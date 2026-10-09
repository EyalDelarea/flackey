# Fetch the helper programs a packaged Windows Flackey carries inside itself: ffmpeg and ffprobe (built
# from source by build_ffmpeg.sh, slimmed to what Flackey uses) and fpcalc (acoustid/chromaprint v1.6.1).
# The Windows twin of fetch_helpers.sh. Each download is pinned by SHA-256 and checked before it is
# unpacked. Result:
#   packaging/build/bin/{ffmpeg,ffprobe,fpcalc}.exe   executables Flackey-windows.spec bundles at bin/
#   packaging/build/bin/licenses/                     the licence texts that travel with them
#   packaging/build/webview2/MicrosoftEdgeWebview2Setup.exe   for Flackey.iss (the installer, not the app)
# Downloads and the ffmpeg build are cached in packaging/build/helpers so a rebuild repeats neither.
#
# The ffmpeg build needs MSYS2 (https://www.msys2.org), at C:\msys64 or wherever MSYS2_ROOT points, with
#   pacman -S --needed make diffutils mingw-w64-x86_64-gcc mingw-w64-x86_64-nasm mingw-w64-x86_64-pkgconf mingw-w64-x86_64-zlib
#
# Written for Windows PowerShell 5.1 as well as PowerShell 7, and kept pure ASCII: 5.1 reads a script
# without a BOM as the ANSI code page, where a stray UTF-8 dash turns into a quote character.
#   powershell -ExecutionPolicy Bypass -File packaging\fetch_helpers_windows.ps1
$ErrorActionPreference = 'Stop'
# Invoke-WebRequest's progress bar makes a 30 MB download in 5.1 take minutes; curl.exe is used for the
# downloads anyway, but Expand-Archive draws the same bar.
$ProgressPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$Cache = Join-Path $Root 'packaging\build\helpers'
$Bin = Join-Path $Root 'packaging\build\bin'
$Licenses = Join-Path $Bin 'licenses'
$Chromaprint = 'https://github.com/acoustid/chromaprint/releases/download/v1.6.1'
$FpcalcDir = 'chromaprint-fpcalc-1.6.1-windows-x86_64'

New-Item -ItemType Directory -Force -Path $Cache, $Licenses | Out-Null

