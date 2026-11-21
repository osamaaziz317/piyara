# Windows one-folder bundle. Build only from the reviewed isolated staging tree.
from pathlib import Path
import os
import sys

if sys.platform != "win32":
    raise RuntimeError("Windows executables must be built on Windows.")

recipe = Path(SPECPATH).resolve()
root = recipe.parent
sys.path.insert(0, str(recipe))
from release_support import guard_analysis, verify_staged_source

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
runtime_roots = [Path(sys.prefix), Path(sys.base_prefix), Path(workpath)]
if os.environ.get("WINDIR"):
    runtime_roots.append(Path(os.environ["WINDIR"]))
guard_analysis(native, root, runtime_roots=runtime_roots)
guard_analysis(history, root, runtime_roots=runtime_roots)

native_exe = EXE(
    PYZ(native.pure), native.scripts, [], exclude_binaries=True,
    name="BIFMA_TRADERS", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
    version=str(recipe / "native-version.txt"), uac_admin=False,
    contents_directory="_internal",
)
history_exe = EXE(
    PYZ(history.pure), history.scripts, [], exclude_binaries=True,
    name="BIFMA_HISTORY", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=True, disable_windowed_traceback=False,
    version=str(recipe / "history-version.txt"), uac_admin=False,
    contents_directory="_internal",
)
COLLECT(
    native_exe, history_exe, native.binaries, history.binaries,
    native.datas, history.datas, strip=False, upx=False,
    name="BIFMA_TRADERS",
)
