from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .kerekasztal import KerekasztalError


@dataclass(frozen=True, slots=True)
class MasterPromptArtifact:
    version: str
    text: str
    sha256: str
    size_bytes: int
    activation_status: str
    source_filename: str


def load_master_prompt(root: Path, version: str) -> MasterPromptArtifact:
    """Load an immutable master prompt only when bytes and contract anchors match."""

    directory = Path(root) / version
    manifest = _load_manifest(directory / "manifest.json")
    if manifest.get("prompt_version") != version:
        raise KerekasztalError("master prompt version does not match its directory")

    master_path = directory / str(manifest.get("master_file", "master.md"))
    try:
        raw = master_path.read_bytes()
    except OSError as error:
        raise KerekasztalError(f"cannot load master prompt: {master_path}") from error

    digest = hashlib.sha256(raw).hexdigest()
    if digest != manifest.get("sha256"):
        raise KerekasztalError("master prompt SHA-256 mismatch")
    if len(raw) != manifest.get("size_bytes"):
        raise KerekasztalError("master prompt byte size mismatch")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise KerekasztalError("master prompt must be UTF-8") from error

    missing = [anchor for anchor in manifest.get("required_anchors", []) if anchor not in text]
    if missing:
        raise KerekasztalError(f"master prompt contract anchor missing: {missing[0]}")

    return MasterPromptArtifact(
        version=version,
        text=text,
        sha256=digest,
        size_bytes=len(raw),
        activation_status=str(manifest.get("activation_status")),
        source_filename=str(manifest.get("source_filename")),
    )


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise KerekasztalError(f"cannot load master prompt manifest: {path}") from error
    if not isinstance(payload, dict):
        raise KerekasztalError("master prompt manifest must be an object")
    return payload
