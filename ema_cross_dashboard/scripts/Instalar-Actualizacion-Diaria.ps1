[CmdletBinding()]
param([string]$Hora = '18:15')
$root = Split-Path -Parent $PSScriptRoot
$time = $Hora
if ($time -notmatch '^([01]\d|2[0-3]):[0-5]\d$') { throw 'Usá formato HH:mm.' }
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot 'Actualizar.ps1')`""
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At ([datetime]::ParseExact($time,'HH:mm',$null))
Register-ScheduledTask -TaskName 'EMA Cross Dashboard Update' -Action $action -Trigger $trigger -Description 'Actualiza datos y snapshot del EMA Cross Dashboard.' -Force | Out-Null
Write-Host "Tarea instalada para lunes a viernes a las $time."
