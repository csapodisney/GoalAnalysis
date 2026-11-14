param(
    [Parameter(Mandatory=$true)][string]$Root,
    [Parameter(Mandatory=$true)][string]$TargetDate,
    [Parameter(Mandatory=$true)][string]$Settings,
    [Parameter(Mandatory=$true)][string]$Output,
    [string]$OwnerDeadlineAt
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Native = 1
try {
    $ResolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $ExpectedRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent)).TrimEnd('\')
    if ($ResolvedRoot -cne $ExpectedRoot) { throw 'Exact installed root required.' }
    if ($TargetDate -cnotmatch '^\d{4}-\d{2}-\d{2}$') { throw 'Exact target date required.' }
    $ExpectedOutput = [IO.Path]::GetFullPath($Output).TrimEnd('\')
    if ([IO.Path]::GetFullPath($Settings) -cne (Join-Path $ExpectedOutput 'settings.json')) { throw 'Owned settings required.' }
    $LegacyNames = @('Arthur Goal Analysis Daily', 'Arthur Goal Analysis Daily 223', 'Arthur Goal Analysis Daily223')
    $All = @(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop)
    $Observed = @()
    foreach ($Name in $LegacyNames) {
        $Found = @($All | Where-Object { $_.TaskName -ceq $Name })
        if ($Found.Count -gt 1 -or ($Found.Count -eq 1 -and $Found[0].State -ne 'Disabled')) { throw 'Daily and legacy must remain disabled.' }
        $Observed += @{ name=$Name; present=($Found.Count -eq 1); enabled=$false; observed_at=[DateTime]::UtcNow.ToString('o') }
    }
    $Bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Observed | ConvertTo-Json -Depth 5 -Compress))
    $Stream = [IO.File]::Open((Join-Path $ExpectedOutput 'legacy-off.json'), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try { $Stream.Write($Bytes,0,$Bytes.Length); $Stream.Flush($true) } finally { $Stream.Dispose() }
    . (Join-Path $ResolvedRoot 'scripts\arthur-secrets.ps1')
    foreach ($Name in @('THESTATSAPI_API_KEY','API_FOOTBALL_KEY','THE_ODDS_API_KEY')) {
        [Environment]::SetEnvironmentVariable($Name,$null,'Process')
    }
    foreach ($Name in @('THE_STATS_API_KEY','XAI_API_KEY')) {
        $Value = $null
        try {
            $Value = Import-ArthurSecret -ProjectRoot $ResolvedRoot -EnvironmentName $Name -SavedOnly
            [Environment]::SetEnvironmentVariable($Name,$Value,'Process')
        } catch { [Environment]::SetEnvironmentVariable($Name,$null,'Process') }
        finally { $Value = $null }
    }
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable('THE_STATS_API_KEY','Process'))) { throw 'Required saved credential unavailable.' }
    Set-Location -LiteralPath $ResolvedRoot
    $OwnerArguments = @()
    if (-not [string]::IsNullOrWhiteSpace($OwnerDeadlineAt)) {
        $OwnerArguments = @('--owner-deadline-at', $OwnerDeadlineAt)
    }
    & (Join-Path $ResolvedRoot '.venv\Scripts\python.exe') -B (Join-Path $ResolvedRoot 'scripts\run-arthur.py') --live --date $TargetDate --settings $Settings @OwnerArguments
    $Native = $LASTEXITCODE
    if ($null -eq $Native) { $Native = 1 }
} catch {
    # Private logs receive only a fixed safe reason, never exception text or a key.
    Write-Error 'Arthur saved-only bootstrap stopped.' -ErrorAction Continue
    $Native = 1
} finally {
    foreach ($Name in @('THE_STATS_API_KEY','XAI_API_KEY')) { [Environment]::SetEnvironmentVariable($Name,$null,'Process') }
    $Value = $null
}
exit [int]$Native
