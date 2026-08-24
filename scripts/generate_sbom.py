#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path


def build_sbom(project_root: Path) -> dict[str, object]:
    components = [
        {
            "type": "library",
            "name": item.metadata["Name"],
            "version": item.version,
            "purl": f"pkg:pypi/{item.metadata['Name']}@{item.version}",
        }
        for item in importlib.metadata.distributions()
        if item.metadata.get("Name")
    ]
    components.sort(key=lambda item: (str(item["name"]).lower(), str(item["version"])))
    serial_input = json.dumps(components, sort_keys=True, separators=(",", ":")).encode()
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:" + hashlib.sha256(serial_input).hexdigest()[:32],
        "version": 1,
        "metadata": {"component": {"type": "application", "name": "Nowcaster", "version": "0.1.0"}},
        "components": components,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(build_sbom(args.root), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
