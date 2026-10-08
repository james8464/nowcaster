"""Retained, non-activating historical selection round."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from src.intraday.contracts import ConfirmedBar
from src.intraday.selection import SelectionManifest, SelectionReport, run_selection


def run_registered_selection(
    manifest: SelectionManifest,
    source_files: dict[str, Path],
    output_dir: Path,
) -> SelectionReport:
    """Read immutable downloaded BA candles; keep all trials and input bytes.

    This does not change the live service or authorize paper entries. A new
    hypothesis, source file, cost assumption or boundary needs a new directory.
    """
    output_dir = Path(output_dir).expanduser().resolve()
    if {"ProspectiveStudies", "live-paper-study"} & set(output_dir.parts):
        raise ValueError("protected study path")
    if output_dir.exists():
        raise FileExistsError("selection round already exists")
    instruments = {item.broker_symbol: item for item in manifest.instruments}
    if any(re.fullmatch(r"[A-Z][A-Z0-9_]*", symbol) is None for symbol in instruments):
        raise ValueError("broker symbol is not a safe OANDA product name")
    if set(source_files) != set(instruments):
        raise ValueError("each pre-registered product needs one historical input")
    bars_by_product = {}
    original_bytes = {}
    input_hashes = {}
    for symbol, source in source_files.items():
        contents = Path(source).read_bytes()
        if not contents or len(contents) > 100 * 1024 * 1024 or not contents.endswith(b"\n"):
            raise ValueError("historical input missing, oversized or unterminated")
        lines = contents.splitlines()
        if any(len(line) > 65536 for line in lines):
            raise ValueError("oversized historical row")
        header = json.loads(lines[0])
        if (
            not isinstance(header, dict)
            or not set(header) <= {"schema_version", "price_scope", "instrument", "requested_start", "requested_end"}
            or
            header.get("schema_version") != 1
            or header.get("price_scope") != "historical_base"
            or header.get("instrument") != instruments[symbol].model_dump(mode="json")
        ):
            raise ValueError("historical header product or scope mismatch")
        bars_by_product[symbol] = tuple(ConfirmedBar.model_validate_json(line) for line in lines[1:])
        original_bytes[symbol] = contents
        input_hashes[symbol] = hashlib.sha256(contents).hexdigest()
    report = run_selection(manifest, bars_by_product)
    output_dir.mkdir(parents=True, exist_ok=False)
    inputs = output_dir / "inputs"
    inputs.mkdir()
    for symbol, contents in sorted(original_bytes.items()):
        with (inputs / f"{symbol}.jsonl").open("xb") as stream:
            stream.write(contents)
    with (output_dir / "manifest.json").open("x", encoding="utf-8") as stream:
        stream.write(manifest.model_dump_json(indent=2) + "\n")
    result = {
        "schema_version": 1,
        "paper_only": True,
        "activates_live_rule": False,
        "manifest_hash": report.manifest_hash,
        "input_sha256": input_hashes,
        "price_scope": report.price_scope,
        "cash_baseline": str(report.cash_baseline),
        "multiple_comparisons": report.multiple_comparisons,
        "attempts": [asdict(item) for item in report.attempts],
        "selected": [asdict(item) for item in report.selected],
    }
    with (output_dir / "selection.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True, indent=2, default=str)
        stream.write("\n")
    return report
