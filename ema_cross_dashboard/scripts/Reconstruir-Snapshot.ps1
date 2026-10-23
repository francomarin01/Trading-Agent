[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = 'C:\Python314\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }
Push-Location $root
try {
  $code = @'
from app.updater import rebuild_from_local
s = rebuild_from_local()
print('Snapshot {} as of {} · {} assets'.format(s['status'], s['as_of'], len(s['assets'])))
'@
  & $python -c $code
} finally { Pop-Location }
