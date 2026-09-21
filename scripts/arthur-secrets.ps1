# Shared Windows-user DPAPI storage. Importing never rewrites a saved key.

function Import-ArthurSecret {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)]
        [ValidateSet("API_FOOTBALL_KEY", "THE_ODDS_API_KEY")][string]$EnvironmentName,
        [switch]$SavedOnly
    )
    if (-not $SavedOnly) {
        $CurrentValue = [Environment]::GetEnvironmentVariable($EnvironmentName, "Process")
        if (-not [string]::IsNullOrWhiteSpace($CurrentValue)) { return $CurrentValue.Trim() }
    }
    $SecretPath = Join-Path $ProjectRoot "data\secrets\$EnvironmentName.txt"
    if (-not (Test-Path -LiteralPath $SecretPath -PathType Leaf)) {
        throw "Missing saved key: $EnvironmentName."
    }
    try {
        # Legacy Set-Content appended CR/LF. It is not part of the DPAPI blob.
        $EncryptedValue = (Get-Content -Raw -LiteralPath $SecretPath -ErrorAction Stop).Trim()
        $SecureValue = ConvertTo-SecureString -String $EncryptedValue -ErrorAction Stop
        $PlainValue = [System.Net.NetworkCredential]::new("", $SecureValue).Password.Trim()
        if ([string]::IsNullOrWhiteSpace($PlainValue)) { throw "Empty credential." }
        return $PlainValue
    } catch {
        throw "Cannot read the saved $EnvironmentName for this Windows user."
    }
}

function Save-ArthurSecretValue {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)]
        [ValidateSet("API_FOOTBALL_KEY", "THE_ODDS_API_KEY")][string]$EnvironmentName,
        [Parameter(Mandatory = $true)][System.Security.SecureString]$SecureValue
    )
    $PlainValue = [System.Net.NetworkCredential]::new("", $SecureValue).Password.Trim()
    if ([string]::IsNullOrWhiteSpace($PlainValue)) {
        throw "No value supplied for $EnvironmentName. The saved key has not been changed."
    }
    $SecretDirectory = Join-Path $ProjectRoot "data\secrets"
    $SecretPath = Join-Path $SecretDirectory "$EnvironmentName.txt"
    $TemporaryPath = Join-Path $SecretDirectory ([guid]::NewGuid().ToString("N") + ".tmp")
    try {
        New-Item -ItemType Directory -Path $SecretDirectory -Force -ErrorAction Stop | Out-Null
        $Normalized = ConvertTo-SecureString -String $PlainValue -AsPlainText -Force
        $EncryptedValue = ConvertFrom-SecureString -SecureString $Normalized -ErrorAction Stop
        Set-Content -LiteralPath $TemporaryPath -Value $EncryptedValue -Encoding ASCII -NoNewline -ErrorAction Stop
        # Verify the bytes written to disk before replacing the existing key.
        $Stored = (Get-Content -Raw -LiteralPath $TemporaryPath -ErrorAction Stop).Trim()
        $Verified = ConvertTo-SecureString -String $Stored -ErrorAction Stop
        $RoundTrip = [System.Net.NetworkCredential]::new("", $Verified).Password
        if ($RoundTrip -cne $PlainValue) { throw "Credential read-back failed." }
        if (Test-Path -LiteralPath $SecretPath -PathType Leaf) {
            [System.IO.File]::Replace($TemporaryPath, $SecretPath, $null)
        } else {
            [System.IO.File]::Move($TemporaryPath, $SecretPath)
        }
    } catch {
        throw "Could not save and verify $EnvironmentName. The previous saved key was preserved."
    } finally {
        if (Test-Path -LiteralPath $TemporaryPath) {
            Remove-Item -LiteralPath $TemporaryPath -Force -ErrorAction SilentlyContinue
        }
    }
    [Environment]::SetEnvironmentVariable($EnvironmentName, $PlainValue, "Process")
}
