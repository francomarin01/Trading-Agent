param([switch]$DownloadData)
$ErrorActionPreference = 'Stop'
$researchRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $researchRoot
try {
    if ($DownloadData) {
        $researchCfg = Get-Content -LiteralPath '.\config\regime_sensitivity.json' -Raw | ConvertFrom-Json
        python -m app.research_short_history
        if ($LASTEXITCODE -ne 0) { throw 'Short-history research download failed.' }
        python -m app.cboe_put_call --as-of $researchCfg.as_of
        if ($LASTEXITCODE -ne 0) { throw 'Cboe research download failed.' }
    }
    python -m unittest discover -s tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed. Sensitivity was not run.' }
    python -m app.regime_sensitivity
    if ($LASTEXITCODE -ne 0) { throw 'Sensitivity failed. Inspect the error before using results.' }
    python -m app.validate_regime_artifacts
    if ($LASTEXITCODE -ne 0) { throw 'Research artifact validation failed.' }
}
finally { Pop-Location }
