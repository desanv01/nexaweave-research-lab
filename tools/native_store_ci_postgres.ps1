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
$fixtureRoot = [IO.Path]::GetFullPath((Join-Path $runnerTemp 'nexaweave-native-store-pg'))
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
    $pidFile = Join-Path $fixtureData 'postmaster.pid'
    if (Test-Path -LiteralPath $pidFile) {
        & $pgControl -D $fixtureData -w -t 60 -m fast stop
        if ($LASTEXITCODE -ne 0) {
            # Fixed vocabulary only. Raw log lines, paths and PIDs never appear in
            # the diagnostic, and the original failed stop remains nonzero.
            $dataState = if (Test-Path -LiteralPath $fixtureData) { 'present' } else { 'absent' }
            $pidFileState = if (Test-Path -LiteralPath $pidFile) { 'present' } else { 'absent' }

            # A PID lookup is an observation, never verified identity or proof of death.
            $pidLookup = 'unavailable'
            if ($pidFileState -eq 'present') {
                try {
                    $pidStream = [IO.File]::Open($pidFile, [IO.FileMode]::Open,
                        [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
                    try {
                        # Cap the first-line read; a PID is at most 10 decimal digits.
                        $pidBuffer = [byte[]]::new(32)
                        $pidRead = 0
                        while ($pidRead -lt $pidBuffer.Length) {
                            $pidChunk = $pidStream.Read($pidBuffer, $pidRead, $pidBuffer.Length - $pidRead)
                            if ($pidChunk -le 0) { break }
                            $pidRead += $pidChunk
                        }
                    } finally {
                        $pidStream.Dispose()
                    }
                    $pidText = [Text.Encoding]::ASCII.GetString($pidBuffer, 0, $pidRead)
                    $pidLine = ($pidText -split "`n")[0].Trim()
                    $recordedPid = 0L
                    if (($pidRead -lt $pidBuffer.Length -or $pidText.Contains("`n")) -and
                        [long]::TryParse($pidLine, [ref]$recordedPid) -and $recordedPid -gt 0) {
                        if (Get-Process -Id $recordedPid -ErrorAction SilentlyContinue) {
                            $pidLookup = 'visible'
                        } else {
                            $pidLookup = 'not_visible'
                        }
                    } else {
                        $pidLookup = 'invalid'
                    }
                } catch {
                    $pidLookup = 'unreadable'
                }
            }

            # Recognized PostgreSQL 17 lifecycle records. Matching is anchored at
            # the message start after the fixture's default log prefix.
            $lifecycleRules = @(
                @{ Pattern = '^starting PostgreSQL '; Category = 'starting' },
                @{ Pattern = '^database system was shut down at '; Category = 'shutdown' },
                @{ Pattern = '^database system is shut down$'; Category = 'shutdown' },
                @{ Pattern = '^database system was interrupted; last known up at '; Category = 'interrupted' },
                @{ Pattern = '^database system was not properly shut down; automatic recovery in progress'; Category = 'recovery' },
                @{ Pattern = '^database system is ready to accept connections$'; Category = 'ready' },
                @{ Pattern = '^received fast shutdown request$'; Category = 'shutdown-requested' },
                @{ Pattern = '^received immediate shutdown request$'; Category = 'shutdown-requested' },
                @{ Pattern = '^shutting down(?: |$)'; Category = 'shutting-down' },
                @{ Pattern = '^all server processes terminated; reinitializing$'; Category = 'reinitializing' },
                @{ Pattern = '^server process \(PID [0-9]+\) was terminated by signal [0-9]+'; Category = 'terminated' },
                @{ Pattern = '^terminating any other active server processes$'; Category = 'shutting-down' },
                @{ Pattern = '^aborting any active transactions$'; Category = 'shutting-down' }
            )
            $lifecycleLooking = '^(?:database system|starting PostgreSQL|received (?:fast|immediate) shutdown request|shutting down|all server processes terminated|server process \(PID [0-9]+\) was terminated by signal|terminating any other active server processes|aborting any active transactions)'
            $lifecycle = 'unknown'
            $panicObserved = $false
            $serverLog = Join-Path $fixtureRoot 'postgres.log'
            if (Test-Path -LiteralPath $serverLog) {
                try {
                    $logStream = [IO.File]::Open($serverLog, [IO.FileMode]::Open,
                        [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
                    try {
                        $logLength = $logStream.Length
                        $logCount = [int][Math]::Min(65536L, $logLength)
                        $tail = ''
                        if ($logCount -gt 0) {
                            $tailStart = $logLength - $logCount
                            $logStream.Seek($tailStart, [IO.SeekOrigin]::Begin) | Out-Null
                            $logBuffer = [byte[]]::new($logCount)
                            $logRead = 0
                            while ($logRead -lt $logCount) {
                                $logChunk = $logStream.Read($logBuffer, $logRead, $logCount - $logRead)
                                if ($logChunk -le 0) { break }
                                $logRead += $logChunk
                            }
                            $tail = [Text.Encoding]::UTF8.GetString($logBuffer, 0, $logRead)
                            # A leading line at the window edge is truncated evidence
                            # for the oldest fact, never for the latest one.
                            if ($tailStart -gt 0) {
                                $firstBreak = $tail.IndexOf("`n")
                                if ($firstBreak -lt 0) { $tail = '' } else { $tail = $tail.Substring($firstBreak + 1) }
                            }
                        }
                        $lastBreak = $tail.LastIndexOf("`n")
                        if ($lastBreak -lt 0) {
                            $completeLines = @()
                            $trailing = $tail
                        } else {
                            $completeText = $tail.Substring(0, $lastBreak + 1)
                            $trailing = $tail.Substring($lastBreak + 1)
                            $completeLines = @($completeText -split "`n" | ForEach-Object { $_.TrimEnd("`r") })
                        }
                        foreach ($line in $completeLines) {
                            $parsed = [regex]::Match($line,
                                '^(?:[^\]]*\] )?(LOG|PANIC|FATAL|ERROR|WARNING|NOTICE|INFO|DEBUG[1-5]):\s{1,2}(.*)$')
                            if (-not $parsed.Success) { continue }
                            $severity = $parsed.Groups[1].Value
                            $message = $parsed.Groups[2].Value.TrimEnd()
                            if ($severity -eq 'PANIC') {
                                $panicObserved = $true
                                continue
                            }
                            if ($severity -ne 'LOG') { continue }
                            $matched = @()
                            foreach ($rule in $lifecycleRules) {
                                if ($message -match $rule.Pattern) { $matched += $rule.Category }
                            }
                            if ($matched.Count -eq 1) {
                                $lifecycle = $matched[0]
                            } elseif ($matched.Count -gt 1) {
                                $lifecycle = 'unknown'
                            } elseif ($message -match $lifecycleLooking) {
                                $lifecycle = 'unknown'
                            }
                        }
                        # A trailing record without its newline is incomplete evidence.
                        if ($trailing -match '(?:\] |^)(?:LOG|PANIC):\s{1,2}') {
                            $lifecycle = 'unknown'
                        }
                    } finally {
                        $logStream.Dispose()
                    }
                } catch {
                    $lifecycle = 'unknown'
                }
            }
            [Console]::Error.WriteLine('NEXAWEAVE_PG_STOP_STATE data_dir=' + $dataState +
                ' pid_file=' + $pidFileState + ' pid_lookup=' + $pidLookup +
                ' lifecycle=' + $lifecycle + ' observation=' +
                $(if ($panicObserved) { 'panic' } else { 'none' }))
            throw 'Owned fixture stop failed'
        }
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
