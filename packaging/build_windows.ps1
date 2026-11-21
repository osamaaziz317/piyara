[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$OutputDirectory = "build\windows"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "Build this Windows package on Windows; cross-compilation is not supported."
}

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Build command failed with exit code $LASTEXITCODE. See the build output."
    }
}

function Write-Utf8Json {
    param([string]$Destination, [object[]]$Lines)
    $Text = ($Lines -join "`n") + "`n"
    $null = ConvertFrom-Json -InputObject $Text  # Refuse non-JSON command output.
    [IO.File]::WriteAllText($Destination, $Text, [Text.UTF8Encoding]::new($false))
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputRoot = [IO.Path]::GetFullPath($OutputDirectory)
} else {
    $OutputRoot = [IO.Path]::GetFullPath((Join-Path $ProjectRoot $OutputDirectory))
}
$null = New-Item -ItemType Directory -Path $OutputRoot -Force
$RunName = "run-" + [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ") + "-" + [Guid]::NewGuid().ToString("N")
$RunDirectory = Join-Path $OutputRoot $RunName
$null = New-Item -ItemType Directory -Path $RunDirectory
$VenvDirectory = Join-Path $RunDirectory "venv"
$VenvPython = Join-Path $VenvDirectory "Scripts\python.exe"
$StageDirectory = Join-Path $RunDirectory "source"
$DistDirectory = Join-Path $RunDirectory "dist"
$WorkDirectory = Join-Path $RunDirectory "pyinstaller-work"
$Receipt = Join-Path $RunDirectory "preflight.json"
$FrozenReceipt = Join-Path $RunDirectory "frozen-runtime.json"
$OldInstallerCache = [Environment]::GetEnvironmentVariable("PYINSTALLER_CONFIG_DIR", "Process")

try {
    Invoke-Checked $Python @("-B", "-E", "-s", "-m", "venv", $VenvDirectory)
    Invoke-Checked $VenvPython @("-B", "-E", "-s", "-m", "pip", "install", "--disable-pip-version-check", "--require-virtualenv", "-r", (Join-Path $ProjectRoot "requirements-build.txt"))

    $Preflight = & $VenvPython -B -E -s (Join-Path $ProjectRoot "tools\check_release.py") --root $ProjectRoot --runtime --windows --toolchain
    if ($LASTEXITCODE -ne 0) { throw "Source/runtime/toolchain preflight failed." }
    Write-Utf8Json $Receipt $Preflight

    # This command always runs synthetic application tests, packaging safety
    # tests, and the native Tk UI smoke test before staging reviewed code.
    Invoke-Checked $VenvPython @("-B", "-E", "-s", (Join-Path $ProjectRoot "packaging\release_support.py"), "stage", "--root", $ProjectRoot, "--destination", $StageDirectory, "--receipt", $Receipt)

    [Environment]::SetEnvironmentVariable("PYINSTALLER_CONFIG_DIR", (Join-Path $RunDirectory "pyinstaller-cache"), "Process")
    Push-Location $StageDirectory
    try {
        Invoke-Checked $VenvPython @("-B", "-I", "-m", "PyInstaller", "--clean", "--distpath", $DistDirectory, "--workpath", $WorkDirectory, (Join-Path $StageDirectory "packaging\BIFMA_TRADERS.spec"))
    } finally {
        Pop-Location
    }

    $BundleDirectory = Join-Path $DistDirectory "BIFMA_TRADERS"
    # The native Windows path uses every mandatory frozen gate used by Wine,
    # including the real workflow from a relocated path containing spaces.
    Invoke-Checked $VenvPython @("-B", "-E", "-s", (Join-Path $ProjectRoot "packaging\frozen_checks.py"), "--bundle", $BundleDirectory, "--run", $RunDirectory, "--schema", (Join-Path $ProjectRoot "docs\legacy_schema.json"), "--output", $FrozenReceipt)

    Invoke-Checked $VenvPython @("-B", "-E", "-s", (Join-Path $ProjectRoot "packaging\release_support.py"), "finish", "--bundle", $BundleDirectory, "--staged", $StageDirectory, "--destination", (Join-Path $RunDirectory "release"), "--frozen-report", $FrozenReceipt)
    Write-Host "Review artifacts are in $RunDirectory\release. Windows acceptance and production sign-off remain pending."
} finally {
    [Environment]::SetEnvironmentVariable("PYINSTALLER_CONFIG_DIR", $OldInstallerCache, "Process")
}
