import hashlib
import json
from pathlib import Path

import pytest

from goal_analysis.agents import KerekasztalError, load_master_prompt

ROOT = Path("config/prompts")
VERSION = "arthur-pentagram-v2"
EXPECTED_SHA256 = "f29de06a751fc1283343083771d2877ef512e0b73be28c3de6891d641ae72fe8"


def test_master_prompt_is_exact_hash_locked_source() -> None:
    artifact = load_master_prompt(ROOT, VERSION)

    assert artifact.sha256 == EXPECTED_SHA256
    assert artifact.size_bytes == 61565
    assert artifact.activation_status == "imported_not_activated"
    assert artifact.source_filename == "Arthur_Pentagram_Master_Prompt_v2 (1).md"


def test_master_prompt_contains_required_operating_contract() -> None:
    artifact = load_master_prompt(ROOT, VERSION)

    assert "PREMATCH | FINALIZE | AUDIT" in artifact.text
    assert "A szorzó önmagában soha nem lehet kiválasztási ok." in artifact.text
    assert "## 7. VETO-, hiány- és státuszlogika" in artifact.text


def test_modified_master_prompt_fails_closed(tmp_path) -> None:
    source = ROOT / VERSION
    destination = tmp_path / VERSION
    destination.mkdir()
    (destination / "manifest.json").write_bytes((source / "manifest.json").read_bytes())
    raw = (source / "master.md").read_bytes() + b"\nmodified"
    (destination / "master.md").write_bytes(raw)

    with pytest.raises(KerekasztalError, match="SHA-256 mismatch"):
        load_master_prompt(tmp_path, VERSION)


def test_missing_contract_anchor_fails_even_with_matching_hash(tmp_path) -> None:
    source = ROOT / VERSION
    destination = tmp_path / VERSION
    destination.mkdir()
    raw = (source / "master.md").read_bytes().replace(b"FUT\xc3\x81S_BEMENET", b"REMOVED_INPUT")
    manifest = json.loads((source / "manifest.json").read_text("utf-8"))
    manifest["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest["size_bytes"] = len(raw)
    (destination / "manifest.json").write_text(json.dumps(manifest), "utf-8")
    (destination / "master.md").write_bytes(raw)

    with pytest.raises(KerekasztalError, match="contract anchor missing"):
        load_master_prompt(tmp_path, VERSION)
