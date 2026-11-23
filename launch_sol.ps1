# Launcher for run_sol_direct.py — run detached so the SSH call returns at once.
# Usage (via ssh): powershell -NoProfile -File E:\MyProj\ai-hub\launch_sol.ps1 <run_id> <prompt_file>
# Stdout/stderr of the runner go to sol-launch-<run_id>.log/.err so a crash at
# startup still leaves a trace.
$runId = $args[0]
$promptFile = $args[1]
Start-Process -FilePath "python" -ArgumentList "E:\MyProj\ai-hub\run_sol_direct.py", $runId, $promptFile -WindowStyle Hidden -RedirectStandardOutput "E:\MyProj\ai-hub\sol-launch-$runId.log" -RedirectStandardError "E:\MyProj\ai-hub\sol-launch-$runId.err"
Write-Output "LAUNCHED $runId"
