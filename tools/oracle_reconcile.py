#!/usr/bin/env python3
"""Run a private, isolated Oracle verification in explicit, inspectable phases.

This script never pulls an image, changes an archive, publishes a port, imports
application routines, or prints database records/passwords. Docker access is an
explicit prerequisite. The caller owns the private work directory it creates.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import uuid

from oracle_compare import ComparisonError, compare_archive, identifier, read_json_lines


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "container-registry.oracle.com/database/free:latest"
LABEL = "bifma.oracle-reconciliation.run_id"
OWNER = "BT_RECON"
INNER = "/tmp/bifma_oracle_verification"
ERROR_CODES = re.compile(r"\b(?:ORA|IMP|SP2|LRM|TNS)-\d+\b")


class RunError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def private_write(path, content):
    path = Path(path)
    with path.open("w", encoding="utf-8") as file:
        os.chmod(path, 0o600)
        file.write(content)


def save_state(work, state):
    atomic_json(work / "state.json", state)


def atomic_json(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(json.dumps(value, indent=2) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_state(work):
    work = Path(work).resolve(strict=True)
    if work.is_symlink() or work.stat().st_uid != os.getuid() or work.stat().st_mode & 0o077:
        raise RunError("The run directory must be private and owned by the current user")
    state = json.loads((work / "state.json").read_text())
    if state["work"] != str(work) or not re.fullmatch(r"[0-9a-f]{32}", state["run_id"]):
        raise RunError("The run directory provenance is invalid")
    if state["container"] != "bifma-oracle-verify-" + state["run_id"][:12]:
        raise RunError("The owned container name is invalid")
    return work, state


def command(work, args, *, stdin=None, log=None, allow_failure=False):
    """Capture everything privately; never place secret input in argv/errors."""
    result = subprocess.run(args, input=stdin, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, check=False)
    if log:
        path = work / log
        with path.open("wb") as file:
            os.chmod(path, 0o600)
            file.write(result.stdout)
    if result.returncode and not allow_failure:
        codes = sorted(set(ERROR_CODES.findall(result.stdout.decode("utf-8", errors="replace"))))
        raise RunError(f"Private subprocess failed with status {result.returncode}; error codes: {codes}")
    return result


def docker(work, args, **kwargs):
    return command(work, ["docker", *args], **kwargs)


def owned_container(work, state, *, allow_missing=False, recover_id=False):
    result = docker(work, ["inspect", state["container"]], allow_failure=True)
    if result.returncode:
        # Permission/daemon/connection failures are not evidence of absence.
        diagnostic = result.stdout.decode("utf-8", errors="replace").strip()
        absent_messages = ("Error: No such object: " + state["container"],
                           "Error response from daemon: No such container: " + state["container"])
        # Docker prints an empty JSON array before its not-found diagnostic.
        diagnostic = diagnostic.removeprefix("[]\n").strip()
        if allow_missing and diagnostic.casefold() in {message.casefold() for message in absent_messages}:
            return None
        raise RunError("Unable to verify the owned container's identity")
    info = json.loads(result.stdout)[0]
    if ((state.get("container_id") and info["Id"] != state["container_id"]) or
            (not state.get("container_id") and not recover_id) or
            info["Config"].get("Labels", {}).get(LABEL) != state["run_id"]):
        raise RunError("Container provenance does not match this verification run")
    if (info["HostConfig"]["NetworkMode"] != "none" or info["HostConfig"].get("PortBindings") or
            info["HostConfig"]["Memory"] != 3 * 1024**3 or
            info["HostConfig"]["MemorySwap"] != 3 * 1024**3 or
            info["HostConfig"]["NanoCpus"] != 1_000_000_000 or
            info["HostConfig"]["ShmSize"] != 1024**3):
        raise RunError("The verification container isolation settings changed")
    binds = [mount for mount in info["Mounts"] if mount["Type"] == "bind"]
    if len(binds) != 1 or binds[0]["Source"] != state["source"] or binds[0]["RW"]:
        raise RunError("The verification container bind mounts changed")
    if not state.get("container_id") and recover_id:
        state["container_id"] = info["Id"]
        save_state(work, state)
    return info


def check_network(work, state):
    """Verify only a local dummy NIC exists, with no peer or default route."""
    owned_container(work, state)
    result = docker(work, ["exec", state["container"], "ip", "-j", "-d", "link", "show"])
    links = json.loads(result.stdout)
    if {link["ifname"] for link in links} != {"lo", "bt_dummy"}:
        raise RunError("Unexpected interface in the isolated verification namespace")
    dummy = next(link for link in links if link["ifname"] == "bt_dummy")
    if dummy.get("linkinfo", {}).get("info_kind") != "dummy":
        raise RunError("The isolated interface is not a dummy device")
    routes = docker(work, ["exec", state["container"], "ip", "-j", "route", "show"])
    if any(route.get("dst") == "default" for route in json.loads(routes.stdout)):
        raise RunError("Unexpected external route in the verification namespace")
    route = docker(work, ["exec", state["container"], "ip", "route", "get", "1.1.1.1"], allow_failure=True)
    if route.returncode == 0:
        raise RunError("An external route unexpectedly exists")
    return {"interfaces": ["lo", "bt_dummy"], "dummy_has_peer": False,
            "default_route": False, "external_route_available": False}


def schema_for(state):
    schema = json.loads(Path(state["schema"]).read_text(encoding="utf-8"))
    if sha256(state["source"]) != state["source_sha256"] or schema["source"]["sha256"] != state["source_sha256"]:
        raise RunError("Source fingerprint changed")
    if sha256(state["archive"]) != state["archive_sha256"]:
        raise RunError("Archive fingerprint changed")
    if sha256(state["schema"]) != state["schema_sha256"]:
        raise RunError("Source schema fingerprint changed")
    for table in schema["tables"]:
        identifier(table["name"])
        for column in table["columns"]:
            identifier(column["name"])
    return schema


def prepare(args):
    source = args.source.resolve(strict=True)
    archive = args.archive.resolve(strict=True)
    schema_path = args.schema.resolve(strict=True)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    source_digest = sha256(source)
    if source_digest != schema["source"]["sha256"]:
        raise RunError("Source fingerprint differs from the reviewed schema")
    work = Path(tempfile.mkdtemp(prefix="bifma-oracle-", dir="/tmp"))
    os.chmod(work, 0o700)
    run_id = uuid.uuid4().hex
    credentials = {"database": "Ax8" + secrets.token_hex(20),
                   "schema": "Ax8" + secrets.token_hex(20)}
    private_write(work / "credentials.json", json.dumps(credentials))
    private_write(work / "oracle.env", "ORACLE_PWD=" + credentials["database"] + "\n")
    state = {"run_id": run_id, "container": "bifma-oracle-verify-" + run_id[:12],
             "work": str(work), "source": str(source), "archive": str(archive),
             "schema": str(schema_path), "source_sha256": source_digest,
             "archive_sha256": sha256(archive), "schema_sha256": sha256(schema_path),
             "image_requested": args.image, "prepared_at": utc_now(), "phase": "prepared"}
    save_state(work, state)
    return {"work": str(work), "phase": "prepared", "source_sha256": source_digest,
            "source_tables": len(schema["tables"])}


def start(work, state):
    if state["phase"] != "prepared":
        raise RunError("Start requires a fresh prepared run; do not start a duplicate")
    schema_for(state)
    result = docker(work, ["image", "inspect", state["image_requested"]])
    image = json.loads(result.stdout)[0]
    image_id = image["Id"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise RunError("Unexpected local image identifier")
    name = state["container"]
    result = docker(work, ["run", "--detach", "--name", name, "--hostname", name,
                          "--add-host", name + ":127.0.0.1", "--network", "none",
                          "--cap-add", "NET_ADMIN",
                          "--memory", "3g", "--memory-swap", "3g", "--cpus", "1",
                          "--shm-size", "1g", "--env-file", str(work / "oracle.env"),
                          "--label", LABEL + "=" + state["run_id"], "--mount",
                          "type=bind,src=" + state["source"] + ",dst=/recovery/source.dmp,readonly",
                          image_id, "/bin/bash", "-c",
                          'while [ ! -f /tmp/bifma-network-ready ]; do sleep 0.1; done; exec "$ORACLE_BASE/$RUN_FILE"'], log="start.log")
    container_id = result.stdout.decode().strip()
    if not re.fullmatch(r"[0-9a-f]{64}", container_id):
        raise RunError("Docker did not return a valid owned container identifier")
    state.update(container_id=container_id, image_id=image_id,
                 image_digests=image.get("RepoDigests", []), started_at=utc_now(), phase="starting")
    save_state(work, state)
    owned_container(work, state)
    # Oracle's IPC layer requires a non-loopback IPv4 device even for a
    # standalone database. The dummy has no peer, bridge, host NIC, or route out.
    for args in (["link", "add", "bt_dummy", "type", "dummy"],
                 ["address", "add", "192.0.2.1/32", "dev", "bt_dummy"],
                 ["link", "set", "bt_dummy", "up"]):
        docker(work, ["exec", "--user", "root", name, "ip", *args], log="network-setup.log")
    state["network_verification"] = check_network(work, state)
    save_state(work, state)
    docker(work, ["exec", "--user", "oracle", name, "touch", "/tmp/bifma-network-ready"])
    return {"container": name, "phase": "starting", "image_id": image_id,
            "network": "none", "published_ports": 0, "memory_gib": 3, "cpus": 1}


def status(work, state):
    info = owned_container(work, state)
    result = docker(work, ["logs", state["container"]], log="startup.log", allow_failure=True)
    logs = result.stdout.decode("utf-8", errors="replace")
    return {"phase": state["phase"], "container_status": info["State"]["Status"],
            "health": info["State"].get("Health", {}).get("Status", "not-configured"),
            "oom_killed": info["State"].get("OOMKilled", False),
            "ready_message": "DATABASE IS READY TO USE!" in logs,
            "error_codes": sorted(set(ERROR_CODES.findall(logs)))}


SQL_SETTINGS = """WHENEVER SQLERROR EXIT FAILURE ROLLBACK
WHENEVER OSERROR EXIT FAILURE ROLLBACK
SET ECHO OFF FEEDBACK OFF HEADING OFF PAGESIZE 0 VERIFY OFF
SET LINESIZE 32767 LONG 1000000 LONGCHUNKSIZE 32767 TRIMSPOOL ON TRIMOUT ON TAB OFF
SET WRAP ON
COLUMN payload FORMAT A32767
ALTER SESSION SET CONTAINER=FREEPDB1;
ALTER SESSION SET NLS_NUMERIC_CHARACTERS='.,';
ALTER SESSION SET NLS_CALENDAR='GREGORIAN';
ALTER SESSION SET NLS_DATE_LANGUAGE='AMERICAN';
"""


def sqlplus(work, state, script, log):
    owned_container(work, state)
    payload = (SQL_SETTINGS + script).encode("utf-8")
    result = docker(work, ["exec", "--interactive", "--user", "oracle", "--env", "ORACLE_SID=FREE",
                          "--env", "NLS_LANG=AMERICAN_AMERICA.AL32UTF8", state["container"],
                          "bash", "-c", 'exec "$ORACLE_HOME/bin/sqlplus" -s "/ as sysdba"'],
                    stdin=payload, log=log)
    # SQLPlus command errors do not trigger WHENEVER SQLERROR, and Oracle SQL
    # error numbers can wrap modulo the shell's exit-status range. JSON rows
    # start with '['; only standalone diagnostic lines count as errors here.
    codes = sorted(set(re.findall(r"(?m)^[ \t]*((?:ORA|SP2|PLS|TNS)-\d+):", result.stdout.decode("utf-8", errors="replace"))))
    if codes:
        raise RunError("SQLPlus reported diagnostic errors: " + ", ".join(codes))
    return result


def configure(work, state):
    if state["phase"] != "starting":
        raise RunError("Configure requires a newly started database")
    owned_container(work, state)
    check_network(work, state)
    result = docker(work, ["exec", "--user", "oracle", state["container"], "bash", "-c",
                          'test -x "$ORACLE_HOME/bin/imp" && test -x "$ORACLE_HOME/bin/sqlplus"'],
                    log="tools.log", allow_failure=True)
    if result.returncode:
        raise RunError("The selected Oracle image does not contain both original imp and SQLPlus")
    credentials = json.loads((work / "credentials.json").read_text())
    script = (f'CREATE USER {OWNER} IDENTIFIED BY "{credentials["schema"]}" '
              'DEFAULT TABLESPACE USERS TEMPORARY TABLESPACE TEMP QUOTA 512M ON USERS;\n'
              f"GRANT CREATE SESSION, CREATE TABLE TO {OWNER};\nEXIT SUCCESS;\n")
    sqlplus(work, state, script, "configure.log")
    state.update(phase="configured", configured_at=utc_now())
    save_state(work, state)
    return {"phase": state["phase"], "original_import_available": True}


def import_tables(work, state):
    if state["phase"] != "configured":
        raise RunError("Import requires a freshly configured target schema")
    owned_container(work, state)
    check_network(work, state)
    schema = schema_for(state)
    credentials = json.loads((work / "credentials.json").read_text())
    table_list = ",".join(table["name"] for table in schema["tables"])
    parameters = (f'USERID=system/{credentials["database"]}@//127.0.0.1:1521/FREEPDB1\n'
                  "FILE=/recovery/source.dmp\nFROMUSER=BT\nTOUSER=BT_RECON\n"
                  f"TABLES=({table_list})\nROWS=Y\nINDEXES=N\nCONSTRAINTS=N\n"
                  "GRANTS=N\nIGNORE=N\nSTATISTICS=NONE\nCOMPILE=N\n"
                  "LOG=/tmp/bifma-oracle-import.log\n")
    docker(work, ["exec", "--interactive", "--user", "oracle", state["container"],
                  "bash", "-c", "umask 077; cat > /tmp/bifma-oracle-import.par"],
           stdin=parameters.encode("ascii"))
    state.update(phase="import-running", import_started_at=utc_now())
    save_state(work, state)
    result = docker(work, ["exec", "--user", "oracle", "--env",
                          "NLS_LANG=AMERICAN_AMERICA.AL32UTF8", state["container"],
                          "bash", "-c", 'umask 077; exec "$ORACLE_HOME/bin/imp" parfile=/tmp/bifma-oracle-import.par'],
                    log="import.log", allow_failure=True)
    text = result.stdout.decode("utf-8", errors="replace")
    codes = sorted(set(ERROR_CODES.findall(text)))
    # Warnings are preserved for review; table counts and exact values still
    # have to pass the independent comparison before verification is accepted.
    imported_counts = re.findall(r'\.\s*\.\s+importing table\s+"([A-Z][A-Z0-9_]*)"\s+(\d+) rows imported', text)
    state.update(phase="imported" if result.returncode in (0, 1) else "import-failed",
                 import_exit_code=result.returncode, import_error_codes=codes,
                 import_table_counts={name: int(count) for name, count in imported_counts},
                 import_completed_at=utc_now())
    save_state(work, state)
    return {"phase": state["phase"], "exit_code": result.returncode, "error_codes": codes,
            "tables_reported": len(imported_counts),
            "rows_reported": sum(int(count) for _, count in imported_counts)}


def number_sql(expression):
    return f"TO_CHAR({expression},'TM9','NLS_NUMERIC_CHARACTERS=''.,''')"


def json_query(expressions, source):
    return "SELECT JSON_ARRAY(" + ",".join(expressions) + \
           " NULL ON NULL RETURNING CLOB STRICT) payload FROM " + source + ";\n"


def export_script(schema):
    script = "SET TRANSACTION READ ONLY;\n"
    script += f"SPOOL {INNER}/columns.jsonl\n"
    script += ("SELECT JSON_ARRAY(TABLE_NAME,COLUMN_NAME,DATA_TYPE,DATA_LENGTH,"
               "DATA_PRECISION,DATA_SCALE,NULLABLE,COLUMN_ID NULL ON NULL RETURNING CLOB STRICT) payload "
               f"FROM ALL_TAB_COLUMNS WHERE OWNER='{OWNER}' ORDER BY TABLE_NAME,COLUMN_ID;\nSPOOL OFF\n")
    script += f"SPOOL {INNER}/counts.jsonl\n"
    for table in schema["tables"]:
        script += json_query(["'" + table["name"] + "'", number_sql("COUNT(*)")],
                             OWNER + "." + identifier(table["name"]))
    script += "SPOOL OFF\n"
    script += f"SPOOL {INNER}/environment.jsonl\n"
    script += json_query(["'version'", "BANNER_FULL"], "V$VERSION")
    script += ("SELECT JSON_ARRAY(PARAMETER,VALUE NULL ON NULL RETURNING CLOB STRICT) payload "
               "FROM NLS_DATABASE_PARAMETERS WHERE PARAMETER IN ('NLS_CHARACTERSET','NLS_NCHAR_CHARACTERSET');\n")
    script += ("SELECT JSON_ARRAY('object_type',OBJECT_TYPE,COUNT(*) NULL ON NULL RETURNING CLOB STRICT) payload "
               f"FROM ALL_OBJECTS WHERE OWNER='{OWNER}' GROUP BY OBJECT_TYPE;\nSPOOL OFF\n")
    # This independent, synthetic transport probe checks precision, escaping,
    # non-ASCII, CHAR padding, DATE seconds, and NULL positions in SQLPlus JSON.
    script += f"SPOOL {INNER}/probe.jsonl\n"
    script += json_query([number_sql("9007199254740993"),
                          number_sql("12345678901234567890123456789012345678"),
                          number_sql("-0.00000000000000000000000000000012345678"),
                          "CAST('x' AS CHAR(4))", "'a'||CHR(10)||CHR(34)||CHR(92)||'z'",
                          "UNISTR('\\0627\\0631\\062F\\0648')", "NULL",
                          "TO_CHAR(TO_DATE('2026-09-27 14:32:19','YYYY-MM-DD HH24:MI:SS'),'YYYY-MM-DD\"T\"HH24:MI:SS')"],
                         "DUAL")
    script += "SPOOL OFF\n"
    for table in schema["tables"]:
        expressions = []
        bound = 2
        for column in table["columns"]:
            name, kind = identifier(column["name"]), column["oracle_type"]
            if kind.startswith("NUMBER"):
                expressions.append(number_sql(name))
                bound += 260
            elif kind == "DATE":
                expressions.append(f"TO_CHAR({name},'YYYY-MM-DD\"T\"HH24:MI:SS')")
                bound += 24
            else:
                match = re.fullmatch(r"(?:VARCHAR2|CHAR)\((\d+)\)", kind)
                if not match:
                    raise RunError("Unsupported column type for Oracle JSON verification")
                expressions.append(name)
                bound += int(match[1]) * 6 + 4
        if bound >= 32000:
            raise RunError("An exported row could exceed the SQLPlus line limit")
        script += f"SPOOL {INNER}/{table['name']}.jsonl\n"
        script += json_query(expressions, OWNER + "." + identifier(table["name"]))
        script += "SPOOL OFF\n"
    return script + "COMMIT;\nEXIT SUCCESS;\n"


def validate_probe(path):
    rows = list(read_json_lines(path))
    expected = [["9007199254740993", "12345678901234567890123456789012345678",
                 "-.00000000000000000000000000000012345678", "x   ", 'a\n"\\z',
                 "اردو", None, "2026-09-27T14:32:19"]]
    # TM9's optional leading zero does not change exact numerical meaning.
    from oracle_compare import canonical_number
    if len(rows) != 1 or len(rows[0]) != 8:
        raise RunError("The Oracle JSON transport probe has an invalid shape")
    for index in range(3):
        if canonical_number(rows[0][index]) != canonical_number(expected[0][index]):
            raise RunError("The Oracle JSON transport probe lost numeric precision")
    if rows[0][3:] != expected[0][3:]:
        raise RunError("The Oracle JSON transport probe lost text/date/NULL fidelity")


def export_tables(work, state):
    if state["phase"] != "imported":
        raise RunError("Export requires a completed import")
    owned_container(work, state)
    check_network(work, state)
    schema = schema_for(state)
    docker(work, ["exec", "--user", "oracle", state["container"], "bash", "-c",
                  "umask 077; mkdir " + INNER])
    script = export_script(schema)
    private_write(work / "export.sql", SQL_SETTINGS + script)
    sqlplus(work, state, script, "export.log")
    docker(work, ["cp", state["container"] + ":" + INNER, str(work / "exports")], log="copy.log")
    for file in (work / "exports").iterdir():
        os.chmod(file, 0o600)
    validate_probe(work / "exports" / "probe.jsonl")
    environment = list(read_json_lines(work / "exports" / "environment.jsonl"))
    object_types = {row[1]: row[2] for row in environment if row[0] == "object_type"}
    if object_types != {"TABLE": len(schema["tables"])}:
        raise RunError("The imported schema contains unexpected objects")
    state.update(phase="exported", exported_at=utc_now(), environment=environment,
                 transport_probe_passed=True)
    save_state(work, state)
    return {"phase": state["phase"], "transport_probe_passed": True,
            "tables": object_types["TABLE"]}


def compare(work, state, report_path):
    if state["phase"] != "exported":
        raise RunError("Comparison requires a complete export and transport probe")
    schema = schema_for(state)
    report = compare_archive(schema, state["archive"], work / "exports")
    report.update(verified_at=utc_now(), run_id=state["run_id"], image_id=state["image_id"],
                  image_digests=state["image_digests"], archive_sha256=state["archive_sha256"],
                  schema_sha256=state["schema_sha256"], environment=state["environment"],
                  import_exit_code=state["import_exit_code"],
                  import_error_codes=state["import_error_codes"],
                  transport_probe_passed=state["transport_probe_passed"],
                  isolation={"network": "none", "published_ports": 0, "memory_gib": 3, "cpus": 1,
                             "source_bind_readonly": True,
                             "network_verification": state["network_verification"],
                             "additional_capability": "NET_ADMIN for disconnected dummy NIC only"},
                  source_unchanged=True, archive_unchanged=True,
                  cleanup_complete=False,
                  verifier_sha256={name: sha256(ROOT / "tools" / name)
                                   for name in ("oracle_compare.py", "oracle_reconcile.py")},
                  scope="Table values only; original application workflows and report parity are not certified")
    report_path = report_path.absolute()
    with report_path.open("x", encoding="utf-8") as file:
        file.write(json.dumps(report, indent=2) + "\n")
    state.update(phase="compared", compared_at=utc_now(), report=str(report_path),
                 all_fields_match=report["all_fields_match"])
    save_state(work, state)
    return {"phase": state["phase"], "all_fields_match": report["all_fields_match"],
            "tables": report["table_count"], "rows": report["total_rows"],
            "schema_errors": report["schema_errors"], "report": str(report_path)}


def cleanup(work, state):
    # Recover a crash between docker run and state persistence, or continue
    # cleanup after docker rm succeeded but private-file/report cleanup did not.
    existing = owned_container(work, state, allow_missing=True, recover_id=True)
    if existing is not None:
        docker(work, ["rm", "--force", "--volumes", state["container"]], log="cleanup.log")
    for name in ("credentials.json", "oracle.env"):
        (work / name).unlink(missing_ok=True)
    # Oracle export files include confidential records and credential columns.
    # Keep only aggregate provenance; SQLPlus/import logs can contain records.
    for name in ("exports", "report_exports", "aggregate_exports"):
        if (work / name).is_dir():
            shutil.rmtree(work / name)
    (work / "deferred.sql").unlink(missing_ok=True)
    (work / "aggregate_cases.json").unlink(missing_ok=True)
    for file in work.glob("*.log"):
        file.unlink()
    for key in ("report", "report_parity_report", "aggregate_report"):
        if not state.get(key):
            continue
        report_path = Path(state[key])
        report = json.loads(report_path.read_text())
        if report.get("run_id") != state["run_id"]:
            raise RunError("Report provenance changed; refusing to overwrite")
        report.update(cleanup_complete=True, cleaned_at=utc_now(),
                      source_unchanged_at_cleanup=sha256(state["source"]) == state["source_sha256"],
                      archive_unchanged_at_cleanup=sha256(state["archive"]) == state["archive_sha256"])
        atomic_json(report_path, report)
    state.update(phase="cleaned", cleaned_at=utc_now())
    save_state(work, state)
    return {"phase": "cleaned", "owned_container_removed": bool(state.get("container_id")),
            "private_credentials_removed": True, "private_exports_removed": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "start", "status", "configure", "import", "export", "compare", "cleanup"))
    parser.add_argument("--work", type=Path)
    parser.add_argument("--source", type=Path, default=ROOT / "BT-270926.EXE")
    parser.add_argument("--archive", type=Path, default=ROOT / "data/legacy_recovered.sqlite3")
    parser.add_argument("--schema", type=Path, default=ROOT / "docs/legacy_schema.json")
    parser.add_argument("--image", choices=(IMAGE, IMAGE + "-lite"), default=IMAGE)
    parser.add_argument("--report", type=Path, default=ROOT / "docs/oracle_reconciliation.json")
    args = parser.parse_args()
    try:
        if args.phase == "prepare":
            result = prepare(args)
        else:
            if args.work is None:
                parser.error("--work is required for this phase")
            work, state = load_state(args.work)
            if args.phase == "compare":
                result = compare(work, state, args.report)
            else:
                operation = {"start": start, "status": status, "configure": configure,
                             "import": import_tables, "export": export_tables,
                             "cleanup": cleanup}[args.phase]
                result = operation(work, state)
        print(json.dumps(result, sort_keys=True))
        if args.phase == "compare" and not result["all_fields_match"]:
            return 2
    except Exception as error:
        # Unexpected exceptions are intentionally generic; records and private
        # SQL content never appear in a traceback or a user-facing error.
        if isinstance(error, (RunError, ComparisonError)):
            print(json.dumps({"error": str(error)}), file=sys.stderr)
        else:
            print(json.dumps({"error": "Private verification operation failed", "type": type(error).__name__}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
