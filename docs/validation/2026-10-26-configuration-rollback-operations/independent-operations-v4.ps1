$ErrorActionPreference = 'Stop'
$ArthurRoot = 'C:\AI-Work\GoalAnalysis'
$ArthurOutput = $PSScriptRoot
$ArthurExit = 1
$ArthurClaimed = $false
$ArthurStart = Join-Path $ArthurOutput 'independent-operations-v4-start.json'
try {
    if (Test-Path -LiteralPath $ArthurStart) { throw 'Worker already attempted' }
    $ArthurChain = @()
    $ArthurAncestorId = $PID
    for ($ArthurIndex = 0; $ArthurIndex -lt 8 -and $ArthurAncestorId -gt 0; $ArthurIndex++) {
        $ArthurAncestor = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $ArthurAncestorId)
        if (-not $ArthurAncestor) { break }
        $ArthurChain += @{pid=[int]$ArthurAncestor.ProcessId;parent=[int]$ArthurAncestor.ParentProcessId;name=$ArthurAncestor.Name;created=$ArthurAncestor.CreationDate.ToUniversalTime().ToString('o')}
        if ($ArthurAncestor.Name -match '^Codex(?:\.exe)?$') { throw 'Worker still descends from Codex' }
        if ($ArthurAncestor.ParentProcessId -eq $ArthurAncestorId) { break }
        $ArthurAncestorId = [int]$ArthurAncestor.ParentProcessId
    }
    if (-not ($ArthurChain | Where-Object { $_.name -in @('svchost.exe','taskeng.exe','taskhostw.exe') })) { throw 'Task Scheduler service ancestry not witnessed' }
    $ArthurStartJson = @{at=[DateTime]::UtcNow.ToString('o');task='11.7';pid=$PID;ancestry=$ArthurChain;outside_codex_ancestry=$true;automatic_retry=$false} | ConvertTo-Json -Depth 5
    $ArthurClaimStream = [IO.File]::Open($ArthurStart, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    $ArthurClaimed = $true
    try {
        $ArthurBytes = [Text.Encoding]::UTF8.GetBytes($ArthurStartJson + [Environment]::NewLine)
        $ArthurClaimStream.Write($ArthurBytes, 0, $ArthurBytes.Length)
    } finally { $ArthurClaimStream.Dispose() }
    $env:PYTHONUTF8 = '1'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:ARTHUR_CONFIGURATION_ROLLBACK_OPERATIONS_V4_SCHEDULED_WORKER = '1'
    Set-Location -LiteralPath $ArthurRoot
    $ArthurPreviousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & (Join-Path $ArthurRoot '.venv\Scripts\python.exe') (Join-Path $ArthurOutput 'operations_v4_once.py') *> (Join-Path $ArthurOutput 'independent-operations-v4.log')
        $ArthurExit = $LASTEXITCODE
        if ($null -eq $ArthurExit) { $ArthurExit = 1 }
    } finally { $ErrorActionPreference = $ArthurPreviousPreference }
} catch {
    if ($ArthurClaimed) {
        $_.Exception.GetType().FullName | Set-Content -LiteralPath (Join-Path $ArthurOutput 'independent-operations-v4-error.txt') -Encoding UTF8
    }
} finally {
    if ($ArthurClaimed) {
        @{at=[DateTime]::UtcNow.ToString('o');task='11.7';exit_code=$ArthurExit;automatic_retry=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ArthurOutput 'independent-operations-v4-outcome.json') -Encoding UTF8
    }
}
exit $ArthurExit
