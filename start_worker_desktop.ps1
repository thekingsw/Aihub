# Run this file from a PowerShell window on the logged-in Windows desktop.
# -RestartIdleWorker is only for an explicitly authorized worker restart.
param([switch]$RestartIdleWorker, [switch]$SmokeTest)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$sessionId = (Get-Process -Id $PID).SessionId
if ($sessionId -eq 0) {
    throw 'Worker must run from your logged-in Windows desktop, not SSH/WMI (Session 0).'
}
$existingWorkers = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -match '(^|[\\\s"''])emi_worker\.py(["''\s]|$)' })
if ($existingWorkers.Count -gt 0) {
    if (-not $RestartIdleWorker) {
        $existingWorkers | Select-Object ProcessId, SessionId | Format-Table
        throw 'A worker is already running; explicit -RestartIdleWorker is required.'
    }
    $allProcesses = @(Get-CimInstance Win32_Process)
    foreach ($oldWorker in $existingWorkers) {
        $descendantIds = @([uint32]$oldWorker.ProcessId)
        do {
            $newIds = @($allProcesses | Where-Object {
                $_.ParentProcessId -in $descendantIds -and $_.ProcessId -notin $descendantIds
            } | Select-Object -ExpandProperty ProcessId)
            $descendantIds += $newIds
        } while ($newIds.Count -gt 0)
        $activeChildren = @($allProcesses | Where-Object {
            $_.ProcessId -in $descendantIds -and $_.ProcessId -ne $oldWorker.ProcessId -and $_.Name -ne 'conhost.exe'
        })
        if ($activeChildren.Count -gt 0) {
            throw 'Worker has active child processes; wait for its task to finish before restarting.'
        }
    }
    foreach ($oldWorker in $existingWorkers) {
        Stop-Process -Id $oldWorker.ProcessId -ErrorAction Stop
    }
}
$pythonPath = (Get-Command python.exe).Source
$codexPath = (Get-Command codex.exe).Source
Write-Host "Codex: $codexPath; Windows session: $sessionId"
# Start-Process inherits this interactive desktop, unlike Win32_Process.Create via WMI.
$worker = Start-Process -FilePath $pythonPath -ArgumentList @("-X", "utf8", "`"$projectRoot\emi_worker.py`"") `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput "$projectRoot\emi-worker-desktop.out.log" `
    -RedirectStandardError "$projectRoot\emi-worker-desktop.err.log"
Start-Sleep -Seconds 2
$worker.Refresh()
if ($worker.HasExited) {
    throw "Worker exited with code $($worker.ExitCode); check emi-worker-desktop.err.log. No smoke task queued."
}
Write-Host "Worker started: PID=$($worker.Id). Check emi_worker.log and send one small file-read task."

if ($SmokeTest) {
    $taskId = 'tools-smoke-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
    $proofPath = Join-Path $projectRoot ($taskId + '.proof.json')
    $task = @{
        task_id = $taskId
        from = 'anh'
        project = 'ai-hub'
        kind = 'task'
        body = "Test tools only. Read AGENTS.md and STATE.md using a shell tool. Compute SHA256 of STATE.md using Get-FileHash. Write JSON with task_id=$taskId and state_sha256 to $proofPath using a shell tool. Read the JSON back to verify. Write the requested report file with the actual hash and command results. Do not modify any other file, restart any process, call Codex again, or send API messages yourself; the worker will send the report to anh via AI Hub. If tools fail, report failure honestly."
    }
    $inboxPath = Join-Path $projectRoot 'emi-tasks\inbox'
    New-Item -ItemType Directory -Path $inboxPath -Force | Out-Null
    $taskFile = Join-Path $inboxPath ($taskId + '.json')
    [System.IO.File]::WriteAllText(($taskFile + '.tmp'), ($task | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath ($taskFile + '.tmp') -Destination $taskFile
    Write-Host "Queued $taskId. Verify proof JSON and AI Hub report after the worker polls."
}
