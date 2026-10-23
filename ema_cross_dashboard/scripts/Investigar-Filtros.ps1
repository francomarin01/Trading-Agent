[CmdletBinding()]
param()
$ErrorActionPreference='Stop'; $root=Split-Path -Parent $PSScriptRoot
$python='C:\Python314\python.exe'; if(-not(Test-Path $python)){$python='python'}
Push-Location $root
try { & $python -m app.research; if($LASTEXITCODE -ne 0){throw "La investigación de filtros falló: $LASTEXITCODE"} }
finally { Pop-Location }