# A native program's non-zero exit is not an error to PowerShell 5.1, whatever ErrorActionPreference says.
function Assert-ExitCode([string]$What) {
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

# Fetch NAME URL SHA256 -- download into the cache unless already there, then verify. A file that fails
# the check is deleted so the next run fetches it again instead of trusting a partial download.
# curl.exe, spelled out: in 5.1 plain `curl` is an alias for Invoke-WebRequest. It ships with Windows 10
# 1803 and later, and it retries where Invoke-WebRequest (in 5.1) cannot.
function Get-Pinned([string]$Name, [string]$Url, [string]$Sha256) {
    $file = Join-Path $Cache $Name
    if (-not (Test-Path -LiteralPath $file)) {
        Write-Host "    fetching $Name"
        & curl.exe -fsSL --retry 3 -o "$file.part" $Url
        Assert-ExitCode "downloading $Name"
        Move-Item -Force -LiteralPath "$file.part" -Destination $file
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256) {
        Remove-Item -Force -LiteralPath $file
        throw "checksum mismatch for $Name (deleted; run again)"
    }
    return $file
}

$fpcalcZip = Get-Pinned "$FpcalcDir.zip" "$Chromaprint/$FpcalcDir.zip" '735d6182b38e9f364b84ce6f4ccd682c75e2851de89735711d6b762d12b92a4e'

# build_ffmpeg.sh under MSYS2's MINGW64 shell, the same script the Mac and Linux builds run. A login
# shell (-l) for MINGW64's PATH; CHERE_INVOKING so it starts in the repository rather than in $HOME.
$Msys2 = if ($env:MSYS2_ROOT) { $env:MSYS2_ROOT } else { 'C:\msys64' }
$MsysBash = Join-Path $Msys2 'usr\bin\bash.exe'
if (-not (Test-Path -LiteralPath $MsysBash)) {
    throw "MSYS2 not found at $Msys2 (set MSYS2_ROOT): it builds ffmpeg, see the top of this script"
}
$env:MSYSTEM = 'MINGW64'
$env:CHERE_INVOKING = '1'
Push-Location $Root
try {
    & $MsysBash -lc './packaging/build_ffmpeg.sh packaging/build/bin'
    Assert-ExitCode 'building ffmpeg'
} finally { Pop-Location }

$unpacked = Join-Path $Cache $FpcalcDir
if (Test-Path -LiteralPath $unpacked) { Remove-Item -Recurse -Force -LiteralPath $unpacked }
Expand-Archive -LiteralPath $fpcalcZip -DestinationPath $Cache -Force
Copy-Item -Force -LiteralPath (Join-Path $unpacked 'fpcalc.exe') -Destination (Join-Path $Bin 'fpcalc.exe')

$readme = @'
ffmpeg.exe and ffprobe.exe: FFmpeg built from its unmodified release source, LGPL v2.1 or later. ffmpeg.README
names the release, where its source is and how it was configured; ffmpeg.LICENSE is the licence.
fpcalc.exe: Chromaprint 1.6.1 from https://github.com/acoustid/chromaprint, LGPL v2.1 or later.
'@
[IO.File]::WriteAllText((Join-Path $Licenses 'README.txt'), ($readme -replace "`r`n", "`n") + "`n",
    (New-Object Text.UTF8Encoding $false))

# Microsoft's WebView2 Evergreen Bootstrapper, for Flackey.iss to run on a PC without the runtime (a
# minority of Windows 10 machines; Windows 11 always has it). Not in bin/: the installer carries it, the
# app does not. Not pinned by SHA-256 either, because Microsoft reissues the file behind this link;
# instead its Authenticode signature must be valid and Microsoft's. Fetched fresh every build so the
# installer never carries a stale one.
$WebView2Dir = Join-Path $Root 'packaging\build\webview2'
$WebView2 = Join-Path $WebView2Dir 'MicrosoftEdgeWebview2Setup.exe'
New-Item -ItemType Directory -Force -Path $WebView2Dir | Out-Null
Write-Host '    fetching MicrosoftEdgeWebview2Setup.exe'
& curl.exe -fsSL --retry 3 -o "$WebView2.part" 'https://go.microsoft.com/fwlink/p/?LinkId=2124703'
Assert-ExitCode 'downloading the WebView2 bootstrapper'
Move-Item -Force -LiteralPath "$WebView2.part" -Destination $WebView2
$signature = Get-AuthenticodeSignature -LiteralPath $WebView2
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|, )O=Microsoft Corporation(,|$)') {
    Remove-Item -Force -LiteralPath $WebView2
    throw "the WebView2 bootstrapper is not validly signed by Microsoft ($($signature.Status): $($signature.SignerCertificate.Subject))"
}
Write-Host "    WebView2 bootstrapper signed by $($signature.SignerCertificate.Subject)"

# Each one has to run on this machine, or the bundle would carry three files nobody can execute. On a
# Mac or Linux box (where this script can be dry-run under pwsh) they cannot, which is the point.
foreach ($tool in 'ffmpeg', 'ffprobe', 'fpcalc') {
    $exe = Join-Path $Bin "$tool.exe"
    # No 2>&1: under 5.1 with ErrorActionPreference Stop, any stderr line from a native program becomes
    # a terminating error. All three print their version on stdout. Captured whole before taking the
    # first line, because Select-Object -First stops the pipeline and leaves LASTEXITCODE meaningless.
    $out = & $exe -version
    Assert-ExitCode "$tool -version"
    Write-Host "    $(@($out)[0])"
}
