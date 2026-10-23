"""Version the actual strategy code and policy; never relabel old snapshots."""

from __future__ import annotations

import hashlib
from importlib import metadata
from pathlib import Path

from goal_analysis.agents import canonical_sha256

APPLICATION_VERSION = "3.11"
STRATEGY_DIRECTORIES = (
    "agents", "config", "engine", "features", "jobs", "models",
    "normalization", "providers", "storage", "team", "learning", "tipsters",
)


def strategy_manifest(settings: dict) -> dict:
    package = Path(__file__).resolve().parent
    paths = [package / "strategy_provenance.py"]
    for directory in STRATEGY_DIRECTORIES:
        paths.extend(sorted((package / directory).rglob("*.py")))
    paths.append(package / 'tipsters/profiles.json')
    source = {path.relative_to(package).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(paths)}
    prompts = package.parents[1] / "config/prompts/arthur-portfolio-v3"
    prompt_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(prompts.glob("*")) if path.is_file()
    }
    knowledge = package.parents[1] / 'config/weather-expert-knowledge.json'
    knowledge_hashes = ({knowledge.name: hashlib.sha256(knowledge.read_bytes()).hexdigest()}
                        if knowledge.exists() else {})
    versions = {}
    for name in ("penaltyblog", "catboost", "threadpoolctl"):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    code_hash = canonical_sha256(source)
    settings_hash = canonical_sha256(settings)
    base = {
        "schema_version": 1, "application_version": APPLICATION_VERSION,
        "code_sha256": code_hash, "source_files": source,
        "settings_sha256": settings_hash, "prompt_files": prompt_hashes,
        "libraries": versions,
        'expert_knowledge_files': knowledge_hashes,
    }
    identity = canonical_sha256(base)
    base["manifest_sha256"] = identity
    base["profiles"] = {
        profile: {"version_id": f"{profile}-v{APPLICATION_VERSION}-{identity[:16]}"}
        for profile in settings["enabled_profiles"]
    }
    return base


def attach_strategy_provenance(report: dict, settings: dict) -> dict:
    manifest = strategy_manifest(settings)
    report["strategy_manifest"] = manifest
    report["application_version"] = APPLICATION_VERSION
    for group in ("tickets", "preview_tickets", "recommendations"):
        for item in report.get(group, []):
            profile = manifest["profiles"].get(item.get("profile_id"))
            if profile:
                if item.get("strategy_version"):
                    item.setdefault("method_version", item["strategy_version"])
                item["strategy_version"] = profile["version_id"]
                item["strategy_manifest_sha256"] = manifest["manifest_sha256"]
    return manifest
