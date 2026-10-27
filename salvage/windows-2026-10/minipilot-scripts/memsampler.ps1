param(
  [string]$Out = "C:/dev/products/sciagent/.cache/minipilot-mem.csv",
  [string]$StopFlag = "C:/Users/Ebinezer/AppData/Local/Temp/claude/c--dev-products-sciagent/9dfb0c58-9eaa-41f7-877a-e9727f0935ad/scratchpad/memsampler.stop",
  [string]$PilotRoot = "C:/dev/products/sciagent/.cache/minipilot",
  [int]$Interval = 30
)
if (-not (Test-Path $Out)) {
  "time,kind,pid,ppid,private_mb,peak_commit_mb,working_mb,committed_gb,limit_gb,headroom_gb,cmd" | Out-File -FilePath $Out -Encoding utf8
}
while (-not (Test-Path $StopFlag)) {
  $t = (Get-Date).ToString("s")
  $c = (Get-Counter '\Memory\Committed Bytes','\Memory\Commit Limit').CounterSamples
  $com = $c[0].CookedValue / 1GB; $lim = $c[1].CookedValue / 1GB; $head = $lim - $com
  $lines = @("$t,system,,,,,,{0:F3},{1:F3},{2:F3}," -f $com, $lim, $head)
  foreach ($p in Get-CimInstance Win32_Process -Filter "Name='python.exe'") {
    $cmd = ($p.CommandLine -replace '[",]', ' ')
    if ($cmd.Length -gt 140) { $cmd = $cmd.Substring(0, 140) }
    $lines += "{0},proc,{1},{2},{3:F1},{4:F1},{5:F1},,,,{6}" -f $t, $p.ProcessId, $p.ParentProcessId, ($p.PrivatePageCount/1MB), ($p.PeakPageFileUsage/1KB), ($p.WorkingSetSize/1MB), $cmd
  }
  $lines | Out-File -FilePath $Out -Encoding utf8 -Append
  if ($head -lt 1.5 -and (Test-Path $PilotRoot)) {
    if (-not (Test-Path "$PilotRoot/STOP")) { New-Item -ItemType File "$PilotRoot/STOP" | Out-Null; "$t,STOP-created,,,,,,,,{0:F3}," -f $head | Out-File -FilePath $Out -Encoding utf8 -Append }
  }
  Start-Sleep -Seconds $Interval
}
