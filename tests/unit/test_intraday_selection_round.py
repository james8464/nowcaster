import json

import pytest

from scripts.run_intraday_selection import main
from src.intraday.selection_round import run_registered_selection
from tests.unit.test_intraday_selection import INSTRUMENT, bar, manifest


def test_registered_selection_retains_inputs_and_every_rejected_attempt(tmp_path):
    source = tmp_path / "DE30_EUR.jsonl"
    source.write_text(
        json.dumps({
            "schema_version": 1, "price_scope": "historical_base",
            "instrument": INSTRUMENT.model_dump(mode="json"),
            "requested_start": "2026-01-01T00:00:00+00:00", "requested_end": "2026-01-04T00:00:00+00:00",
        })
        + "\n" + "\n".join(bar(day).model_dump_json() for day in (1, 2, 3)) + "\n"
    )
    target = tmp_path / "round-2"
    result = run_registered_selection(manifest(), {"DE30_EUR": source}, target)
    assert result.selected == ()
    assert len(result.attempts) == 16
    assert (target / "inputs" / source.name).read_bytes() == source.read_bytes()
    saved = json.loads((target / "selection.json").read_text())
    assert len(saved["attempts"]) == 16
    assert saved["price_scope"] == "historical_base_exploratory"
    assert saved["selected"] == []
    with pytest.raises(FileExistsError):
        run_registered_selection(manifest(), {"DE30_EUR": source}, target)


def test_registered_selection_rejects_header_product_mismatch(tmp_path):
    source = tmp_path / "wrong.jsonl"
    source.write_text(json.dumps({
        "schema_version": 1, "price_scope": "historical_base",
        "instrument": {**INSTRUMENT.model_dump(mode="json"), "broker_symbol": "SPX500_USD"},
    }) + "\n")
    with pytest.raises(ValueError, match="historical header product"):
        run_registered_selection(manifest(), {"DE30_EUR": source}, tmp_path / "round-2")


def test_registered_selection_rejects_unexpected_header_fields_before_copy(tmp_path):
    source = tmp_path / "unsafe.jsonl"
    source.write_text(json.dumps({
        "schema_version": 1, "price_scope": "historical_base",
        "instrument": INSTRUMENT.model_dump(mode="json"), "token": "should-not-be-retained",
    }) + "\n")
    with pytest.raises(ValueError, match="historical header product"):
        run_registered_selection(manifest(), {"DE30_EUR": source}, tmp_path / "round-2")
    assert not (tmp_path / "round-2").exists()


def test_registered_selection_does_not_use_product_name_as_unsafe_path(tmp_path):
    unsafe = INSTRUMENT.model_copy(update={"broker_symbol": "../escape"})
    config = manifest().model_copy(update={"instruments": (unsafe,)})
    with pytest.raises(ValueError, match="broker symbol"):
        run_registered_selection(config, {"../escape": tmp_path / "unused.jsonl"}, tmp_path / "round-2")
    assert not (tmp_path / "round-2").exists()


def test_selection_command_preserves_failed_round_without_enabling_live_rule(tmp_path, capsys):
    source = tmp_path / "DE30_EUR.jsonl"
    source.write_text(json.dumps({
        "schema_version": 1, "price_scope": "historical_base", "instrument": INSTRUMENT.model_dump(mode="json"),
        "requested_start": "2026-01-01T00:00:00+00:00", "requested_end": "2026-01-04T00:00:00+00:00",
    }) + "\n")
    config = tmp_path / "manifest.json"
    config.write_text(manifest().model_dump_json())
    target = tmp_path / "round-2"
    assert main(["--manifest", str(config), "--input", f"DE30_EUR={source}", "--output-directory", str(target)]) == 0
    assert json.loads((target / "selection.json").read_text())["activates_live_rule"] is False
    assert "selected 0" in capsys.readouterr().out


@pytest.mark.parametrize("requested_end", [None, "2026-01-03T00:00:00+00:00"])
def test_selection_refuses_missing_or_truncated_declared_history(tmp_path, requested_end):
    header = {
        "schema_version": 1, "price_scope": "historical_base",
        "instrument": INSTRUMENT.model_dump(mode="json"),
        "requested_start": "2026-01-01T00:00:00+00:00",
    }
    if requested_end is not None:
        header["requested_end"] = requested_end
    source = tmp_path / "DE30_EUR.jsonl"
    source.write_text(json.dumps(header) + "\n" + bar(1).model_dump_json() + "\n")
    with pytest.raises(ValueError, match="declared history window"):
        run_registered_selection(manifest(), {"DE30_EUR": source}, tmp_path / "round-2")
    assert not (tmp_path / "round-2").exists()
