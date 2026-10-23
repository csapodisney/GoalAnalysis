[CmdletBinding()]
param([string]$KeyFile = "")

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "arthur-secrets.ps1")
if ([string]::IsNullOrWhiteSpace($KeyFile)) {
    $KeyFile = Join-Path $ProjectRoot "TheStatsAPI.txt"
}
try {
    $StatsKeyValue = [System.IO.File]::ReadAllText((Resolve-Path -LiteralPath $KeyFile).ProviderPath).Trim()
    if ($StatsKeyValue -notmatch '^[A-Za-z0-9_.-]{20,512}$') { throw "Invalid key file." }
    $SecureStatsKey = ConvertTo-SecureString -String $StatsKeyValue -AsPlainText -Force
    Save-ArthurSecretValue -ProjectRoot $ProjectRoot -EnvironmentName "THE_STATS_API_KEY" -SecureValue $SecureStatsKey
    $VerifiedStatsKey = Import-ArthurSecret -ProjectRoot $ProjectRoot -EnvironmentName "THE_STATS_API_KEY" -SavedOnly
    if ($VerifiedStatsKey -cne $StatsKeyValue) { throw "Credential verification failed." }
    Write-Output "TheStatsAPI credential saved and verified with Windows-user encryption."
} catch {
    Write-Output "TheStatsAPI credential import failed; no credential content was logged."
    exit 1
} finally {
    $StatsKeyValue = $null
    $VerifiedStatsKey = $null
    $SecureStatsKey = $null
    Remove-Item Env:THE_STATS_API_KEY -ErrorAction SilentlyContinue
}
