# Real Docker/PostgreSQL acceptance check. Run from any working directory.
param(
    [int]$ApiPort = 18000,
    [int]$DbPort = 15432
)
$ErrorActionPreference = 'Stop'
$project = 'statusmonitor-verify-' + [guid]::NewGuid().ToString('N').Substring(0, 12)
$repo = Split-Path $PSScriptRoot -Parent
$previousApiPort = $env:API_PUBLISHED_PORT
$previousDbPort = $env:DB_PUBLISHED_PORT

function Invoke-Compose {
    & docker compose -p $project @args
    if ($LASTEXITCODE -ne 0) { throw "Compose failed: $args" }
}

function Wait-Api {
    $deadline = (Get-Date).AddSeconds(120)
    do {
        try {
            $ready = Invoke-RestMethod "$base/db-health" -TimeoutSec 5
            if ($ready.database -eq 'ok') { return }
        } catch { }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw 'API readiness timed out'
}

function Get-CheckHistory {
    # Windows PowerShell 5.1 can emit the JSON array as ONE pipeline object.
    # Assign first, then enumerate its records; @(<Invoke-RestMethod>) can
    # otherwise wrap even an empty JSON array in an outer array of Count 1.
    $response = Invoke-RestMethod "$base/monitors/$monitorId/checks?limit=100" -TimeoutSec 5
    foreach ($check in $response) {
        if ($null -ne $check.id -and $check.id -gt 0) {
            $check
        }
    }
}

function Assert-SavedData {
    $restored = Invoke-RestMethod "$base/monitors/$monitorId" -TimeoutSec 5
    $history = @(Get-CheckHistory)
    if ($restored.name -ne 'Docker acceptance' -or $savedId -notin $history.id) {
        throw 'Monitor or original CheckResult did not survive'
    }
}

Push-Location $repo
try {
    $env:API_PUBLISHED_PORT = "$ApiPort"
    $env:DB_PUBLISHED_PORT = "$DbPort"
    $base = "http://127.0.0.1:$ApiPort"
    Write-Host "Isolated project: $project; fresh volume: ${project}_postgres_data"
    Invoke-Compose config --quiet
    Invoke-Compose up --build -d
    Wait-Api
    # No manually triggered check: the standalone worker must create this row.
    $monitor = Invoke-RestMethod -Method Post -Uri "$base/monitors" -ContentType 'application/json' -Body '{"name":"Docker acceptance","url":"http://api:8000/health"}'
    $monitorId = $monitor.id
    $deadline = (Get-Date).AddSeconds(150)
    $check = $null
    do {
        $check = Get-CheckHistory | Select-Object -First 1
        if ($null -ne $check.id) { break }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    if ($null -eq $check.id -or $check.status_code -ne 200 -or $check.is_up -ne $true) {
        throw 'Worker did not persist a successful real HTTP check'
    }
    $savedId = $check.id
    Invoke-RestMethod -Method Patch -Uri "$base/monitors/$monitorId" -ContentType 'application/json' -Body '{"is_active":false}' | Out-Null
    Invoke-Compose stop worker
    Invoke-Compose restart db api
    Wait-Api
    Assert-SavedData
    Write-Host 'PASS: container restart preserved monitor and check history'

    # down (without -v) removes containers/network but preserves the named volume.
    Invoke-Compose down
    Invoke-Compose up -d
    Wait-Api
    Assert-SavedData
    Invoke-Compose ps -a
    Write-Host 'PASS: clean-volume startup, migrations, real worker HTTP check, restart and volume reuse'
} finally {
    Write-Host "Resources retained for inspection. Project: $project"
    Write-Host "Stop test containers: docker compose -p $project down"
    Write-Host "Only when finished, delete THIS test volume: docker volume rm ${project}_postgres_data"
    $env:API_PUBLISHED_PORT = $previousApiPort
    $env:DB_PUBLISHED_PORT = $previousDbPort
    Pop-Location
}
