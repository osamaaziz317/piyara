"""Synthetic tests for independent Oracle verification (no private data)."""

from argparse import Namespace
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from oracle_compare import (ComparisonError, canonical_number, canonical_row,
                            check_oracle_schema, compare_archive, row_multiset)
from oracle_reconcile import RunError, export_script, validate_probe, cleanup, owned_container, sqlplus, main, LABEL


COLUMNS = [{"name": "ID", "oracle_type": "NUMBER", "nullable": False},
           {"name": "NAME", "oracle_type": "VARCHAR2(20)", "nullable": True},
           {"name": "WHEN_AT", "oracle_type": "DATE", "nullable": True},
           {"name": "FLAG", "oracle_type": "CHAR(1)", "nullable": True}]
SCHEMA = {"source": {"sha256": "a" * 64},
          "tables": [{"name": "SAMPLE", "columns": COLUMNS}]}
ROWS = [["9007199254740993", 'a\n"\\اردو', "2026-09-27T14:32:19", " "],
        ["1.25", None, None, None], ["1.25", None, None, None]]


def jsonl(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


class CanonicalTests(unittest.TestCase):
    def test_exact_decimals_without_context_rounding(self):
        self.assertEqual(canonical_number("1.2345678901234567890123456789012345678E37"),
                         "12345678901234567890123456789012345678")
        self.assertEqual(canonical_number("1.25000"), "1.25")
        self.assertEqual(canonical_number("-0"), "0")
        self.assertEqual(canonical_number("-.125"), "-0.125")
        for value in ("NaN", "Infinity", "1E126", "1E-131", "1_0", " 1", 1.25):
            with self.subTest(value=value), self.assertRaises(ComparisonError):
                canonical_number(value)

    def test_type_null_timestamp_and_text_are_preserved(self):
        self.assertEqual(canonical_row(ROWS[0], COLUMNS), ROWS[0])
        for values in ([None, None, None, None], [1.25, None, None, None],
                       ["1", None, "2026-09-27T14:32:19.001", None],
                       ["1", None, "2026-09-27T14:32:19+00:00", None], ["1"]):
            with self.subTest(values=values), self.assertRaises(ComparisonError):
                canonical_row(values, COLUMNS)

    def test_multiset_is_order_independent_but_retains_duplicates(self):
        actual, digest = row_multiset(ROWS, COLUMNS)
        reversed_rows, reversed_digest = row_multiset(reversed(ROWS), COLUMNS)
        self.assertEqual((actual, digest), (reversed_rows, reversed_digest))
        unique, _ = row_multiset(ROWS[:2], COLUMNS)
        self.assertNotEqual(actual, unique)
        self.assertEqual(sum((actual - unique).values()), 1)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.columns = [["SAMPLE", "ID", "NUMBER", 22, None, None, "N", 1],
                        ["SAMPLE", "NAME", "VARCHAR2", 20, None, None, "Y", 2],
                        ["SAMPLE", "WHEN_AT", "DATE", 7, None, None, "Y", 3],
                        ["SAMPLE", "FLAG", "CHAR", 1, None, None, "Y", 4]]
        jsonl(self.directory / "columns.jsonl", self.columns)
        jsonl(self.directory / "counts.jsonl", [["SAMPLE", "3"]])
        jsonl(self.directory / "SAMPLE.jsonl", list(reversed(ROWS)))
        self.archive = self.directory / "archive.db"
        with sqlite3.connect(self.archive) as connection:
            connection.execute("CREATE TABLE SAMPLE(ID TEXT,NAME TEXT,WHEN_AT TEXT,FLAG TEXT)")
            connection.executemany("INSERT INTO SAMPLE VALUES (?,?,?,?)", ROWS)
            connection.execute("CREATE TABLE _recovery_metadata(key TEXT,value TEXT)")
            connection.execute("INSERT INTO _recovery_metadata VALUES ('report',?)",
                               (json.dumps({"source_sha256": "a" * 64}),))

    def test_all_columns_exactly_match_and_archive_untouched(self):
        before = self.archive.read_bytes()
        result = compare_archive(SCHEMA, self.archive, self.directory)
        self.assertTrue(result["all_fields_match"])
        self.assertEqual(result["total_rows"], 3)
        self.assertEqual(self.archive.read_bytes(), before)

    def test_loss_precision_text_padding_null_or_duplicates_fails(self):
        mutations = [ROWS[:2], [list(ROWS[0]), *ROWS[1:]],
                     [list(ROWS[0]), *ROWS[1:]], [list(ROWS[0]), *ROWS[1:]]]
        mutations[1][0][0] = "9007199254740992"
        mutations[2][0][3] = ""
        mutations[3][0][1] = None
        for changed in mutations:
            with self.subTest(changed=changed):
                jsonl(self.directory / "SAMPLE.jsonl", changed)
                self.assertFalse(compare_archive(SCHEMA, self.archive, self.directory)["all_fields_match"])

    def test_independent_count_mismatch_fails(self):
        jsonl(self.directory / "counts.jsonl", [["SAMPLE", "4"]])
        self.assertFalse(compare_archive(SCHEMA, self.archive, self.directory)["all_fields_match"])

    def test_counts_and_metadata_reject_fractional_boolean_or_coerced_values(self):
        for value in (3.9, True, 3, "03", "3.0", "-1"):
            jsonl(self.directory / "counts.jsonl", [["SAMPLE", value]])
            with self.subTest(value=value), self.assertRaises(ComparisonError):
                compare_archive(SCHEMA, self.archive, self.directory)
        for value in (22.9, True, "22.0"):
            columns = deepcopy(self.columns)
            columns[0][3] = value
            jsonl(self.directory / "columns.jsonl", columns)
            with self.subTest(value=value), self.assertRaises(ComparisonError):
                check_oracle_schema(SCHEMA, self.directory / "columns.jsonl")

    def test_source_fingerprint_mismatch_rejected(self):
        wrong = deepcopy(SCHEMA)
        wrong["source"]["sha256"] = "b" * 64
        with self.assertRaises(ComparisonError):
            compare_archive(wrong, self.archive, self.directory)

    def test_column_order_precision_or_nullability_mismatch_fails(self):
        for index, value in ((1, "DIFFERENT"), (4, 12), (6, "Y"), (7, 2)):
            rows = deepcopy(self.columns)
            rows[0][index] = value
            jsonl(self.directory / "columns.jsonl", rows)
            self.assertTrue(check_oracle_schema(SCHEMA, self.directory / "columns.jsonl"))

    def test_wrapped_or_truncated_json_rejected_without_printing_record(self):
        (self.directory / "SAMPLE.jsonl").write_text('["secret\nrecord"]\n')
        with self.assertRaises(ComparisonError) as caught:
            compare_archive(SCHEMA, self.archive, self.directory)
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("record", str(caught.exception))

    def test_sql_export_uses_explicit_types_nulls_and_snapshot(self):
        script = export_script(SCHEMA)
        self.assertTrue(script.startswith("SET TRANSACTION READ ONLY;"))
        self.assertIn('TO_CHAR("ID",\'TM9\'', script)
        self.assertIn('TO_CHAR("WHEN_AT",\'YYYY-MM-DD"T"HH24:MI:SS\')', script)
        self.assertIn("NULL ON NULL RETURNING CLOB STRICT", script)
        self.assertIn("ALL_TAB_COLUMNS WHERE OWNER='BT_RECON'", script)
        self.assertIn('FROM BT_RECON."SAMPLE"', script)
        self.assertTrue(script.endswith("COMMIT;\nEXIT SUCCESS;\n"))

    def test_transport_probe_precision_padding_escaping_nulls(self):
        row = ["9007199254740993", "12345678901234567890123456789012345678",
               "-0.00000000000000000000000000000012345678", "x   ", 'a\n"\\z',
               "اردو", None, "2026-09-27T14:32:19"]
        path = self.directory / "probe.jsonl"
        jsonl(path, [row])
        validate_probe(path)
        row[3] = "x"
        jsonl(path, [row])
        with self.assertRaises(RunError):
            validate_probe(path)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.state = {"run_id": "a" * 32, "container": "bifma-oracle-verify-" + "a" * 12,
                      "work": str(self.work), "source": "/source.dmp", "phase": "prepared"}
        self.info = {"Id": "b" * 64, "Config": {"Labels": {LABEL: "a" * 32}},
                     "HostConfig": {"NetworkMode": "none", "PortBindings": {},
                                    "Memory": 3 * 1024**3, "MemorySwap": 3 * 1024**3,
                                    "NanoCpus": 1_000_000_000, "ShmSize": 1024**3},
                     "Mounts": [{"Type": "bind", "Source": "/source.dmp", "RW": False}]}
        (self.work / "credentials.json").write_text("synthetic-secret")

    def response(self, output, code=0):
        return subprocess.CompletedProcess([], code, output.encode())

    def test_cleanup_recovers_interrupted_start_before_id_was_saved(self):
        inspected = self.response(json.dumps([self.info]))
        with patch("oracle_reconcile.docker", side_effect=[inspected, self.response("")]) as call:
            cleanup(self.work, self.state)
        self.assertEqual(self.state["container_id"], self.info["Id"])
        self.assertEqual(call.call_args_list[1].args[1][0], "rm")
        self.assertFalse((self.work / "credentials.json").exists())

    def test_cleanup_resumes_after_container_was_already_removed(self):
        self.state["container_id"] = self.info["Id"]
        missing = self.response("[]\nerror: no such object: " + self.state["container"], 1)
        with patch("oracle_reconcile.docker", return_value=missing) as call:
            cleanup(self.work, self.state)
        self.assertEqual(call.call_count, 1)
        self.assertFalse((self.work / "credentials.json").exists())
        self.assertEqual(self.state["phase"], "cleaned")

    def test_cleanup_refuses_foreign_container_or_uncertain_daemon_failure(self):
        foreign = deepcopy(self.info)
        foreign["Config"]["Labels"][LABEL] = "c" * 32
        for response in (self.response(json.dumps([foreign])), self.response("permission denied", 1)):
            with patch("oracle_reconcile.docker", return_value=response) as call:
                with self.assertRaises(RunError):
                    cleanup(self.work, self.state)
                self.assertEqual(call.call_count, 1)
                self.assertTrue((self.work / "credentials.json").exists())

    def test_sqlplus_rejects_zero_exit_diagnostics_but_not_json_data(self):
        with patch("oracle_reconcile.owned_container"), patch("oracle_reconcile.docker") as call:
            call.return_value = self.response("SP2-0734: unknown command\n")
            with self.assertRaises(RunError):
                sqlplus(self.work, self.state, "", "private.log")
            call.return_value = self.response('["SP2-0734: synthetic field"]\n')
            sqlplus(self.work, self.state, "", "private.log")

    def test_cli_returns_failure_for_a_valid_negative_comparison_report(self):
        with patch("sys.argv", ["oracle_reconcile.py", "compare", "--work", str(self.work)]), \
                patch("oracle_reconcile.load_state", return_value=(self.work, self.state)), \
                patch("oracle_reconcile.compare", return_value={"all_fields_match": False}), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 2)


if __name__ == "__main__":
    unittest.main()
