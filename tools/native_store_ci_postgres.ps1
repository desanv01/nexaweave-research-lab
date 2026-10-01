param([ValidateSet('start', 'stop')][string]$Action)

# GitHub's Windows image supplies PostgreSQL binaries. Use a fresh job-local
# cluster, never its preconfigured service or data directory:
# https://github.com/actions/runner-images/blob/main/images/windows/Windows2025-Readme.md
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or
    -not $env:RUNNER_TEMP -or -not $env:PGBIN) {
    throw 'This fixture helper requires a hosted Windows Actions job'
}
$runnerTemp = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\')
$fixtureRoot = [IO.Path]::GetFullPath((Join-Path $runnerTemp 'mirofish-native-store-pg'))
if (-not $fixtureRoot.StartsWith($runnerTemp + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Fixture directory escapes runner temporary directory'
}
$fixtureData = Join-Path $fixtureRoot 'data'
$pgControl = Join-Path $env:PGBIN 'pg_ctl.exe'
$pgVersion = & (Join-Path $env:PGBIN 'postgres.exe') --version
if ($LASTEXITCODE -ne 0 -or $pgVersion -notmatch 'PostgreSQL\) 17\.') {
    throw 'Expected hosted PostgreSQL 17 binaries'
}
if ($Action -eq 'stop') {
    if (Test-Path -LiteralPath (Join-Path $fixtureData 'postmaster.pid')) {
        & $pgControl -D $fixtureData -w -t 60 -m fast stop
        if ($LASTEXITCODE -ne 0) { throw 'Owned fixture stop failed' }
    }
    exit 0
}
if (-not $env:PROJECT_STORE_TEST_PASSWORD -or (Test-Path -LiteralPath $fixtureRoot)) {
    throw 'Disposable fixture requires a password and a fresh directory'
}
New-Item -ItemType Directory -Path $fixtureRoot | Out-Null
$passwordFile = Join-Path $fixtureRoot 'password.txt'
[IO.File]::WriteAllText($passwordFile, $env:PROJECT_STORE_TEST_PASSWORD + "`n",
                       [Text.UTF8Encoding]::new($false))
try {
    & (Join-Path $env:PGBIN 'initdb.exe') -D $fixtureData -U mirofish_fixture `
        --pwfile=$passwordFile --auth=scram-sha-256 --encoding=UTF8 --locale=C
    if ($LASTEXITCODE -ne 0) { throw 'Disposable fixture initialization failed' }
} finally {
    Remove-Item -LiteralPath $passwordFile -ErrorAction SilentlyContinue
}
& $pgControl -D $fixtureData -l (Join-Path $fixtureRoot 'postgres.log') -w -t 60 `
    -o '-h 127.0.0.1 -p 15432' start
if ($LASTEXITCODE -ne 0) { throw 'Disposable fixture start failed' }
$previousPassword = [Environment]::GetEnvironmentVariable('PGPASSWORD', 'Process')
try {
    $env:PGPASSWORD = $env:PROJECT_STORE_TEST_PASSWORD
    & (Join-Path $env:PGBIN 'createdb.exe') -h 127.0.0.1 -p 15432 -U mirofish_fixture `
        --maintenance-db=postgres mirofish_operations_test
    if ($LASTEXITCODE -ne 0) { throw 'Disposable fixture database creation failed' }
} finally {
    [Environment]::SetEnvironmentVariable('PGPASSWORD', $previousPassword, 'Process')
}
