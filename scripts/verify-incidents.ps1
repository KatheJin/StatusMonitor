# Stage A real PostgreSQL/HTTP acceptance. No published ports or Docker recovery actions.
$ErrorActionPreference = 'Stop'
$project = 'sm-incidents-' + [guid]::NewGuid().ToString('N').Substring(0,12)
$repo = Split-Path $PSScriptRoot -Parent
function Compose {
    & docker compose -p $project -f demo/incidents/compose.yaml @args
    if ($LASTEXITCODE -ne 0) { throw "Stage A command failed: $args" }
}
Push-Location $repo
try {
    Write-Host "Fresh disposable project: $project (no published ports)"
    Compose config --quiet
    Compose build
    Compose up -d db fixture
    # Keep the verifier container and PostgreSQL audit for inspection, even on failure.
    Compose run --name "$project-verifier" verify
    Write-Host 'PASS: real PostgreSQL migrations, multi-process detection, production worker HTTP fault/recovery.'
} finally {
    Write-Host "Evidence retained. Logs: docker logs $project-verifier"
    Write-Host "Stop only this test project: docker compose -p $project -f demo/incidents/compose.yaml down"
    Write-Host 'No volumes were deleted. Expect about 6 minutes for the unchanged 60-second worker schedule.'
    Pop-Location
}
