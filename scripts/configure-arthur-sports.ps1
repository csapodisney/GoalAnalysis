[CmdletBinding()]
param(
    [ValidateSet("API_FOOTBALL_KEY", "THE_ODDS_API_KEY")]
    [string]$Name = "THE_ODDS_API_KEY",
    [switch]$Replace,
    [switch]$Launch
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "arthur-secrets.ps1")

$NeedsInput = [bool]$Replace
if (-not $NeedsInput) {
    try {
        $Existing = Import-ArthurSecret -ProjectRoot $ProjectRoot -EnvironmentName $Name -SavedOnly
        [Environment]::SetEnvironmentVariable($Name, $Existing, "Process")
        Write-Host "Saved $Name loaded successfully for this Windows user."
    } catch {
        $NeedsInput = $true
    }
}
if ($NeedsInput) {
    Write-Host "Only $Name needs to be entered. Paste it into the hidden prompt and press Enter."
    $SecureValue = Read-Host "$Name (hidden input)" -AsSecureString
    Save-ArthurSecretValue -ProjectRoot $ProjectRoot -EnvironmentName $Name -SecureValue $SecureValue
    Write-Host "$Name saved with Windows user encryption; disk read-back verified."
}
Write-Host "No key was printed. No network request or ChatGPT login was made."
if ($Launch) {
    & (Join-Path $PSScriptRoot "start-arthur.ps1")
}
