# Linux x86_64 one-folder development bundle, from isolated reviewed sources.
from pathlib import Path
import platform
import struct
import sys

if sys.platform != "linux" or platform.machine().lower() != "x86_64" or struct.calcsize("P") != 8:
    raise RuntimeError("Build the Linux x86_64 package on native Linux x86_64.")

recipe = Path(SPECPATH).resolve()
root = recipe.parent
sys.path.insert(0, str(recipe))
from release_support import verify_staged_source
from linux_release import guard_linux_analysis

verify_staged_source(root)
excluded = ["tools", "tests", "BIFMA_TRADERS_V2", "pip", "setuptools"]
native = Analysis(
    [str(recipe / "native_entry.py")], pathex=[str(root), str(recipe)],
    binaries=[], datas=[], hiddenimports=[], hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=excluded, noarchive=False, optimize=0,
)
history = Analysis(
    [str(recipe / "history_entry.py")], pathex=[str(root), str(recipe)],
    binaries=[], datas=[], hiddenimports=[], hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=excluded, noarchive=False, optimize=0,
)
runtime_roots = [Path(sys.prefix), Path(sys.base_prefix), Path(workpath), Path("/lib"), Path("/lib64")]
guard_linux_analysis(native, root, runtime_roots=runtime_roots)
guard_linux_analysis(history, root, runtime_roots=runtime_roots)
native_exe = EXE(
    PYZ(native.pure), native.scripts, [], exclude_binaries=True,
    name="BIFMA_TRADERS", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=True, contents_directory="_internal",
)
history_exe = EXE(
    PYZ(history.pure), history.scripts, [], exclude_binaries=True,
    name="BIFMA_HISTORY", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=True, contents_directory="_internal",
)
COLLECT(native_exe, history_exe, native.binaries, history.binaries,
        native.datas, history.datas, strip=False, upx=False, name="BIFMA_TRADERS")
