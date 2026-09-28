#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

SCHEMA_VERSION = 1
EXCLUDED_PARTS = {".git", ".venv", ".env", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist"}


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def source_hashes(root: Path) -> dict[str, str]:
    candidates = [
        *root.glob("src/**/*.py"),
        *root.glob("config/*.yaml"),
        root / "pyproject.toml",
        root / "scripts/live_engine_entry.py",
        root / "scripts/engine_manifest.py",
        root / "scripts/build_engine_bundle.sh",
    ]
    paths = sorted(
        {
            path.resolve()
            for path in candidates
            if path.is_file() and not EXCLUDED_PARTS.intersection(path.relative_to(root).parts)
        }
    )
    return {path.relative_to(root).as_posix(): file_hash(path) for path in paths}


def distribution_identity(distribution) -> dict[str, object]:
    """Hash actual installed RECORD-listed content, not just version labels."""
    files = {}
    if distribution.files is None:
        raise ValueError("runtime dependency has no installed file inventory")
    for item in distribution.files:
        if str(item).endswith(".pyc") or "__pycache__" in item.parts:
            continue
        path = Path(distribution.locate_file(item))
        if not path.is_file():
            raise ValueError("runtime dependency artifact is missing")
        files[str(item)] = file_hash(path)
    return {"version": distribution.version, "content_sha256": digest(files)}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def environment_identity() -> dict[str, object]:
    # All declared production dependencies and active transitive requirements.
    pending = [
        "certifi",
        "duckdb",
        "duckdb-engine",
        "httpx",
        "numpy",
        "pandas",
        "pytz",
        "pydantic",
        "python-dotenv",
        "pyyaml",
        "scikit-learn",
        "scipy",
        "sqlalchemy",
        "statsmodels",
        "tenacity",
        "typer",
        "websockets",
        "packaging",
    ]
    dependencies = {}
    while pending:
        name = pending.pop().lower().replace("_", "-")
        if name in dependencies:
            continue
        distribution = metadata.distribution(name)
        dependencies[name] = distribution_identity(distribution)
        for value in distribution.requires or ():
            requirement = Requirement(value)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "python_executable_sha256": file_hash(Path(sys.executable)),
        "dependencies": dependencies,
    }


def build_identity(root: Path) -> dict[str, object]:
    files = source_hashes(root)
    return {"source_files": files, "source_tree_sha256": digest(files), "environment": environment_identity()}


def build_manifest(root: Path, executable: Path, *, retained_build=None) -> dict[str, object]:
    build = retained_build if retained_build is not None else build_identity(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "executable_name": executable.name,
        "executable_sha256": file_hash(executable),
        **build,
    }


def verify_manifest(root: Path, executable: Path, manifest: dict[str, object]) -> bool:
    return manifest == build_manifest(root, executable)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--build-output", type=Path)
    parser.add_argument("--retained-build", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.build_output:
        args.build_output.parent.mkdir(parents=True, exist_ok=True)
        args.build_output.write_text(json.dumps(build_identity(root), sort_keys=True) + "\n")
        return 0
    if args.executable is None:
        parser.error("--executable is required")
    executable = args.executable.resolve()
    if args.verify:
        manifest = json.loads(args.verify.read_text(encoding="utf-8"))
        return 0 if verify_manifest(root, executable, manifest) else 1
    if args.output is None:
        parser.error("--output is required unless --verify is used")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    retained = json.loads(args.retained_build.read_text()) if args.retained_build else None
    if retained is not None and retained != build_identity(root):
        raise ValueError("retained build identity differs from current source/environment")
    args.output.write_text(
        json.dumps(build_manifest(root, executable, retained_build=retained), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
