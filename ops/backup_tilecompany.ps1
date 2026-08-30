[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $BackupRoot,
    [string] $ComposeFile = (Join-Path $PSScriptRoot 'local-prod/compose.yml'),
    [string] $EnvFile = (Join-Path $PSScriptRoot 'local-prod/.env')
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$composeFile = (Resolve-Path $ComposeFile).Path
$envFile = (Resolve-Path $EnvFile).Path

function Invoke-Docker([string[]] $Arguments) { & docker @Arguments; if ($LASTEXITCODE) { throw "docker failed: $($Arguments -join ' ')" } }
function Invoke-Compose([string[]] $Arguments) {
    $allArguments = @('compose','--env-file',$envFile,'-f',$composeFile) + $Arguments
    Invoke-Docker $allArguments
}
function Is-Within([string] $Child, [string] $Parent) {
    $c = [IO.Path]::GetFullPath($Child).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $p = [IO.Path]::GetFullPath($Parent).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    return $c.StartsWith($p, [StringComparison]::OrdinalIgnoreCase)
}
function Wait-Healthy([string] $Container) {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $status = (& docker inspect --format '{{.State.Health.Status}}' $Container).Trim()
        if ($status -eq 'healthy') { return }
        Start-Sleep -Seconds 2
    }
    throw "Container did not become healthy: $Container"
}
function Volume-For([string] $Container, [string] $Destination) {
    $mounts = (& docker inspect --format '{{range .Mounts}}{{println .Name "|" .Destination}}{{end}}' $Container)
    foreach ($mount in $mounts) {
        $parts = $mount -split '\|', 2
        if ($parts.Count -eq 2 -and $parts[1].Trim() -eq $Destination) { return $parts[0].Trim() }
    }
    throw "No named volume mounted at $Destination"
}
function Archive-Volume([string] $Volume, [string] $Artifact) {
    $result = & docker run --rm --mount "type=volume,src=$Volume,dst=/source,readonly" --mount "type=bind,src=$staging,dst=/backup" --mount "type=bind,src=$PSScriptRoot,dst=/tool,readonly" $image python /tool/recovery_archive.py create /source "/backup/$Artifact"
    if ($LASTEXITCODE) { throw "Archive creation failed: $Artifact" }
    return ($result | Select-Object -Last 1 | ConvertFrom-Json)
}
function Verify-Archive([string] $Artifact) {
    $result = & docker run --rm --mount "type=bind,src=$staging,dst=/backup,readonly" --mount "type=bind,src=$PSScriptRoot,dst=/tool,readonly" $image python /tool/recovery_archive.py verify "/backup/$Artifact"
    if ($LASTEXITCODE) { throw "Archive validation failed: $Artifact" }
    return ($result | Select-Object -Last 1 | ConvertFrom-Json)
}

if (-not [IO.Path]::IsPathRooted($BackupRoot)) { throw 'BackupRoot must be an absolute path.' }
$backupRoot = [IO.Path]::GetFullPath($BackupRoot)
if (Is-Within $backupRoot $repoRoot -or $backupRoot -eq $repoRoot) { throw 'BackupRoot must be outside the repository.' }
if ($backupRoot -match '(?i)(^|[\\/])(media|private_inventory_price_import_sources)([\\/]|$)') { throw 'BackupRoot must not be an application storage path.' }
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
$probe = Join-Path $backupRoot '.tilecompany-write-probe'; [IO.File]::WriteAllText($probe, 'ok'); Remove-Item -LiteralPath $probe -Force

Invoke-Docker @('version','--format','{{.Server.Version}}') | Out-Null
$web = (& docker compose --env-file $envFile -f $composeFile ps -q web).Trim()
$postgres = (& docker compose --env-file $envFile -f $composeFile ps -q postgres).Trim()
$redis = (& docker compose --env-file $envFile -f $composeFile ps -q redis).Trim()
if (-not $web -or -not $postgres -or -not $redis) { throw 'Expected local-production web/postgres/redis containers are not running.' }
Wait-Healthy $web; Wait-Healthy $postgres; Wait-Healthy $redis
$mediaVolume = Volume-For $web '/app/media'
$privateVolume = Volume-For $web '/app/private_inventory_price_import_sources'
$image = (& docker inspect --format '{{.Config.Image}}' $web).Trim()
$gitSha = (& git -C $repoRoot rev-parse HEAD).Trim(); $shortSha = (& git -C $repoRoot rev-parse --short=12 HEAD).Trim()
$createdUtc = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$setId = "tilecompany-backup-$createdUtc-$shortSha"
$staging = Join-Path $backupRoot ".$setId-incomplete"
$complete = Join-Path $backupRoot $setId
if ((Test-Path -LiteralPath $staging) -or (Test-Path -LiteralPath $complete)) { throw 'Backup set name collision.' }
New-Item -ItemType Directory -Path $staging | Out-Null
$webStopped = $false; $dumpPath = "/tmp/$setId.dump"

