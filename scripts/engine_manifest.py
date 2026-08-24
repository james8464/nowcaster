#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SCHEMA_VERSION = 1
EXCLUDED_PARTS = {".git", ".venv", ".env", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist"}


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def source_hashes(root: Path) -> dict[str, str]:
    candidates = [*root.glob("src/**/*.py"), *root.glob("config/*.yaml"), root / "pyproject.toml"]
    paths = sorted(
        {
            path.resolve()
            for path in candidates
            if path.is_file() and not EXCLUDED_PARTS.intersection(path.relative_to(root).parts)
        }
    )
    return {path.relative_to(root).as_posix(): file_hash(path) for path in paths}


def build_manifest(root: Path, executable: Path) -> dict[str, object]:
    files = source_hashes(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "executable_name": executable.name,
        "executable_sha256": file_hash(executable),
        "source_files": files,
        "source_tree_sha256": hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def verify_manifest(root: Path, executable: Path, manifest: dict[str, object]) -> bool:
    return manifest == build_manifest(root, executable)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    executable = args.executable.resolve()
    if args.verify:
        manifest = json.loads(args.verify.read_text(encoding="utf-8"))
        return 0 if verify_manifest(root, executable, manifest) else 1
    if args.output is None:
        parser.error("--output is required unless --verify is used")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(build_manifest(root, executable), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
