"""Linux packaging guards use synthetic ELF markers, never real business data."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
from subprocess import CompletedProcess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import linux_release as linux
import native_entry
import release_support as shared
import frozen_checks as gates


ROOT = Path(__file__).resolve().parents[1]
ELF = b"\x7fELF\x02\x01" + b"\0" * 12 + b"\x3e\0" + b"synthetic marker, not an executable"


def workflow_fixture(version):
    return {"schema_version": 1, "scenario": "native-workflow-v2", "application_version": version,
            "frozen": True, "passed": True, "temporary_data_removed": True,
            "steps": [{"step": step, "passed": True} for step in gates.WORKFLOW_STEPS],
            "effects": dict(gates.WORKFLOW_EFFECTS)}

class LinuxPackagingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        definition = json.loads((ROOT / "packaging/source_manifest.json").read_text())
        for field in ("application_files", "build_files", "review_files"):
            for relative in definition[field]:
                destination = self.source / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, destination)

    def runtime(self):
        return {"application_version": shared.application_version(ROOT)[0], "python_version": "3.10.12",
                "sqlite_version": "3.37.2", "tk_version": "8.6.12", "platform": "linux", "machine": "x86_64", "bits": 64,
                "libc": {"name": "glibc", "version": "2.35"}, "distribution": {"ID": "ubuntu", "VERSION_ID": "22.04"}}

    def receipt(self):
        return {"source": shared.source_inventory(self.source), "runtime": self.runtime(),
                "checks": [{"check": name, "passed": True, "tests_run": 1} for name in sorted(linux.REQUIRED_CHECKS)],
                "verification_inputs": [], "toolchain_wheels": []}

    def bundle(self):
        bundle = self.root / "bundle"
        (bundle / "_internal").mkdir(parents=True)
        for name in ("BIFMA_TRADERS", "BIFMA_HISTORY", "_internal/_tkinter.cpython-310-x86_64-linux-gnu.so",
                     "_internal/_sqlite3.cpython-310-x86_64-linux-gnu.so", "_internal/libsqlite3.so.0.8.6",
                     "_internal/libtk8.6.so", "_internal/libtcl8.6.so"):
            (bundle / name).write_bytes(ELF)
            (bundle / name).chmod(0o755)
        (bundle / "_internal/libsqlite3.so.0").symlink_to("libsqlite3.so.0.8.6")
        with zipfile.ZipFile(bundle / "_internal/base_library.zip", "x") as archive:
            archive.writestr("stdlib_example.pyc", "synthetic bytecode")
        return bundle

    def frozen(self):
        value = {"history_runtime": self.runtime(), "native_runtime": self.runtime(),
                 "checks": [{"check": name, "passed": True} for name in sorted(linux.FROZEN_CHECKS)],
                 "workflow": workflow_fixture(self.runtime()["application_version"])}
        path = self.root / "frozen.json"
        path.write_text(json.dumps(value))
        return path

    def test_allowlist_syntax_contains_linux_recipe_and_diagnostics_without_private_data(self):
        inventory = shared.source_inventory(self.source)
        names = {row["path"] for row in inventory["files"]}
        self.assertIn("packaging/BIFMA_TRADERS_LINUX.spec", names)
        self.assertIn("packaging/native_entry.py", names)
        self.assertFalse(any(name.endswith((".db", ".sqlite3", ".EXE")) for name in names))

    def test_verification_receipt_covers_recovery_helpers_imported_by_synthetic_tests(self):
        for relative in ('tests/test_synthetic.py', 'tools/smoke_native_ui.py', 'tools/recovery_oracle.py',
                         'tools/stage_recovery.py', 'docs/legacy_schema.json'):
            path = self.source / relative
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_text('# synthetic verification input\n')
        before = {row['path']: row['sha256'] for row in linux.verification_inventory(self.source)}
        self.assertIn('tools/recovery_oracle.py', before)
        self.assertIn('tools/stage_recovery.py', before)
        (self.source / 'tools/stage_recovery.py').write_text('# changed verification dependency\n')
        after = {row['path']: row['sha256'] for row in linux.verification_inventory(self.source)}
        self.assertNotEqual(before['tools/stage_recovery.py'], after['tools/stage_recovery.py'])

    def test_linux_runtime_refuses_wrong_platform_architecture_and_libc(self):
        for platform_name, machine in (("win32", "x86_64"), ("linux", "aarch64")):
            with self.subTest(platform=platform_name, machine=machine), patch.object(linux.sys, "platform", platform_name), patch.object(linux.platform, "machine", return_value=machine):
                with self.assertRaisesRegex(shared.ReleaseError, "native Linux x86_64"):
                    linux.runtime_report()
        with patch.object(linux.sys, "platform", "linux"), patch.object(linux.platform, "machine", return_value="x86_64"), patch.object(shared, "runtime_report", return_value=self.runtime()), patch.object(linux.platform, "libc_ver", return_value=("musl", "1.2")):
            with self.assertRaisesRegex(shared.ReleaseError, "glibc-based"):
                linux.runtime_report()

    def test_safe_internal_file_symlink_is_recorded_without_weakening_windows_guard(self):
        bundle = self.bundle()
        inventory = linux.audit_bundle(bundle)
        alias = next(row for row in inventory if row["path"] == "_internal/libsqlite3.so.0")
        original = next(row for row in inventory if row["path"] == "_internal/libsqlite3.so.0.8.6")
        self.assertEqual((alias["kind"], alias["target"], alias["sha256"]), ("symlink", "libsqlite3.so.0.8.6", original["sha256"]))
        with self.assertRaises(shared.ReleaseError):
            shared.audit_bundle(bundle)

    def test_wrong_elf_architecture_and_missing_executable_mode_are_rejected(self):
        bundle = self.bundle()
        executable = bundle / "BIFMA_TRADERS"
        executable.write_bytes(b"MZ synthetic Windows fixture")
        with self.assertRaisesRegex(shared.ReleaseError, "x86_64 ELF"):
            linux.audit_bundle(bundle)
        executable.write_bytes(ELF[:18] + b"\xb7\0" + ELF[20:])
        with self.assertRaises(shared.ReleaseError):
            linux.audit_bundle(bundle)
        executable.write_bytes(ELF)
        executable.chmod(0o644)
        with self.assertRaises(shared.ReleaseError):
            linux.audit_bundle(bundle)

    def test_broken_cyclic_absolute_and_escaping_symlinks_are_refused(self):
        bundle = self.bundle()
        outside = self.root / "outside.so"
        outside.write_bytes(ELF)
        link = bundle / "_internal/bad.so"
        for target in (str(outside), "../../outside.so", "missing.so", "bad.so"):
            with self.subTest(target=target):
                link.symlink_to(target)
                try:
                    with self.assertRaises(shared.ReleaseError):
                        linux.audit_bundle(bundle)
                finally:
                    link.unlink()
        (bundle / "_internal/dir-link").symlink_to(".", target_is_directory=True)
        with self.assertRaisesRegex(shared.ReleaseError, "directory symlinks"):
            linux.audit_bundle(bundle)

    def test_database_credential_logs_and_renamed_payloads_cannot_enter_bundle(self):
        bundle = self.bundle()
        for name, payload in (("accounts.db", b"synthetic"), ("credentials.json", b"{}"), ("recovery.log", b"synthetic"),
                              ("plain.dat", b"SQLite format 3\0synthetic"), ("plain.dat", b"\x00EXPORT:V10.02.01\n")):
            path = bundle / "_internal" / name
            path.write_bytes(payload)
            with self.subTest(name=name), self.assertRaises(shared.ReleaseError):
                linux.audit_bundle(bundle)
            path.unlink()

    def test_nested_runtime_zip_and_required_linux_libraries_are_checked(self):
        bundle = self.bundle()
        with zipfile.ZipFile(bundle / "_internal/base_library.zip", "w") as archive:
            archive.writestr("credentials.json", "{}")
        with self.assertRaises(shared.ReleaseError):
            linux.audit_bundle(bundle)
        (bundle / "_internal/base_library.zip").unlink()
        (bundle / "_internal/libtk8.6.so").unlink()
        with self.assertRaisesRegex(shared.ReleaseError, "runtime library is missing"):
            linux.audit_bundle(bundle)

    def test_analysis_symlink_records_are_checked_without_resolving_as_source(self):
        analysis = SimpleNamespace(scripts=[], pure=[], binaries=[("libdemo.so", "libdemo.so.1", "SYMLINK")], datas=[])
        linux.guard_linux_analysis(analysis, self.source)
        for target in ("/etc/passwd", "../../credentials.json", "data/archive.db"):
            analysis.binaries = [("libdemo.so", target, "SYMLINK")]
            with self.subTest(target=target), self.assertRaises(shared.ReleaseError):
                linux.guard_linux_analysis(analysis, self.source)
        analysis.binaries = []
        (self.source / "private.txt").write_text("synthetic")
        analysis.datas = [("runtime.txt", str(self.source / "private.txt"), "DATA")]
        with self.assertRaisesRegex(shared.ReleaseError, "non-application project data"):
            linux.guard_linux_analysis(analysis, self.source)

    def test_glibc_baseline_is_numeric_and_comes_from_readelf(self):
        bundle = self.bundle()
        output = "Name: GLIBC_2.9\nName: GLIBC_2.35\nName: GLIBC_2.2.5\n"
        with patch.object(linux.shutil, "which", return_value="/usr/bin/readelf"), patch.object(linux.subprocess, "run", return_value=CompletedProcess([], 0, output)) as run:
            baseline = linux.binary_baseline(bundle)
        self.assertEqual(baseline["glibc_symbol_requirement"], "2.35")
        self.assertGreater(baseline["elf_files_checked"], 1)
        self.assertTrue(all(call.args[0][:2] == ["readelf", "--version-info"] for call in run.call_args_list))
        with patch.object(linux.shutil, "which", return_value=None), self.assertRaises(shared.ReleaseError):
            linux.binary_baseline(bundle)

    def test_stdlib_dump_module_name_is_code_while_dump_payload_stays_forbidden(self):
        runtime = self.root / "runtime"
        runtime.mkdir()
        code = runtime / "dump.py"
        code.write_text("# Synthetic stdlib stand-in\n")
        analysis = SimpleNamespace(scripts=[], pure=[("sqlite3.dump", str(code), "PYMODULE")], binaries=[], datas=[])
        shared.guard_analysis(analysis, self.source, runtime_roots=[runtime])
        analysis.pure = []
        analysis.datas = [("sqlite3.dump", str(code), "DATA")]
        with self.assertRaisesRegex(shared.ReleaseError, "forbidden"):
            shared.guard_analysis(analysis, self.source, runtime_roots=[runtime])
        analysis.datas = []
        for name in ("package/../outside", "package..outside", "", ".leading", "trailing.", "package/child"):
            analysis.pure = [(name, str(code), "PYMODULE")]
            with self.subTest(name=name), self.assertRaisesRegex(shared.ReleaseError, "dotted Python names"):
                shared.guard_analysis(analysis, self.source, runtime_roots=[runtime])

    def test_release_tar_preserves_links_modes_and_development_provenance(self):
        stage = shared.stage_source(self.source, self.root / "staged", self.receipt())
        bundle, frozen = self.bundle(), self.frozen()
        with patch.object(linux, "binary_baseline", return_value={"glibc_symbol_requirement": "2.35", "elf_files_checked": 7}):
            result = linux.finish_release(bundle, stage, self.root / "release", frozen)
        archive_path = Path(result["archive"])
        self.assertEqual(shared.digest(archive_path), result["sha256"])
        with tarfile.open(archive_path) as archive:
            alias = archive.getmember("BIFMA_TRADERS/_internal/libsqlite3.so.0")
            executable = archive.getmember("BIFMA_TRADERS/BIFMA_TRADERS")
            self.assertTrue(alias.issym())
            self.assertEqual(alias.linkname, "libsqlite3.so.0.8.6")
            self.assertEqual(executable.mode, 0o755)
            self.assertTrue(all(item.uid == item.gid == 0 and not item.uname and not item.gname for item in archive.getmembers()))
            report = json.load(archive.extractfile("BIFMA_TRADERS/release-manifest.json"))
            self.assertEqual(report["release_channel"], "DEVELOPMENT")
            self.assertFalse(report["production_ready"])
            self.assertEqual(report["target"], "linux-x86_64")
            warning = archive.extractfile("BIFMA_TRADERS/DEVELOPMENT.txt").read().decode()
            self.assertIn("Not production ready", warning)
            self.assertIn("--database PATH", warning)
        with self.assertRaises(FileExistsError):
            linux.finish_release(bundle, stage, self.root / "release", frozen)

    def test_missing_source_or_frozen_checks_prevent_publication(self):
        receipt = self.receipt()
        receipt["checks"] = []
        stage = shared.stage_source(self.source, self.root / "staged", receipt)
        bundle, frozen = self.bundle(), self.frozen()
        with self.assertRaisesRegex(shared.ReleaseError, "release checks are incomplete"):
            linux.finish_release(bundle, stage, self.root / "release", frozen)
        self.assertFalse((self.root / "release").exists())
        shared._write_new_json(self.root / "unused.json", {})
        metadata = json.loads((stage / ".release-stage.json").read_text())
        metadata["checks"] = self.receipt()["checks"]
        (stage / ".release-stage.json").write_text(json.dumps(metadata))
        report = json.loads(frozen.read_text())
        report["checks"] = []
        frozen.write_text(json.dumps(report))
        with self.assertRaisesRegex(shared.ReleaseError, "frozen Linux checks are incomplete"):
            linux.finish_release(bundle, stage, self.root / "release", frozen)

    def test_failed_gate_does_not_create_a_passing_receipt(self):
        with patch.object(linux.subprocess, "run", return_value=CompletedProcess([], 1, "Ran 2 tests\n", "FAILED\n")), self.assertRaisesRegex(shared.ReleaseError, "application-tests"):
            linux.run_checks(self.source, self.root)
        self.assertIn("FAILED", (self.root / "application-tests.log").read_text())

    def test_native_launcher_keeps_normal_arguments_and_has_data_free_runtime_check(self):
        with patch("bifma.native_ui.main", return_value=0) as main:
            self.assertEqual(native_entry.main([]), 0)
            self.assertEqual(native_entry.main(["--database", "/synthetic/path.sqlite3"]), 0)
        self.assertEqual(main.call_args_list[0].args[0], [])
        with patch("runtime_probe.check_runtime", return_value={"runtime": "checked"}), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(native_entry.main(["--check-runtime"]), 0)
        self.assertEqual(json.loads(output.getvalue()), {"runtime": "checked"})


if __name__ == "__main__":
    unittest.main()
