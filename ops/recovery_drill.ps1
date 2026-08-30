[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $BackupSet,
    [string] $EnvFile = (Join-Path $PSScriptRoot 'local-prod/.env'),
    [switch] $DeleteBackupAfterSuccess,
    [switch] $KeepFailedDrill
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = (Resolve-Path $EnvFile).Path
if (-not [IO.Path]::IsPathRooted($BackupSet)) { throw 'BackupSet must be an absolute path.' }
$backupSet = (Resolve-Path $BackupSet).Path
if ([IO.Path]::GetFullPath($backupSet).StartsWith($repoRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'BackupSet must be outside the repository.' }

function Invoke-Docker([string[]] $Arguments) { & docker @Arguments; if ($LASTEXITCODE) { throw 'A drill Docker command failed.' } }
function Read-EnvValue([string] $Name) {
    $line = Get-Content -LiteralPath $envFile | Where-Object { $_ -match "^$([regex]::Escape($Name))=(.*)$" } | Select-Object -First 1
    if (-not $line) { throw "Missing $Name in EnvFile." }
    return ($line -replace "^$([regex]::Escape($Name))=", '')
}
function Check-Sums {
    foreach ($line in Get-Content -LiteralPath (Join-Path $backupSet 'SHA256SUMS.txt')) {
        $parts = $line -split '  ', 2
        if ($parts.Count -ne 2 -or (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $backupSet $parts[1])).Hash.ToLowerInvariant() -ne $parts[0]) { throw 'Backup checksum verification failed.' }
    }
}
function Wait-Postgres { for ($i=0; $i -lt 30; $i++) { & docker exec $postgres pg_isready -U $drillDbUser -d $drillDb | Out-Null; if (-not $LASTEXITCODE) { return }; Start-Sleep 2 }; throw 'Drill PostgreSQL did not become ready.' }
function Archive-Restore([string] $Archive, [string] $Volume) {
    Invoke-Docker @('run','--rm','--user','0:0','--mount',"type=bind,src=$backupSet,dst=/backup,readonly",'--mount',"type=volume,src=$Volume,dst=/target",'--mount',"type=bind,src=$PSScriptRoot,dst=/tool,readonly",$image,'python','/tool/recovery_archive.py','extract',"/backup/$Archive",'/target')
}

$manifestPath = Join-Path $backupSet 'manifest.json'
foreach ($file in @($manifestPath, (Join-Path $backupSet 'postgresql.dump'), (Join-Path $backupSet 'media.tar.gz'), (Join-Path $backupSet 'private_inventory.tar.gz'), (Join-Path $backupSet 'SHA256SUMS.txt'))) { if (-not (Test-Path -LiteralPath $file)) { throw "Backup set is incomplete: $file" } }
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
if ($manifest.format_version -ne 1 -or $manifest.secrets_included -ne $false -or $manifest.redis_included -ne $false -or $manifest.staticfiles_included -ne $false) { throw 'Backup manifest does not match the approved recovery format.' }
$currentSha = (& git -C $repoRoot rev-parse HEAD).Trim(); if ($manifest.git_sha -ne $currentSha) { throw 'Backup Git SHA differs from current code; refusing drill without matching code.' }
Check-Sums

$id = [guid]::NewGuid().ToString('N').Substring(0,12)
$project = "tilecompany-recovery-drill-$id"
$network = "$project-network"; $postgresVolume = "$project-postgres"; $redisVolume = "$project-redis"; $mediaVolume = "$project-media"; $privateVolume = "$project-private"; $staticVolume = "$project-static"
$postgres = "$project-postgres"; $redis = "$project-redis"; $web = "$project-web"
$image = (& docker inspect --format '{{.Config.Image}}' ((& docker compose --env-file $envFile -f (Join-Path $PSScriptRoot 'local-prod/compose.yml') ps -q web).Trim())).Trim()
$drillDb = 'tilecompany_recovery'; $drillDbUser = 'recovery_admin'; $drillPassword = [guid]::NewGuid().ToString('N'); $redisPassword = [guid]::NewGuid().ToString('N')
$scratch = Join-Path ([IO.Path]::GetTempPath()) "$project-env"; New-Item -ItemType Directory -Path $scratch | Out-Null; $drillEnv = Join-Path $scratch 'drill.env'
@(
    "POSTGRES_DB=$drillDb", "POSTGRES_USER=$drillDbUser", "POSTGRES_PASSWORD=$drillPassword",
    "APP_ENV=local_production", 'DEBUG=false', "DJANGO_SECRET_KEY=$(Read-EnvValue 'LOCALPROD_DJANGO_SECRET_KEY')",
    "ALLOWED_HOSTS=$(Read-EnvValue 'LOCALPROD_ALLOWED_HOSTS')", "CSRF_TRUSTED_ORIGINS=$(Read-EnvValue 'LOCALPROD_CSRF_TRUSTED_ORIGINS')",
    'DB_BACKEND=postgresql', "POSTGRES_HOST=postgres", 'POSTGRES_PORT=5432', 'POSTGRES_SSLMODE=disable',
    'CACHE_BACKEND=redis', "REDIS_URL=redis://:$redisPassword@redis:6379/0", 'SMS_ENABLED=false', 'SMS_OTP_LOGIN_ENABLED=false', 'SMS_BACKEND=accounts.sms.DisabledSmsBackend', 'LOCAL_PAYMENT_SIMULATOR_ENABLED=false', 'TRUST_PROXY_SSL_HEADER=true', 'TRUST_PROXY_CLIENT_IP=false', 'STATIC_ROOT=/app/staticfiles'
) | Set-Content -LiteralPath $drillEnv -Encoding ascii
$cleanup = $false
try {
    foreach ($volume in @($postgresVolume,$redisVolume,$mediaVolume,$privateVolume,$staticVolume)) { Invoke-Docker @('volume','create','--label',"tilecompany.recovery.project=$project",$volume) | Out-Null }
    Invoke-Docker @('network','create','--label',"tilecompany.recovery.project=$project",$network) | Out-Null
    Invoke-Docker @('run','-d','--name',$postgres,'--network',$network,'--network-alias','postgres','--env-file',$drillEnv,'--mount',"type=volume,src=$postgresVolume,dst=/var/lib/postgresql",'postgres:18.6-bookworm') | Out-Null
    Invoke-Docker @('run','-d','--name',$redis,'--network',$network,'--network-alias','redis','--env',"REDIS_PASSWORD=$redisPassword",'--mount',"type=volume,src=$redisVolume,dst=/data",'redis:7.4.11-alpine','/bin/sh','-c','exec redis-server --appendonly no --requirepass "$REDIS_PASSWORD"') | Out-Null
    Wait-Postgres
    Invoke-Docker @('run','--rm','--network',$network,'--env-file',$drillEnv,'--mount',"type=bind,src=$backupSet,dst=/backup,readonly",'postgres:18.6-bookworm','sh','-c','PGPASSWORD="$POSTGRES_PASSWORD" pg_restore --exit-on-error --no-owner --no-privileges --host=postgres --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" /backup/postgresql.dump')
    Archive-Restore 'media.tar.gz' $mediaVolume; Archive-Restore 'private_inventory.tar.gz' $privateVolume
    Invoke-Docker @('run','--rm','--user','0:0','--mount',"type=volume,src=$mediaVolume,dst=/app/media",'--mount',"type=volume,src=$privateVolume,dst=/app/private_inventory_price_import_sources",$image,'sh','-c','chown -R 10001:10001 /app/media /app/private_inventory_price_import_sources && chmod 0755 /app/media /app/private_inventory_price_import_sources')
    Invoke-Docker @('run','--rm','--network',$network,'--env-file',$drillEnv,'--mount',"type=volume,src=$staticVolume,dst=/app/staticfiles",$image,'python','manage.py','collectstatic','--noinput')
    Invoke-Docker @('run','-d','--name',$web,'--network',$network,'--env-file',$drillEnv,'--mount',"type=volume,src=$mediaVolume,dst=/app/media",'--mount',"type=volume,src=$privateVolume,dst=/app/private_inventory_price_import_sources",'--mount',"type=volume,src=$staticVolume,dst=/app/staticfiles,readonly",'--mount',"type=bind,src=$backupSet,dst=/backup,readonly",$image,'gunicorn','--bind=0.0.0.0:8000','--workers=1','--timeout=60','TileCompany.wsgi:application') | Out-Null
    Start-Sleep 3
    Invoke-Docker @('exec',$web,'python','manage.py','check'); Invoke-Docker @('exec',$web,'python','manage.py','migrate','--check'); Invoke-Docker @('exec',$web,'python','ops/recovery_verify.py','--expected-manifest','/backup/manifest.json')
    $privateStorageProof = @'
from django.core.files.base import ContentFile
from products.storage import private_inventory_import_storage

name = private_inventory_import_storage.save("recovery-drill-marker.txt", ContentFile(b"recovery-drill"))
try:
    if private_inventory_import_storage.open(name, "rb").read() != b"recovery-drill":
        raise SystemExit("private storage read proof failed")
    try:
        private_inventory_import_storage.url(name)
    except ValueError:
        pass
    else:
        raise SystemExit("private storage unexpectedly generated a URL")
finally:
    private_inventory_import_storage.delete(name)
if private_inventory_import_storage.exists(name):
    raise SystemExit("private storage marker cleanup failed")
'@
    Invoke-Docker @('exec',$web,'python','manage.py','shell','-c',$privateStorageProof)
    $smoke = @'
import http.client
for path in ("/_health/ready/", "/en/", "/en/shop/", "/en/articles/", "/en/accounts/login/"):
    connection = http.client.HTTPConnection("127.0.0.1", 8000, timeout=5)
    connection.request("GET", path, headers={"Host": "tilecompany.localhost", "X-Forwarded-Proto": "https"})
    response = connection.getresponse()
    response.read()
    connection.close()
    if response.status not in (200, 302):
        raise SystemExit(f"unexpected status for {path}: {response.status}")
'@
    Invoke-Docker @('exec',$web,'python','-c',$smoke)
    $cleanup = $true
    Write-Output "Recovery drill passed: $project"
}
finally {
    if ($cleanup) {
        foreach ($container in @($web,$postgres,$redis)) { & docker rm -f $container 2>$null | Out-Null }
        & docker network rm $network 2>$null | Out-Null
        foreach ($volume in @($postgresVolume,$redisVolume,$mediaVolume,$privateVolume,$staticVolume)) { & docker volume rm $volume 2>$null | Out-Null }
        if ($DeleteBackupAfterSuccess) { Remove-Item -LiteralPath $backupSet -Recurse -Force }
    } elseif (-not $KeepFailedDrill) { Write-Warning "Drill resources retained for diagnosis: $project" }
    if (Test-Path -LiteralPath $scratch) { Remove-Item -LiteralPath $scratch -Recurse -Force }
}
