[CmdletBinding()]
param([string]$KeyFile = "", [switch]$RemoveSourceAfterImport)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "arthur-secrets.ps1")
if ([string]::IsNullOrWhiteSpace($KeyFile)) {
    $KeyFile = Join-Path $ProjectRoot "Grok_API.txt"
}
try {
    $ResolvedKeyFile = (Resolve-Path -LiteralPath $KeyFile -ErrorAction Stop).ProviderPath
    $RawKeyText = [System.IO.File]::ReadAllText($ResolvedKeyFile).Trim()
    $KeyMatches = [regex]::Matches($RawKeyText, 'xai-[A-Za-z0-9_-]{20,}')
    if ($KeyMatches.Count -ne 1) { throw "Invalid key file." }
    $GrokKeyValue = $KeyMatches[0].Value
    $SecureGrokKey = ConvertTo-SecureString -String $GrokKeyValue -AsPlainText -Force
    Save-ArthurSecretValue -ProjectRoot $ProjectRoot -EnvironmentName "XAI_API_KEY" -SecureValue $SecureGrokKey
    $VerifiedGrokKey = Import-ArthurSecret -ProjectRoot $ProjectRoot -EnvironmentName "XAI_API_KEY" -SavedOnly
    if ($VerifiedGrokKey -cne $GrokKeyValue) { throw "Credential verification failed." }
    if ($RemoveSourceAfterImport) {
        # Only remove the exact project-root handoff file, after verified DPAPI storage.
        $ExpectedKeyFile = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "Grok_API.txt"))
        if (-not [string]::Equals($ResolvedKeyFile, $ExpectedKeyFile, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Source removal is limited to the project-root Grok_API.txt."
        }
        Remove-Item -LiteralPath $ResolvedKeyFile -Force -ErrorAction Stop
    }
    Write-Output "Grok credential saved and verified with Windows-user encryption."
} catch {
    Write-Output "Grok credential import failed; no credential content was logged."
    exit 1
} finally {
    $RawKeyText = $null
    $GrokKeyValue = $null
    $VerifiedGrokKey = $null
    $SecureGrokKey = $null
    Remove-Item Env:XAI_API_KEY -ErrorAction SilentlyContinue
}
