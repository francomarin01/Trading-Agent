[CmdletBinding()]
param([switch]$Actualizar)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if ($Actualizar) { & (Join-Path $PSScriptRoot 'Actualizar.ps1') }
$python = 'C:\Python314\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }
Push-Location $root
try { & $python -m app.server }
finally { Pop-Location }
