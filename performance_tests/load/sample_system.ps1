param(
    [int]$DurationSeconds = 780,
    [int]$IntervalSeconds = 10,
    [string]$OutputPath = "performance_tests/load/results/system.jsonl"
)

$ErrorActionPreference = "Stop"
$target = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $OutputPath))
$directory = Split-Path -Parent $target
New-Item -ItemType Directory -Force -Path $directory | Out-Null
if (Test-Path -LiteralPath $target) {
    Remove-Item -LiteralPath $target
}

$deadline = [DateTimeOffset]::UtcNow.AddSeconds($DurationSeconds)
while ([DateTimeOffset]::UtcNow -lt $deadline) {
    $containers = @{}
    & docker stats --no-stream --format "{{json .}}" | ForEach-Object {
        $row = $_ | ConvertFrom-Json
        $containers[$row.Name] = @{
            cpu = $row.CPUPerc
            memory = $row.MemUsage
            memory_percent = $row.MemPerc
        }
    }

    $connections = & docker exec tilecompany-local-prod-postgres-1 sh -c 'psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -Atc \"SELECT count(*) FROM pg_stat_activity WHERE datname=current_database();\"'
    if ($LASTEXITCODE -ne 0 -or -not $connections) {
        throw "PostgreSQL connection sampling failed."
    }
    $os = Get-CimInstance Win32_OperatingSystem
    $cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
    $sample = @{
        timestamp = [DateTimeOffset]::UtcNow.ToString("o")
        postgres_connections = [int]$connections
        host_cpu_percent = [double]$cpu
        host_memory_used_percent = [math]::Round((1 - ($os.FreePhysicalMemory / $os.TotalVisibleMemorySize)) * 100, 2)
        containers = $containers
    }
    Add-Content -LiteralPath $target -Value ($sample | ConvertTo-Json -Compress -Depth 5)
    Start-Sleep -Seconds $IntervalSeconds
}
