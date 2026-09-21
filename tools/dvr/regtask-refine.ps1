$name = 'jdl-r6-refine6'
Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument '-d Ubuntu-24.04 -u saqoosha -- bash -lc "export CUDA_HOME=/usr/local/cuda-12.9 PATH=/usr/local/cuda-12.9/bin:\$PATH ONLY_INTERP=1 ITERS=16 BATCH=8 BANDS=4 BLUR=3; cd /mnt/c/Users/saqoosha/JDL-2026-R6/dvr && rm -f refined6.jsonl && ~/gsenv/bin/python refine_batch.py /mnt/c/Users/saqoosha/JDL-2026-R6-fix/out/JDL-2026-R6-fix-web.ply dvr_pinhole.mp4 poses60_init.json refined6.jsonl 1 > refine6.log 2>&1"'
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden
Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $name; Start-Sleep 5; (Get-ScheduledTask -TaskName $name).State
