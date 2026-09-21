"""Build a self-contained Windows update from reviewed, Git-tracked source files."""

import argparse
import base64
import hashlib
import io
import json
import subprocess
import textwrap
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    files = {}
    for name in filter(None, tracked):
        if name.startswith(("data/", "reports/", "logs/", "backups/", ".venv/", ".git/", ".env")):
            continue
        if name in {"config/arthur-settings.json", "config/daily223-live.json"}:
            continue
        files[name] = (root / name).read_bytes()
    manifest = {
        "release": "3.5",
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for name, data in sorted(files.items()):
            output.writestr(name, data)
        output.writestr("release-manifest.json", json.dumps(manifest, indent=2))
    payload = archive.getvalue()
    template = (root / "scripts/arthur-update-template.ps1").read_text()
    rendered = template.replace("@@PAYLOAD_SHA256@@", hashlib.sha256(payload).hexdigest()).replace(
        "@@PAYLOAD_BASE64@@", "\n".join(textwrap.wrap(base64.b64encode(payload).decode(), 120))
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8-sig")
    print(f"Built {args.output.name}: {len(files)} tracked files, {len(payload)} ZIP bytes")


if __name__ == "__main__":
    main()
