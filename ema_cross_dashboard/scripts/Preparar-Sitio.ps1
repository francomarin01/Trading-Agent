[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$web = Join-Path $root 'web'
$dist = Join-Path $root 'dist'

if (Test-Path $dist) { Remove-Item -LiteralPath $dist -Recurse -Force }
New-Item -ItemType Directory -Path (Join-Path $dist 'data') -Force | Out-Null
Copy-Item (Join-Path $web 'index.html') $dist
Copy-Item (Join-Path $web 'app.js') $dist
Copy-Item (Join-Path $web 'styles.css') $dist

# Public bundle includes only data consumed by the dashboard. Internal backtest
# and filter-research summaries are intentionally excluded from publication.
foreach ($name in @('dashboard.json', 'asset_rotation.json')) {
  $source = Join-Path $web "data\$name"
  if (-not (Test-Path $source)) { throw "Falta $source. Ejecutá la actualización antes de preparar el sitio." }
  Copy-Item $source (Join-Path $dist "data\$name")
}

Write-Host "Sitio estático preparado en: $dist"
Write-Host 'Publicá solamente esta carpeta. No incluye base SQLite, claves ni backtests detallados.'
