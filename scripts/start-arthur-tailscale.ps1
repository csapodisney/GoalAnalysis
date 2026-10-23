[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Hostname,
    [Parameter(Mandatory = $true)][string]$AllowedLogin,
    [int]$HttpsPort = 8443,
    [int]$GatewayPort = 8770,
    [int]$UpstreamPort = 8765
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$gatewayPath = Join-Path $PSScriptRoot 'arthur-tailscale-gateway.py'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'The existing Arthur Python environment is missing.'
}
if (-not (Test-Path -LiteralPath $gatewayPath -PathType Leaf)) {
    throw 'The Arthur Tailscale gateway is missing.'
}

# Transport only: do not load secrets, start Arthur jobs, or change its settings.
$logDirectory = Join-Path $projectRoot 'logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8 = '1'
$gatewayArguments = @(
    '-B', $gatewayPath,
    '--hostname', $Hostname,
    '--allowed-login', $AllowedLogin,
    '--https-port', $HttpsPort,
    '--port', $GatewayPort,
    '--upstream-port', $UpstreamPort
)
$ErrorActionPreference = 'Continue'
& $pythonPath @gatewayArguments `
    1>> (Join-Path $logDirectory 'arthur-tailscale-stdout.log') `
    2>> (Join-Path $logDirectory 'arthur-tailscale-stderr.log')
exit $LASTEXITCODE