try {
    $sentinels = (Invoke-Compose @('exec','-T','web','python','ops/recovery_verify.py') | Select-Object -Last 1 | ConvertFrom-Json)
    Invoke-Compose @('stop','web'); $webStopped = $true
    $dumpCommand = 'pg_dump --format=custom --no-owner --no-privileges --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --file=' + $dumpPath
    Invoke-Compose @('exec','-T','postgres','sh','-c',$dumpCommand)
    Invoke-Docker @('cp',"${postgres}:$dumpPath",(Join-Path $staging 'postgresql.dump'))
    $dumpListing = Invoke-Compose @('exec','-T','postgres','sh','-c',"pg_restore --list $dumpPath")
    if (-not $dumpListing) { throw 'PostgreSQL archive listing was unexpectedly empty.' }
    $media = Archive-Volume $mediaVolume 'media.tar.gz'; $private = Archive-Volume $privateVolume 'private_inventory.tar.gz'
    $null = Verify-Archive 'media.tar.gz'; $null = Verify-Archive 'private_inventory.tar.gz'
    $artifacts = @('postgresql.dump','media.tar.gz','private_inventory.tar.gz')
    $hashes = @{}
    foreach ($artifact in $artifacts) { $hashes[$artifact] = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $staging $artifact)).Hash.ToLowerInvariant() }
    $manifest = [ordered]@{
        format_version = 1; backup_set_id = $setId; created_utc = $createdUtc; completed_utc = [DateTime]::UtcNow.ToString('o')
        git_sha = $gitSha; git_short_sha = $shortSha; compose_project = 'tilecompany-local-prod'
        postgresql_client_version = ((Invoke-Compose @('exec','-T','postgres','pg_dump','--version')) -join ' ').Trim()
        postgresql_server_version = ((Invoke-Compose @('exec','-T','postgres','postgres','--version')) -join ' ').Trim()
        artifacts = [ordered]@{
            postgresql = @{ filename='postgresql.dump'; size_bytes=(Get-Item (Join-Path $staging 'postgresql.dump')).Length; sha256=$hashes['postgresql.dump'] }
            media = @{ filename='media.tar.gz'; size_bytes=(Get-Item (Join-Path $staging 'media.tar.gz')).Length; sha256=$hashes['media.tar.gz']; member_count=[int]$media.member_count }
            private_inventory = @{ filename='private_inventory.tar.gz'; size_bytes=(Get-Item (Join-Path $staging 'private_inventory.tar.gz')).Length; sha256=$hashes['private_inventory.tar.gz']; member_count=[int]$private.member_count }
        }
        private_inventory_retention_days = 90; private_inventory_archive_expiry_utc = ([DateTime]::UtcNow.AddDays(90).ToString('o'))
        redis_included = $false; staticfiles_included = $false; secrets_included = $false; web_quiesced = $true
        database_sentinels = $sentinels.database_sentinels; file_references = $sentinels.file_references
    }
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $staging 'manifest.json') -Encoding utf8NoBOM
    $checksumLines = @($artifacts + 'manifest.json' | ForEach-Object {
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $staging $_)).Hash.ToLowerInvariant()
        "$hash  $_"
    })
    Set-Content -LiteralPath (Join-Path $staging 'SHA256SUMS.txt') -Value $checksumLines -Encoding ascii
    foreach ($line in $checksumLines) { $parts = $line -split '  ', 2; if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $staging $parts[1])).Hash.ToLowerInvariant() -ne $parts[0]) { throw 'Checksum re-verification failed.' } }
    Move-Item -LiteralPath $staging -Destination $complete
    Write-Output "Backup set complete: $complete"
}
finally {
    if ($postgres) { & docker compose --env-file $envFile -f $composeFile exec -T postgres sh -c "rm -f $dumpPath" | Out-Null }
    if ($webStopped) { & docker compose --env-file $envFile -f $composeFile start web | Out-Null; Wait-Healthy $web }
}
