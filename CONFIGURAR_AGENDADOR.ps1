$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.venv\Scripts\pythonw.exe'
$worker = Join-Path $root 'PROCESSAR_PASTAS.py'
if (-not (Test-Path $python)) { throw 'Python virtual nao encontrado' }
$task = 'NextFit - Converter e Transcrever Reunioes'
$action = New-ScheduledTaskAction -Execute $python -Argument ('"' + $worker + '"') -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings -Description 'Converte MP4 em MP3 e transcreve MP3 em TXT' -Force | Out-Null
Start-ScheduledTask -TaskName $task
Write-Host ('Tarefa criada: '+$task)
