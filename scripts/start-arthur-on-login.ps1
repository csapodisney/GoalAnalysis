[CmdletBinding()]
param([string]$ProjectRoot = "")

# This guard never imports credentials or starts analysis. The existing launcher
# remains responsible for starting the dashboard and loading its saved keys.
$ErrorActionPreference = "Stop"

function Write-StartupEvent {
    param([string]$EventCode, [int]$ExitNumber)
    $LogDirectory = Join-Path $script:StartupRoot "logs"
    [void][System.IO.Directory]::CreateDirectory($LogDirectory)
    $Line = "{0} event={1} exit={2}{3}" -f `
        [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ"), `
        $EventCode, $ExitNumber, [Environment]::NewLine
    [System.IO.File]::AppendAllText(
        (Join-Path $LogDirectory "arthur-dashboard-startup.log"), $Line)
}

function Invoke-ArthurStartup {
    $Mutex = $null
    $OwnsMutex = $false
    try {
        # Resolve after parameter binding for Windows PowerShell 5.1 -File.
        if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
            $ProjectRoot = Split-Path -Parent $PSScriptRoot
        }
        $ResolvedRoot = Resolve-Path -LiteralPath $ProjectRoot -ErrorAction Stop
        if ($ResolvedRoot.Provider.Name -ne "FileSystem" -or
            -not (Test-Path -LiteralPath $ResolvedRoot.ProviderPath -PathType Container)) {
            return 1
        }
        $script:StartupRoot = $ResolvedRoot.ProviderPath
        $Identity = $script:StartupRoot.TrimEnd('\').ToUpperInvariant() + "|8765"
        $HashAlgorithm = [System.Security.Cryptography.SHA256]::Create()
        try {
            $Hash = [BitConverter]::ToString($HashAlgorithm.ComputeHash(
                [Text.Encoding]::UTF8.GetBytes($Identity))).Replace("-", "")
        } finally {
            $HashAlgorithm.Dispose()
        }
        $Mutex = New-Object System.Threading.Mutex($false, "Local\ArthurDashboardStartup_$Hash")
        try {
            $OwnsMutex = $Mutex.WaitOne(0)
        } catch [System.Threading.AbandonedMutexException] {
            $OwnsMutex = $true
        }
        if (-not $OwnsMutex) {
            Write-StartupEvent "duplicate_start" 0
            return 0
        }

        # Enumerate first: a filtered Get-NetTCPConnection query raises an error
        # for the normal case where this port has no listener.
        $Listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
            Where-Object { $_.LocalPort -eq 8765 })
        if ($Listeners.Count -gt 0) {
            if (@($Listeners | Where-Object { $_.LocalAddress -ne "127.0.0.1" }).Count -gt 0) {
                Write-StartupEvent "port_conflict" 1
                return 1
            }
            try {
                $Response = Invoke-WebRequest -Uri "http://127.0.0.1:8765/" `
                    -UseBasicParsing -TimeoutSec 3 -MaximumRedirection 0 -ErrorAction Stop
                $Healthy = [int]$Response.StatusCode -eq 200 -and
                    [string]$Response.Headers["Server"] -match '^Arthur/' -and
                    [string]$Response.Content -match '(?is)<title\b[^>]*>[^<]*Arthur[^<]*</title>'
            } catch {
                $Healthy = $false
            }
            if ($Healthy) {
                Write-StartupEvent "already_running" 0
                return 0
            }
            Write-StartupEvent "health_failed" 1
            return 1
        }

        $Launcher = Join-Path $script:StartupRoot "scripts\start-arthur.ps1"
        if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
            Write-StartupEvent "launcher_missing" 1
            return 1
        }
        Write-StartupEvent "starting" 0
        $PreviousErrorPreference = $ErrorActionPreference
        try {
            # A separate PowerShell process also captures direct console writes.
            # Continue prevents captured native stderr from aborting PS 5.1.
            $ErrorActionPreference = "Continue"
            & (Join-Path $PSHOME "powershell.exe") -NoProfile -NonInteractive `
                -WindowStyle Hidden -ExecutionPolicy Bypass -File $Launcher -NoBrowser *> $null
            $ChildExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $PreviousErrorPreference
        }
        if ($null -eq $ChildExitCode) { $ChildExitCode = 1 }
        Write-StartupEvent "stopped" ([int]$ChildExitCode)
        return [int]$ChildExitCode
    } catch {
        # Only fixed event codes are logged; child output and exception details
        # may contain credentials and must never reach this task's output/log.
        if ($script:StartupRoot) {
            try { Write-StartupEvent "startup_failed" 1 } catch { }
        }
        return 1
    } finally {
        if ($OwnsMutex) { $Mutex.ReleaseMutex() }
        if ($null -ne $Mutex) { $Mutex.Dispose() }
    }
}

exit (Invoke-ArthurStartup)
