"""Exercise actual Windows DPAPI with synthetic keys and isolated files."""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Requires real Windows user DPAPI")
def test_windows_legacy_line_endings_atomic_replacement_and_read_only_import(tmp_path):
    helper = Path(__file__).parents[1] / "scripts/arthur-secrets.ps1"
    script = tmp_path / "check-secrets.ps1"
    script.write_text(
        r"""
param([string]$Helper, [string]$TestRoot)
$ErrorActionPreference = "Stop"
. $Helper
$Name = "THE_ODDS_API_KEY"
$Plain = "arthur-synthetic-dpapi-test-key"
$Secure = ConvertTo-SecureString $Plain -AsPlainText -Force
Save-ArthurSecretValue -ProjectRoot $TestRoot -EnvironmentName $Name -SecureValue $Secure
$Path = Join-Path $TestRoot "data\secrets\THE_ODDS_API_KEY.txt"
$Encrypted = [System.IO.File]::ReadAllText($Path)
if ($Encrypted.Contains($Plain) -or $Encrypted.EndsWith("`n")) { throw "Invalid encrypted storage." }
$Loaded = Import-ArthurSecret -ProjectRoot $TestRoot -EnvironmentName $Name -SavedOnly
if ($Loaded -cne $Plain) { throw "Initial disk read-back failed." }
[System.IO.File]::AppendAllText($Path, "`r`n")
$Loaded = Import-ArthurSecret -ProjectRoot $TestRoot -EnvironmentName $Name -SavedOnly
if ($Loaded -cne $Plain) { throw "Legacy CRLF recovery failed." }
$Before = [System.IO.File]::ReadAllText($Path)
[Environment]::SetEnvironmentVariable($Name, "synthetic-session-override", "Process")
$Loaded = Import-ArthurSecret -ProjectRoot $TestRoot -EnvironmentName $Name
if ($Loaded -cne "synthetic-session-override" -or [System.IO.File]::ReadAllText($Path) -cne $Before) {
    throw "Import rewrote the stored credential."
}
$Rejected = $false
try {
    Save-ArthurSecretValue -ProjectRoot $TestRoot -EnvironmentName $Name -SecureValue ([System.Security.SecureString]::new())
} catch { $Rejected = $true }
if (-not $Rejected -or [System.IO.File]::ReadAllText($Path) -cne $Before) { throw "Empty input replaced a key." }
[System.IO.File]::WriteAllText($Path, "damaged-test-ciphertext")
$Rejected = $false
try { $Loaded = Import-ArthurSecret -ProjectRoot $TestRoot -EnvironmentName $Name -SavedOnly }
catch { $Rejected = $true }
if (-not $Rejected -or [System.IO.File]::ReadAllText($Path) -cne "damaged-test-ciphertext") { throw "Damaged storage was not preserved." }
Save-ArthurSecretValue -ProjectRoot $TestRoot -EnvironmentName $Name -SecureValue $Secure
$Loaded = Import-ArthurSecret -ProjectRoot $TestRoot -EnvironmentName $Name -SavedOnly
if ($Loaded -cne $Plain) { throw "Atomic replacement failed." }
if (@(Get-ChildItem -LiteralPath (Split-Path $Path) -Filter "*.tmp").Count) { throw "Temporary files remained." }
Write-Output "PASS"
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Helper",
            str(helper),
            "-TestRoot",
            str(tmp_path / "isolated"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "PASS"
    assert "arthur-synthetic-dpapi-test-key" not in result.stdout + result.stderr
