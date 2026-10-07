param([Parameter(Mandatory)][ValidateSet('start', 'stop')][string]$Action)

$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or
    -not $env:RUNNER_TEMP) { throw 'Requires a hosted Windows Actions job' }
$runnerTemp = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\')
$fixtureRoot = [IO.Path]::GetFullPath((Join-Path $runnerTemp 'nexaweave-native-temporal'))
if (-not $fixtureRoot.StartsWith($runnerTemp + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Fixture directory escapes job temporary directory'
}
$ownerPath = Join-Path $fixtureRoot 'owner.json'
$executable = Join-Path $fixtureRoot 'bin/temporal.exe'
$database = Join-Path $fixtureRoot 'test.db'
if ($Action -eq 'stop') {
    if (-not (Test-Path -LiteralPath $ownerPath)) { exit 0 }
    $owner = Get-Content -LiteralPath $ownerPath -Raw | ConvertFrom-Json
    if ($owner.executable -ne $executable -or $owner.database -ne $database -or
        $owner.pid -isnot [long] -and $owner.pid -isnot [int]) { throw 'Invalid fixture owner' }
    $owned = Get-Process -Id $owner.pid -ErrorAction SilentlyContinue
    if (-not $owned) { exit 0 }
    $details = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $owner.pid)
    # Newer PowerShell versions deserialize ISO JSON timestamps as DateTime.
    # Avoid a culture-dependent string roundtrip that can swap month and day.
    $started = if ($owner.started_at -is [DateTime]) {
        [DateTimeOffset]$owner.started_at
    } else {
        [DateTimeOffset]::Parse($owner.started_at, [Globalization.CultureInfo]::InvariantCulture)
    }
    if ($details.ExecutablePath -ne $executable -or
        -not $details.CommandLine.Contains($database) -or
        [Math]::Abs(($owned.StartTime.ToUniversalTime() - $started.UtcDateTime).TotalSeconds) -gt 5) {
        throw 'Fixture process identity mismatch'
    }
    Stop-Process -Id $owned.Id
    if (-not $owned.WaitForExit(10000)) { throw 'Owned fixture stop timeout' }
    exit 0
}
if (Test-Path -LiteralPath $fixtureRoot) { throw 'Requires a fresh job-local directory' }
New-Item -ItemType Directory -Path $fixtureRoot | Out-Null
$archive = Join-Path $fixtureRoot 'temporal.zip'
Invoke-WebRequest -Uri 'https://github.com/temporalio/cli/releases/download/v1.9.1/temporal_cli_1.9.1_windows_amd64.zip' -OutFile $archive -TimeoutSec 120
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne
    'babb65844835045c91fb98930fe10b81de28db80c8c609180473d2c0b18c7589') {
    throw 'Temporal fixture checksum mismatch'
}
Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $fixtureRoot 'bin')
if (-not (Test-Path -LiteralPath $executable)) { throw 'Fixture executable missing' }
$owned = Start-Process -FilePath $executable -ArgumentList @(
    'server', 'start-dev', '--headless', '--ip', '127.0.0.1', '--port', '17233',
    '--http-port', '17234', '--metrics-port', '17235', '--db-filename', ('"' + $database + '"')) `
    -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $fixtureRoot 'stdout.log') `
    -RedirectStandardError (Join-Path $fixtureRoot 'stderr.log')
@{pid=$owned.Id; executable=$executable; database=$database;
  started_at=$owned.StartTime.ToUniversalTime().ToString('o')} |
    ConvertTo-Json | Set-Content -LiteralPath $ownerPath -Encoding utf8
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    if ($owned.HasExited) { throw 'Fixture exited before readiness' }
    & $executable operator cluster health --address 127.0.0.1:17233 --command-timeout 2s
    if ($LASTEXITCODE -eq 0) { exit 0 }
    Start-Sleep -Seconds 1
}
throw 'Temporal fixture readiness timeout'
