"""Exercise the login guard using disposable roots and mocked network probes.

Never import Arthur, inspect a real port, or invoke the production launcher.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time

import pytest


GUARD = Path(__file__).resolve().parents[1] / "scripts" / "start-arthur-on-login.ps1"
POWERSHELL = (
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32/WindowsPowerShell/v1.0/powershell.exe"
)
pytestmark = pytest.mark.skipif(
    os.name != "nt" or not POWERSHELL.is_file(), reason="Windows PowerShell 5.1 required"
)


def ps_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def fake_root(tmp_path: Path, body: str | None = "exit 0") -> Path:
    root = tmp_path / "fake Arthur project"
    (root / "scripts").mkdir(parents=True)
    if body is not None:
        (root / "scripts/start-arthur.ps1").write_text(
            "param([switch]$NoBrowser)\n"
            "$ErrorActionPreference = 'Stop'\n"
            "Add-Content -LiteralPath (Join-Path $PSScriptRoot '../called.txt') "
            "-Value $NoBrowser.IsPresent\n" + body,
            encoding="utf-8",
        )
    return root


def command(root: Path, tmp_path: Path, probe: str = "", health: str = "") -> list[str]:
    wrapper = tmp_path / f"wrapper-{time.monotonic_ns()}.ps1"
    wrapper.write_text(
        "function Get-NetTCPConnection {\n"
        "  param($State, $ErrorAction)\n" + probe + "\n}\n"
        "function Invoke-WebRequest {\n"
        "  param($Uri, [switch]$UseBasicParsing, $TimeoutSec, "
        "$MaximumRedirection, $ErrorAction)\n" + health + "\n}\n"
        f"& {ps_string(GUARD)} -ProjectRoot {ps_string(root)}\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    return [
        str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(wrapper),
    ]


def run_guard(root: Path, tmp_path: Path, **kwargs: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command(root, tmp_path, **kwargs), capture_output=True, text=True, timeout=20,
        check=False,
    )


def log(root: Path) -> str:
    return (root / "logs/arthur-dashboard-startup.log").read_text(encoding="utf-8-sig")


LISTENER = "[pscustomobject]@{ LocalAddress = '127.0.0.1'; LocalPort = 8765 }"
HEALTHY = (
    "[pscustomobject]@{ StatusCode = 200; Headers = @{ Server = 'Arthur/1.0' }; "
    "Content = '<html><title>Arthur dashboard</title></html>' }"
)


def test_existing_healthy_dashboard_does_not_load_launcher(tmp_path: Path) -> None:
    root = fake_root(tmp_path, "throw 'must never load credentials'")
    result = run_guard(root, tmp_path, probe=LISTENER, health=HEALTHY)
    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert not (root / "called.txt").exists()
    assert "event=already_running exit=0" in log(root)


@pytest.mark.parametrize(
    ("probe", "health", "event"),
    [
        (LISTENER, HEALTHY.replace("Arthur/1.0", "Unknown/1.0"), "health_failed"),
        (LISTENER, HEALTHY.replace("StatusCode = 200", "StatusCode = 503"), "health_failed"),
        (LISTENER, HEALTHY.replace("Arthur dashboard", "Other dashboard"), "health_failed"),
        (LISTENER, "throw 'PROBE_SECRET_SENTINEL'", "health_failed"),
        (LISTENER.replace("127.0.0.1", "0.0.0.0"), HEALTHY, "port_conflict"),
        (LISTENER + "\n" + LISTENER.replace("127.0.0.1", "::"), HEALTHY, "port_conflict"),
        ("throw 'LISTENER_SECRET_SENTINEL'", HEALTHY, "startup_failed"),
    ],
)
def test_occupied_or_uninspectable_port_fails_closed(
    tmp_path: Path, probe: str, health: str, event: str,
) -> None:
    root = fake_root(tmp_path, "throw 'must never load credentials'")
    result = run_guard(root, tmp_path, probe=probe, health=health)
    assert result.returncode == 1
    assert result.stdout == result.stderr == ""
    assert not (root / "called.txt").exists()
    assert f"event={event} exit=1" in log(root)
    assert "SENTINEL" not in log(root)


def test_missing_launcher_is_reported(tmp_path: Path) -> None:
    root = fake_root(tmp_path, None)
    result = run_guard(root, tmp_path)
    assert result.returncode == 1
    assert result.stdout == result.stderr == ""
    assert "event=launcher_missing exit=1" in log(root)


def test_file_invocation_resolves_default_root_after_parameter_binding(tmp_path: Path) -> None:
    root = fake_root(tmp_path)
    # Instrument only a disposable guard copy, then exercise the actual -File
    # default-root path with no argument and no access to real network cmdlets.
    copied_guard = root / "scripts/start-arthur-on-login.ps1"
    copied_guard.write_text(
        GUARD.read_text(encoding="utf-8").replace(
            "# This guard",
            "function Get-NetTCPConnection { param($State, $ErrorAction) }\n"
            "function Invoke-WebRequest { throw 'unexpected health probe' }\n"
            "# This guard",
            1,
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(copied_guard)],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert (root / "called.txt").read_text(encoding="utf-8-sig").splitlines() == ["True"]
    assert "event=stopped exit=0" in log(root)


@pytest.mark.parametrize("exit_code", [0, 37])
def test_starts_once_without_browser_and_preserves_exit_code(
    tmp_path: Path, exit_code: int,
) -> None:
    root = fake_root(tmp_path, f"exit {exit_code}")
    result = run_guard(root, tmp_path, probe=LISTENER.replace("8765", "9876"))
    assert result.returncode == exit_code
    assert result.stdout == result.stderr == ""
    assert (root / "called.txt").read_text(encoding="utf-8-sig").splitlines() == ["True"]
    assert "event=starting exit=0" in log(root)
    assert f"event=stopped exit={exit_code}" in log(root)


def test_child_streams_console_and_native_stderr_are_never_exposed(tmp_path: Path) -> None:
    sentinel = "PRIVATE_KEY_SECRET_SENTINEL"
    root = fake_root(
        tmp_path,
        "\n".join(
            [
                f"Write-Output '{sentinel}'",
                f"Write-Warning '{sentinel}'",
                f"Write-Verbose '{sentinel}' -Verbose",
                "$DebugPreference = 'Continue'",
                f"Write-Debug '{sentinel}'",
                f"Write-Information '{sentinel}' -InformationAction Continue",
                f"Write-Error '{sentinel}' -ErrorAction Continue",
                f"Write-Host '{sentinel}'",
                f"[Console]::WriteLine('{sentinel}')",
                f"[Console]::Error.WriteLine('{sentinel}')",
                f"& cmd.exe /d /c 'echo {sentinel} 1>&2'",
                "exit 0",
            ]
        ),
    )
    result = run_guard(root, tmp_path)
    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert sentinel not in log(root)
    assert "event=stopped exit=0" in log(root)


def test_terminating_child_exception_is_suppressed(tmp_path: Path) -> None:
    root = fake_root(tmp_path, "throw 'CHILD_EXCEPTION_SECRET_SENTINEL'")
    result = run_guard(root, tmp_path)
    assert result.returncode == 1
    assert result.stdout == result.stderr == ""
    assert "SENTINEL" not in log(root)
    assert "event=stopped exit=1" in log(root)


def test_duplicate_invocation_returns_without_launching_second_child(tmp_path: Path) -> None:
    root = fake_root(
        tmp_path,
        "$Deadline = [DateTime]::UtcNow.AddSeconds(12)\n"
        "while (-not (Test-Path (Join-Path $PSScriptRoot '../release.txt'))) {\n"
        "  if ([DateTime]::UtcNow -gt $Deadline) { exit 9 }\n"
        "  Start-Sleep -Milliseconds 50\n"
        "}\nexit 0",
    )
    first = subprocess.Popen(
        command(root, tmp_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 8
        while not (root / "called.txt").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (root / "called.txt").exists(), first.communicate(timeout=1)
        second = run_guard(root, tmp_path, probe="throw 'duplicate must not inspect port'")
        assert second.returncode == 0
        assert second.stdout == second.stderr == ""
        assert "event=duplicate_start exit=0" in log(root)
    finally:
        (root / "release.txt").touch()
        stdout, stderr = first.communicate(timeout=8)
    assert first.returncode == 0
    assert stdout == stderr == ""
    assert (root / "called.txt").read_text(encoding="utf-8-sig").splitlines() == ["True"]
