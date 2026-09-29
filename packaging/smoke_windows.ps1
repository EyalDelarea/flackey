# smoke_windows.ps1 [PATH\TO\Flackey.exe] -- launch the built Windows app the way a double-click would,
# against a scratch data folder, and check that it serves its health endpoint as a Windows build with the
# helpers found and, when the build was given Telegram keys, with Telegram configured -- and that it is
# still running once its window has had time to come up. Prints the log tail on failure. The Windows twin
# of smoke.sh; used by CI and by hand:
#   powershell -ExecutionPolicy Bypass -File packaging\smoke_windows.ps1
# Written for Windows PowerShell 5.1 as well as PowerShell 7, and kept pure ASCII.
param(
    [string]$Exe = (Join-Path (Split-Path -Parent $PSScriptRoot) 'packaging\build\dist\Flackey\Flackey.exe')
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Exe = (Resolve-Path -LiteralPath $Exe).Path
$Port = if ($env:SMOKE_PORT) { [int]$env:SMOKE_PORT } else { 8797 }
$Health = "http://127.0.0.1:$Port/api/health"
$S = Join-Path ([IO.Path]::GetTempPath()) ("flackey-smoke-" + [guid]::NewGuid().ToString('N'))
$Data = Join-Path $S 'data'
$Log = Join-Path $Data 'flackey.log'
New-Item -ItemType Directory -Force -Path $Data, (Join-Path $S 'lib') | Out-Null

function Get-Health {
    # -UseBasicParsing: 5.1 otherwise wants Internet Explorer's engine, which Server Core does not have.
    try { return Invoke-RestMethod -Uri $Health -TimeoutSec 2 -UseBasicParsing } catch { return $null }
}

function Show-LogTail {
    if (Test-Path -LiteralPath $Log) { Get-Content -LiteralPath $Log -Tail 40 | ForEach-Object { Write-Host $_ } }
    else { Write-Host "(no log at $Log)" }
}

if (Get-Health) {
    throw "smoke port $Port is already in use; set SMOKE_PORT to a free port"
}

# Start-Process in 5.1 has no -Environment, so the child inherits these from this process. The same three
# smoke.sh passes to `open --env`: a throwaway data folder (settings, database and log), a throwaway
# library, and a port nothing else is on. The scratch folder is also the working directory, so a
# developer's repo .env (pydantic-settings reads it from the cwd) cannot leak into the run.
$env:DATA_DIR = $Data
$env:LIBRARY_ROOT = Join-Path $S 'lib'
$env:WEB_PORT = "$Port"

$proc = $null
$failed = $true
try {
    $proc = Start-Process -FilePath $Exe -WorkingDirectory $S -PassThru
    # Touching the handle now keeps it open: without that, a process that has already exited reports an
    # empty ExitCode, which is exactly the case the messages below want to print.
    $null = $proc.Handle

    $h = $null
    for ($i = 0; $i -lt 45; $i++) {
        $h = Get-Health
        if ($h) { break }
        if ($proc.HasExited) { break }
        Start-Sleep -Seconds 2
    }
    if (-not $h) {
        if ($proc.HasExited) { Write-Host "the app exited with code $($proc.ExitCode) before answering on port $Port" }
        else { Write-Host "the app never answered on port $Port" }
        Show-LogTail
        exit 1
    }

    # The server starts before the window does (desktop.run_in_window waits for it, then creates the
    # pywebview window). A build missing Python.Runtime.dll or the WebView2 loader answers the health check
    # and then dies on the next line, so the process has to still be alive a while after it answered.
    Start-Sleep -Seconds 10
    if ($proc.HasExited) {
        Write-Host "smoke: the app answered, then exited with code $($proc.ExitCode) (the window failed to open?)"
        Show-LogTail
        exit 1
    }
    # Informational, never fatal: alive does not prove the window is a WebView2 one. pywebview's Edge
    # backend starts msedgewebview2.exe children; none at all hints at a fallback renderer or no window.
    $webviews = @(Get-CimInstance Win32_Process -Filter "Name = 'msedgewebview2.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ParentProcessId -eq $proc.Id }).Count
    Write-Host "smoke: $webviews WebView2 process(es) started by the app"

    $wantKeys = [bool]($env:FLACKEY_TELEGRAM_API_ID -and $env:FLACKEY_TELEGRAM_API_HASH)
    $problems = @()
    if ($h.ok -ne $true) { $problems += 'ok is not true' }
    if ($h.platform -ne 'windows') { $problems += "platform is '$($h.platform)', expected 'windows'" }
    if ($h.lossless.fpcalc -ne $true) { $problems += 'fpcalc not found inside the app' }
    if ($h.telegram_configured -ne $wantKeys) {
        $problems += "telegram_configured is $($h.telegram_configured), expected $wantKeys"
    }
    if ($problems.Count -gt 0) {
        Write-Host ('smoke: ' + ($problems -join '; '))
        exit 1
    }

    if (-not (Test-Path -LiteralPath $Log)) {
        Write-Host "smoke: no log at $Log (launch.py configures logging before anything else)"
        exit 1
    }
    if (Select-String -LiteralPath $Log -Pattern 'traceback' -Quiet) {
        Write-Host 'smoke: traceback in the log'
        Show-LogTail
        exit 1
    }

    Write-Host ("smoke: ok, version $($h.version), platform $($h.platform), " +
        "telegram configured: $($h.telegram_configured), fpcalc: True")
    $failed = $false
} finally {
    # /T takes the whole tree: the server, the WebView2 processes and anything the app spawned. Then any
    # Flackey.exe from this build that escaped the tree (a relaunch would be a new tree), so a CI runner
    # or a developer's machine is not left with a hidden server on the smoke port.
    if ($proc -and -not $proc.HasExited) {
        & taskkill.exe /PID $proc.Id /T /F | Out-Null
    }
    Get-Process -Name 'Flackey' -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $Exe } |
        ForEach-Object { & taskkill.exe /PID $_.Id /T /F | Out-Null }
    Start-Sleep -Seconds 2
    # Files can stay locked for a moment after the kill; a leftover temp folder is not worth failing over.
    Remove-Item -Recurse -Force -LiteralPath $S -ErrorAction SilentlyContinue
    if ($failed) { Write-Host "smoke: FAILED ($Exe)" }
}
# Explicit, because the last native command above is taskkill, whose exit code (128 when the process had
# already gone) GitHub's pwsh wrapper would otherwise report as this script's.
exit 0
