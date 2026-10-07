# rt_power.ps1 <tag> <powerlimit W> <mode> [KEY=VAL ...]: log GPU (nvidia-smi, 200 ms) and CPU package (RAPL, 1 s) power while rt_power_run.sh runs (outputs in rt\pw)
param([string]$tag, [int]$pl, [string]$mode, [Parameter(ValueFromRemainingArguments)][string[]]$kv)
$d = "C:\Users\saqoosha\VDGS\dvr\rt\pw"; New-Item -ItemType Directory -Force $d | Out-Null
nvidia-smi -pl $pl | Out-Null
if ($env:PW_LGC) { nvidia-smi -lgc "0,$env:PW_LGC" | Out-Null }
Start-Sleep 3
$g = Start-Process nvidia-smi -ArgumentList "--query-gpu=timestamp,power.draw,clocks.gr,clocks.mem,utilization.gpu,temperature.gpu --format=csv,noheader,nounits -lms 200 -f $d\$tag.gpu.csv" -PassThru -WindowStyle Hidden
$cpu = "$d\$tag.cpu.csv"; Remove-Item $cpu -ErrorAction SilentlyContinue
$j = Start-Job { param($f) Get-Counter "\Energy Meter(rapl_package0_pkg)\Power" -SampleInterval 1 -Continuous | % { "{0},{1}" -f ([DateTimeOffset]$_.Timestamp).ToUnixTimeMilliseconds(), $_.CounterSamples[0].CookedValue | Out-File -Append -Encoding ascii $f } } -ArgumentList $cpu
Start-Sleep 2
wsl -d Ubuntu-24.04 -u saqoosha -- bash /mnt/c/Users/saqoosha/VDGS/dvr/rt/rt_power_run.sh $tag $mode @kv > "$d\$tag.log"
Start-Sleep 2
Stop-Process $g; Stop-Job $j; Remove-Job $j
nvidia-smi -pl 450 | Out-Null
nvidia-smi -rgc | Out-Null
"done $tag"
