# Chạy từ cửa sổ PowerShell trên màn hình đăng nhập Windows (Session 1), KHÔNG qua SSH/WMI:
# Codex CLI cần phiên desktop mới tạo được tiến trình con.
# Token của từng AI đặt trước một lần ở mức người dùng, ví dụ:
#   setx AI_HUB_TOKEN_CODEX "ah1_..."      (mở lại PowerShell sau khi setx)
# Cấu hình: copy runner.example.json thành runner.json và sửa đường dẫn.
param([string]$Config = "runner.json", [switch]$RestartIdleRunner)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$sessionId = (Get-Process -Id $PID).SessionId
if ($sessionId -eq 0) { throw 'Runner phải chạy từ màn hình đăng nhập Windows, không phải SSH/WMI (Session 0).' }
$existing = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -match '(^|[\\\s"''])agent_runner\.py([''"\s]|$)' })
if ($existing.Count -gt 0) {
    if (-not $RestartIdleRunner) {
        $existing | Select-Object ProcessId, SessionId | Format-Table
        throw 'Runner đang chạy; thêm -RestartIdleRunner nếu thật sự muốn khởi động lại.'
    }
    $all = @(Get-CimInstance Win32_Process)
    foreach ($old in $existing) {
        $kids = @($all | Where-Object { $_.ParentProcessId -eq $old.ProcessId -and $_.Name -ne 'conhost.exe' })
        if ($kids.Count -gt 0) { throw 'Runner đang chạy một lượt AI; đợi xong rồi hãy khởi động lại.' }
        Stop-Process -Id $old.ProcessId -ErrorAction Stop
    }
}
$configPath = Join-Path $projectRoot $Config
if (-not (Test-Path -LiteralPath $configPath)) { throw "Không thấy $configPath (copy từ runner.example.json)." }
$pythonPath = (Get-Command python.exe).Source
$runner = Start-Process -FilePath $pythonPath `
    -ArgumentList @("-X", "utf8", "`"$projectRoot\agent_runner.py`"", "--config", "`"$configPath`"") `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput "$projectRoot\agent-runner.out.log" `
    -RedirectStandardError "$projectRoot\agent-runner.err.log"
Start-Sleep -Seconds 3
$runner.Refresh()
if ($runner.HasExited) { throw "Runner thoát với mã $($runner.ExitCode); xem agent-runner.err.log." }
Write-Host "Runner chạy: PID=$($runner.Id), session=$sessionId. Xem agent-runner.out.log; tab AI trên /hub sẽ hiện agent online."
