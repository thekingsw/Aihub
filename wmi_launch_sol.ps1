# Launch run_sol_direct.py via WMI (Win32_Process.Create) — fully detached,
# survives the SSH session ending. Same mechanism as the TalkBook restart.
# Usage (via ssh): powershell -NoProfile -File E:\MyProj\ai-hub\wmi_launch_sol.ps1 <run_id> <prompt_file>
$runId = $args[0]
$promptFile = $args[1]
$cmd = "E:\Python3.14\python.exe E:\MyProj\ai-hub\run_sol_direct.py $runId $promptFile"
$r = ([wmiclass]"Win32_Process").Create($cmd, "E:\MyProj\ai-hub")
Write-Output ("WMI_RETURN=" + $r.ReturnValue + " PID=" + $r.ProcessId)
