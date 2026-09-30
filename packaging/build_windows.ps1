# Build Flackey for Windows: the PyInstaller app in packaging/build/dist/Flackey and the installer
# packaging/build/Flackey-Setup.exe (+ .sha256). The Windows twin of build_app.sh + build_installer.sh.
# Run from anywhere; paths are resolved from this script's folder:
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
# Needs uv, Node 22 (npm) and Inno Setup 6 (https://jrsoftware.org/isdl.php) on the machine.
#
# The result is unsigned, so Windows SmartScreen warns on first run -- see packaging/README-windows.md
# for what the person receiving it has to do.
#
# Written for Windows PowerShell 5.1 as well as PowerShell 7, and kept pure ASCII (5.1 reads a BOM-less
# script as the ANSI code page).
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$Build = Join-Path $Root 'packaging\build'
Set-Location $Root
New-Item -ItemType Directory -Force -Path $Build | Out-Null

# A native program's non-zero exit is not an error to PowerShell 5.1, whatever ErrorActionPreference says.
function Assert-ExitCode([string]$What) {
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

# Files this build writes for other programs to read: UTF-8 without a BOM and with LF endings. 5.1's
# Set-Content -Encoding UTF8 prepends a BOM (which json.loads refuses) and Out-File writes UTF-16.
function Write-Utf8NoBom([string]$Path, [string]$Text) {
    [IO.File]::WriteAllText($Path, $Text, (New-Object Text.UTF8Encoding $false))
}

# ISCC.exe is not on PATH after a normal Inno Setup install. Looked for in order: an explicit $env:ISCC,
# PATH, the two Program Files folders, a per-user install (winget's default), and finally wherever the
# uninstall registry entry says Inno Setup 6 went.
function Find-Iscc {
    if ($env:ISCC -and (Test-Path -LiteralPath $env:ISCC)) { return $env:ISCC }
    $cmd = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @()
    $bases = @(${env:ProgramFiles(x86)}, $env:ProgramFiles)
    if ($env:LOCALAPPDATA) { $bases += (Join-Path $env:LOCALAPPDATA 'Programs') }
    foreach ($base in $bases) {
        if ($base) { $candidates += (Join-Path $base 'Inno Setup 6\ISCC.exe') }
    }
    foreach ($key in @(
            'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
            'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
            'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1')) {
        $entry = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
        if ($entry -and $entry.InstallLocation) { $candidates += (Join-Path $entry.InstallLocation 'ISCC.exe') }
    }
    foreach ($c in $candidates) { if (Test-Path -LiteralPath $c) { return $c } }
    throw 'Inno Setup 6 (ISCC.exe) not found: install it from https://jrsoftware.org/isdl.php or set $env:ISCC'
}

$Version = [regex]::Match(
    [IO.File]::ReadAllText((Join-Path $Root 'src\flackey\__init__.py')),
    '(?m)^__version__ = "([^"]+)"').Groups[1].Value
if (-not $Version) { throw 'could not read the Flackey version from src/flackey/__init__.py' }
Write-Host "Flackey $Version"

Write-Host '==> web UI'
# `ci`, not `install`: installs exactly the lock file, and never rewrites it on a build machine.
& npm --prefix web ci --silent
Assert-ExitCode 'npm ci'
& npm --prefix web run build
Assert-ExitCode 'npm run build'

Write-Host '==> icon'
# One .ico serves the exe (Flackey-windows.spec), Setup.exe and the uninstall entry (Flackey.iss). Pillow
# is pulled in for this one command only (--no-project), so it never reaches the app's environment or
# the bundle. The source asset is a 1024 square PNG; these are the sizes Explorer asks an .ico for.
$Ico = Join-Path $Build 'Flackey.ico'
# One line with no double quotes: 5.1 does not escape embedded quotes when it builds a command line.
$IcoScript = "import sys; from PIL import Image; " +
    "Image.open(sys.argv[1]).convert('RGBA').save(sys.argv[2], sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])"
& uv run --no-project --with pillow python -c $IcoScript (Join-Path $Root 'src\flackey\assets\app-icon.png') $Ico
Assert-ExitCode 'icon conversion'

Write-Host '==> build defaults'
# The Telegram keys a packaged copy carries. From CI these come from repository secrets; locally, set
# FLACKEY_TELEGRAM_API_ID and FLACKEY_TELEGRAM_API_HASH before running this script, or leave them unset
# to build a copy whose setup screen asks for keys.
$BuildJson = Join-Path $Root 'src\flackey\assets\build.json'
if ($env:FLACKEY_TELEGRAM_API_ID -and $env:FLACKEY_TELEGRAM_API_HASH) {
    Write-Utf8NoBom $BuildJson ('{"telegram_api_id": ' + $env:FLACKEY_TELEGRAM_API_ID +
        ', "telegram_api_hash": "' + $env:FLACKEY_TELEGRAM_API_HASH + '"}' + "`n")
    Write-Host '    Telegram keys: baked in'
} else {
    # A build.json left over from an earlier keyed build would otherwise ship in a copy meant to be keyless.
    if (Test-Path -LiteralPath $BuildJson) { Remove-Item -Force -LiteralPath $BuildJson }
    Write-Host '    Telegram keys: none (setup will ask)'
}

Write-Host '==> helpers'
# ffmpeg, ffprobe and fpcalc ride inside the app: a stock Windows machine has none of them.
& (Join-Path $PSScriptRoot 'fetch_helpers_windows.ps1')

Write-Host '==> bundle'
& uv run --with pyinstaller pyinstaller --noconfirm --clean `
    --distpath (Join-Path $Build 'dist') --workpath (Join-Path $Build 'work') `
    (Join-Path $Root 'packaging\Flackey-windows.spec')
Assert-ExitCode 'pyinstaller'
$Exe = Join-Path $Build 'dist\Flackey\Flackey.exe'
if (-not (Test-Path -LiteralPath $Exe)) { throw "PyInstaller finished but $Exe is missing" }

Write-Host '==> installer'
$Iscc = Find-Iscc
Write-Host "    $Iscc"
$Setup = Join-Path $Build 'Flackey-Setup.exe'
if (Test-Path -LiteralPath $Setup) { Remove-Item -Force -LiteralPath $Setup }
# /Qp: quiet except for progress and errors. The version is passed in rather than read by the .iss so
# there is one place (this script) that decides it.
& $Iscc /Qp "/DAppVersion=$Version" (Join-Path $Root 'packaging\Flackey.iss')
Assert-ExitCode 'ISCC'
if (-not (Test-Path -LiteralPath $Setup)) { throw "ISCC finished but $Setup is missing" }

# The same line `shasum -a 256 Flackey-Setup.exe` prints, so the Mac and Windows checksum files read alike
# and `sha256sum -c` accepts either.
$Sha = (Get-FileHash -Algorithm SHA256 -LiteralPath $Setup).Hash.ToLowerInvariant()
Write-Utf8NoBom "$Setup.sha256" "$Sha  Flackey-Setup.exe`n"
Write-Host "    $Sha  Flackey-Setup.exe"

Write-Host ''
Write-Host "Built $Exe"
Write-Host "Built $Setup"
