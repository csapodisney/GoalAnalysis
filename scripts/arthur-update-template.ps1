# Generated release updater. Payload and hashes are inserted by build-arthur-update.py.
[CmdletBinding()]
param(
    [string]$ProjectRoot = "C:\AI-Work\GoalAnalysis",
    [switch]$NoLaunch
)
$ErrorActionPreference = "Stop"
$PayloadHash = "@@PAYLOAD_SHA256@@"
$PayloadBase64 = @'
@@PAYLOAD_BASE64@@
'@
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "pyproject.toml") -PathType Leaf)) {
    throw "Existing GoalAnalysis project not found: $ProjectRoot. Use -ProjectRoot with its actual path."
}
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\', '/')
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) ("arthur-update-" + [guid]::NewGuid().ToString("N"))
$BackupRoot = Join-Path $ProjectRoot ("backups\arthur-v3.5-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [guid]::NewGuid().ToString("N").Substring(0, 6))
$Changed = New-Object System.Collections.Generic.List[object]
$RunLock = $null
try {
    New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null
    $ZipPath = Join-Path $TempRoot "release.zip"
    [IO.File]::WriteAllBytes($ZipPath, [Convert]::FromBase64String($PayloadBase64))
    if ((Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash -ne $PayloadHash) {
        throw "Release payload verification failed. No project files were changed."
    }
    $Unpacked = Join-Path $TempRoot "release"
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $Unpacked
    $Manifest = Get-Content -LiteralPath (Join-Path $Unpacked "release-manifest.json") -Raw | ConvertFrom-Json
    $Files = @($Manifest.files.PSObject.Properties)
    foreach ($Entry in $Files) {
        $Relative = $Entry.Name
        if ($Relative -match '(^/|^[A-Za-z]:|\\|(^|/)\.\.(/|$)|^(data|reports|logs|backups|\.git|\.venv)/|^\.env$|^config/(arthur-settings|daily223-live)\.json$)') {
            throw "Invalid release path: $Relative"
        }
        $Source = Join-Path $Unpacked $Relative
        if ((Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash -ne $Entry.Value) {
            throw "Release file verification failed: $Relative. No project files were changed."
        }
    }
    $LockDirectory = Join-Path $ProjectRoot "data\arthur"
    New-Item -ItemType Directory -Path $LockDirectory -Force | Out-Null
    try {
        $RunLock = [IO.File]::Open((Join-Path $LockDirectory "run.lock"), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch {
        throw "Arthur is running an analysis. Wait for it to finish, close its server window and retry."
    }
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    foreach ($Entry in $Files) {
        $Destination = Join-Path $ProjectRoot $Entry.Name
        $Saved = Join-Path $BackupRoot $Entry.Name
        $Existed = Test-Path -LiteralPath $Destination -PathType Leaf
        if ($Existed) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $Saved) -Force | Out-Null
            Copy-Item -LiteralPath $Destination -Destination $Saved
        }
        $Changed.Add([pscustomobject]@{Destination=$Destination; Saved=$Saved; Existed=$Existed})
        New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $Unpacked $Entry.Name) -Destination $Destination -Force
        if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -ne $Entry.Value) {
            throw "Installed file verification failed: $($Entry.Name)"
        }
    }
    Copy-Item -LiteralPath (Join-Path $Unpacked "release-manifest.json") -Destination (Join-Path $BackupRoot "installed-manifest.json")
    Write-Host "Arthur v3.5 installed. Files verified: $($Files.Count). Backup: $BackupRoot"
    Write-Host "Start a NEW analysis: all enabled profiles, automatic Astra review, and persistent results. Existing data and login are preserved."
} catch {
    $Failure = $_
    $RestoreFailures = @()
    for ($Index = $Changed.Count - 1; $Index -ge 0; $Index--) {
        $Item = $Changed[$Index]
        try {
            if ($Item.Existed) { Copy-Item -LiteralPath $Item.Saved -Destination $Item.Destination -Force }
            elseif (Test-Path -LiteralPath $Item.Destination -PathType Leaf) { Remove-Item -LiteralPath $Item.Destination }
        } catch { $RestoreFailures += $Item.Destination }
    }
    if ($RestoreFailures.Count -gt 0) { Write-Warning "Some files could not be restored. Use backup: $BackupRoot" }
    throw $Failure
} finally {
    if ($null -ne $RunLock) { $RunLock.Dispose() }
    if (Test-Path -LiteralPath $TempRoot) { Remove-Item -LiteralPath $TempRoot -Recurse -Force }
}
if (-not $NoLaunch) {
    & (Join-Path $ProjectRoot "scripts\start-arthur.ps1")
}
