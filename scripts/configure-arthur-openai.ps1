# Compatibility entry point: Arthur now uses ChatGPT login through Codex.
[CmdletBinding()]
param([switch]$Replace, [switch]$SkipCheck)
$ErrorActionPreference = "Stop"
Write-Host "Arthur uses your ChatGPT/Codex allowance. No OpenAI API key is requested."
& (Join-Path $PSScriptRoot "configure-arthur-codex.ps1") -SkipCheck:$SkipCheck
