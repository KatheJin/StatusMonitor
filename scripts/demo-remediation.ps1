# Disposable local demo; no host ports and no existing project volumes.
$ErrorActionPreference = 'Stop'
$project = 'sm-remediation-' + [guid]::NewGuid().ToString('N').Substring(0,12)
$repo = Split-Path $PSScriptRoot -Parent
function Compose {
    & docker compose -p $project -f demo/remediation/compose.yaml @args
    if ($LASTEXITCODE -ne 0) { throw "Demo command failed: $args" }
}
Push-Location $repo
try {
    Compose config --quiet
    Compose up --build -d db migrate healthy failing router
    Compose run --rm controller
    Write-Host 'PASS: verified baseline, three failures, one rollback, three recovery checks; audit in PostgreSQL.'
} finally {
    Write-Host "Project retained: $project"
    Write-Host "Inspect: docker compose -p $project -f demo/remediation/compose.yaml logs"
    Write-Host "Stop without deleting volumes: docker compose -p $project -f demo/remediation/compose.yaml down"
    Pop-Location
}
