"""Packaging boundaries tested using public code and synthetic temporary files.

Dummy MZ files below are test fixtures, never compiled Windows executables.
No original export or business database is read or copied by these tests.
"""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
from subprocess import CompletedProcess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import history_entry
import release_support as release
import frozen_checks as gates


ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "workspace"
        definition = json.loads((ROOT / "packaging/source_manifest.json").read_text())
        for category in ("application_files", "build_files", "review_files"):
            for relative in definition[category]:
                destination = self.source / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, destination)
        self.definition = definition
        self.stage = self.root / "stage"

    def receipt(self):
        return {"source": release.source_inventory(self.source), "runtime": {
            "application_version": release.application_version(self.source)[0],
            "python_version": "3.13.9", "sqlite_version": "3.50.4", "tk_version": "8.6.15",
            "platform": "win32", "machine": "AMD64", "bits": 64,
        }, "checks": [{"check": name, "passed": True, "tests_run": 1}
                       for name in ("application-tests", "packaging-tests", "native-gui-smoke")]}

    def bundle(self):
        bundle = self.root / "bundle"
        (bundle / "_internal").mkdir(parents=True)
        for name in ("BIFMA_TRADERS.exe", "BIFMA_HISTORY.exe", "_internal/_tkinter.pyd", "_internal/_sqlite3.pyd",
                     "_internal/sqlite3.dll", "_internal/tk86t.dll", "_internal/tcl86t.dll"):
            (bundle / name).write_bytes(b"MZ synthetic packaging fixture; not executable")
        with zipfile.ZipFile(bundle / "_internal/base_library.zip", "x") as archive:
            archive.writestr("stdlib_example.pyc", b"synthetic bytecode fixture")
        return bundle

    def test_real_recipe_manifest_syntax_and_single_version_source(self):
        result = release.source_inventory(self.source)
        self.assertEqual(result["application_version"], release.application_version(ROOT)[0])
        self.assertEqual(result["pyinstaller_required"], "6.22.3")
        self.assertEqual(len(result["files"]), sum(len(self.definition[key]) for key in ("application_files", "build_files", "review_files")))

    def test_staging_copies_only_allowlisted_code_even_when_private_files_are_present(self):
        for relative, content in (("BT-270926.EXE", b"private export fixture"), ("bifma_traders_v2.db", b"SQLite format 3\0fixture"),
                                  ("data/legacy_recovered.sqlite3", b"private archive fixture"), ("backups/current.bak", b"private backup fixture"),
                                  ("credentials.json", b'{"secret":"synthetic"}'), ("recovery.log", b"private log fixture")):
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        receipt = self.receipt()
        release.stage_source(self.source, self.stage, receipt)
        actual = {path.relative_to(self.stage).as_posix() for path in self.stage.rglob("*") if path.is_file()}
        self.assertEqual(actual, set(self.definition["application_files"] + self.definition["build_files"]) |
                         {".release-stage.json", "packaging/native-version.txt", "packaging/history-version.txt"})
        self.assertEqual(release.verify_staged_source(self.stage), receipt)
        for name in ("native-version.txt", "history-version.txt"):
            text = (self.stage / "packaging" / name).read_text()
            compile(text, name, "eval")
            self.assertIn(repr(receipt["source"]["application_version"]), text)

    def test_added_application_module_requires_review_and_manifest_update(self):
        (self.source / "bifma/unreviewed.py").write_text("pass\n")
        with self.assertRaisesRegex(release.ReleaseError, "module set changed"):
            release.source_inventory(self.source)

    def test_data_injection_or_path_traversal_in_manifest_is_rejected(self):
        path = self.source / "packaging/source_manifest.json"
        for name in ("credentials.json", "../external.py", "C:\\outside.py"):
            with self.subTest(name=name):
                definition = json.loads(json.dumps(self.definition))
                definition["application_files"].append(name)
                (self.source / "credentials.json").write_text("{}")
                path.write_text(json.dumps(definition))
                with self.assertRaises(release.ReleaseError):
                    release.source_inventory(self.source)

    def test_stale_receipt_and_existing_staging_destination_are_refused(self):
        receipt = self.receipt()
        with (self.source / "BIFMA_TRADERS_V3.py").open("a") as source:
            source.write("\n# Changed after testing\n")
        with self.assertRaisesRegex(release.ReleaseError, "changed after"):
            release.stage_source(self.source, self.stage, receipt)
        self.assertFalse(self.stage.exists())
        self.stage.mkdir()
        marker = self.stage / "keep.txt"
        marker.write_text("existing content")
        with self.assertRaises(FileExistsError):
            release.stage_source(self.source, self.stage, self.receipt())
        self.assertEqual(marker.read_text(), "existing content")

    def test_staged_extra_data_or_changed_source_prevents_build(self):
        release.stage_source(self.source, self.stage, self.receipt())
        (self.stage / "unexpected.txt").write_text("synthetic unwanted payload")
        with self.assertRaisesRegex(release.ReleaseError, "unreviewed file"):
            release.verify_staged_source(self.stage)
        (self.stage / "unexpected.txt").unlink()
        (self.stage / "BIFMA_TRADERS_V3.py").write_text("pass\n")
        with self.assertRaisesRegex(release.ReleaseError, "differs"):
            release.verify_staged_source(self.stage)

    def test_project_data_cannot_enter_pyinstaller_even_if_renamed(self):
        secret = self.source / "innocent.txt"
        secret.write_text("synthetic private data")
        analysis = SimpleNamespace(scripts=[], pure=[], binaries=[], datas=[("runtime.txt", str(secret), "DATA")])
        with self.assertRaisesRegex(release.ReleaseError, "non-application project data"):
            release.guard_analysis(analysis, self.source)
        analysis.datas = []
        analysis.pure = [("bifma", str(self.root / "external.py"), "PYMODULE")]
        with self.assertRaisesRegex(release.ReleaseError, "outside"):
            release.guard_analysis(analysis, self.source)
        analysis.pure = []
        analysis.datas = [("innocent.txt", str(self.root / "external.txt"), "DATA")]
        with self.assertRaisesRegex(release.ReleaseError, "outside the approved runtime"):
            release.guard_analysis(analysis, self.source)

    def test_generated_windows_version_resources_cannot_drift_from_source(self):
        release.stage_source(self.source, self.stage, self.receipt())
        (self.stage / "packaging/native-version.txt").write_text("VSVersionInfo()\n")
        with self.assertRaisesRegex(release.ReleaseError, "version resource differs"):
            release.verify_staged_source(self.stage)

    def test_bundle_rejects_database_logs_credentials_and_renamed_payloads(self):
        bundle = self.bundle()
        self.assertTrue(release.audit_bundle(bundle))
        for name, data in (("record.db", b"synthetic"), ("credentials.json", b"{}"), ("recovery.log", b"synthetic"),
                           ("settings-wal", b"synthetic"), (".env", b"synthetic"),
                           ("innocent.txt", b"SQLite format 3\0synthetic"), ("innocent.tcl", b"\x03\x03gEXPORT:V10.02.01\n")):
            path = bundle / "_internal" / name
            path.write_bytes(data)
            with self.subTest(name=name), self.assertRaises(release.ReleaseError):
                release.audit_bundle(bundle)
            path.unlink()

    def test_nested_zip_is_checked_and_required_runtime_files_are_present(self):
        bundle = self.bundle()
        for name in ("credentials.json", "../outside", "C:/outside"):
            with zipfile.ZipFile(bundle / "_internal/base_library.zip", "w") as archive:
                archive.writestr(name, "synthetic")
            with self.assertRaises(release.ReleaseError):
                release.audit_bundle(bundle)
        (bundle / "_internal/base_library.zip").unlink()
        (bundle / "_internal/sqlite3.dll").unlink()
        with self.assertRaisesRegex(release.ReleaseError, "runtime file is missing"):
            release.audit_bundle(bundle)

    def test_source_and_bundle_symlinks_do_not_collect_external_files(self):
        path = self.source / "BIFMA_TRADERS_V3.py"
        path.unlink()
        try:
            path.symlink_to(ROOT / "BIFMA_TRADERS_V3.py")
        except OSError:
            self.skipTest("Symlinks are unavailable")
        with self.assertRaisesRegex(release.ReleaseError, "symlinks"):
            release.source_inventory(self.source)
        bundle = self.bundle()
        (bundle / "_internal/link.txt").symlink_to(ROOT / "BIFMA_TRADERS_V3.py")
        with self.assertRaisesRegex(release.ReleaseError, "symlinks"):
            release.audit_bundle(bundle)

    def test_required_test_failure_stops_before_staging_or_archive(self):
        with patch.object(release.subprocess, "run", return_value=CompletedProcess([], 1, "Ran 2 tests\nFAILED\n")), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(release.ReleaseError, "application-tests"):
                release.run_required_checks(self.source, self.root)
        self.assertFalse(self.stage.exists())

    def test_finish_checks_frozen_runtime_and_creates_review_archive_checksum(self):
        receipt = self.receipt()
        release.stage_source(self.source, self.stage, receipt)
        bundle = self.bundle()
        frozen = self.root / "frozen.json"
        bad = dict(receipt["runtime"], sqlite_version="3.36.0")
        frozen.write_text(json.dumps(bad))
        destination = self.root / "release"
        with self.assertRaisesRegex(release.ReleaseError, "does not match"):
            release.finish_release(bundle, self.stage, destination, frozen)
        self.assertFalse(destination.exists())
        frozen.write_text(json.dumps({"native_runtime": receipt["runtime"], "history_runtime": receipt["runtime"],
            "checks": [{"check": name, "passed": True} for name in gates.FROZEN_CHECKS],
            "workflow": {"schema_version": 1, "scenario": "native-workflow-v2",
                "application_version": receipt["runtime"]["application_version"], "frozen": True,
                "passed": True, "temporary_data_removed": True,
                "steps": [{"step": step, "passed": True} for step in gates.WORKFLOW_STEPS],
                "effects": dict(gates.WORKFLOW_EFFECTS)}}))
        result = release.finish_release(bundle, self.stage, destination, frozen)
        artifact = Path(result["archive"])
        self.assertEqual(result["sha256"], hashlib.sha256(artifact.read_bytes()).hexdigest())
        self.assertTrue((destination / (artifact.name + ".sha256")).read_text().startswith(result["sha256"]))
        with zipfile.ZipFile(artifact) as archive:
            metadata = json.loads(archive.read("BIFMA_TRADERS/release-manifest.json"))
            self.assertFalse(metadata["production_ready"])
            self.assertEqual(metadata["target_acceptance"], "pending")
            self.assertTrue(all(name.startswith("BIFMA_TRADERS/") for name in archive.namelist()))
        with self.assertRaises(FileExistsError):
            release.finish_release(bundle, self.stage, destination, frozen)

    def test_historical_launcher_requires_explicit_database_without_loading_archive(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            history_entry.main([])
        self.assertEqual(error.exception.code, 2)
        with patch("bifma.preview_ui.main", return_value=0) as preview:
            self.assertEqual(history_entry.main(["--database", "C:/Synthetic Folder/history.sqlite3"]), 0)
        preview.assert_called_once_with(["--database", str(Path("C:/Synthetic Folder/history.sqlite3"))])

    def test_linux_cannot_be_reported_as_windows_build(self):
        with patch.object(release.sys, "platform", "linux"):
            with self.assertRaisesRegex(release.ReleaseError, "native Windows"):
                release.runtime_report(windows=True)


if __name__ == "__main__":
    unittest.main()
