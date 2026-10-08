import json
from datetime import timedelta
from decimal import Decimal

import pytest

from scripts.run_last_half_hour_research import main
from src.intraday.last_half_hour_round import run_stage
from tests.unit.test_last_half_hour import session


def capture(path, day, earlier):
    bars = [
        *session(earlier, start=Decimal("99"), first=Decimal("99"),
                 penultimate=Decimal("99"), final=Decimal("100")),
        *session(day, start=Decimal("100"), first=Decimal("101"),
                 penultimate=Decimal("102"), final=Decimal("103")),
    ]
    lines = [json.dumps({"schema_version": 1, "price_scope": "historical_base",
                         "instrument": bars[0].instrument.model_dump(mode="json")})]
    lines.extend(bar.model_dump_json() for bar in bars)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_stages_are_immutable_and_sealed_cannot_be_repeated(tmp_path):
    sources = []
    for stage, day, prior in (
        ("development", "2023-03-10", "2023-03-09"),
        ("validation", "2024-03-11", "2024-03-08"),
        ("sealed", "2025-03-10", "2025-03-07"),
    ):
        path = tmp_path / f"{stage}.jsonl"
        capture(path, day, prior)
        sources.append(path)
    directory = tmp_path / "round"
    with pytest.raises(ValueError, match="precede"):
        run_stage("sealed", [sources[2]], directory)
    for stage, source in zip(("development", "validation", "sealed"), sources, strict=True):
        result = run_stage(stage, [source], directory)
        assert result["price_scope"] == "historical_base_exploratory"
        assert result["eligible_days"] == 1
        assert result["source_sha256"]
        assert json.loads((directory / "protocol.json").read_text())["implementation_sha256"]
        assert result["baseline_points"] == "0"
        assert "always_long_net_points" in result
        assert "always_short_net_points" in result
        assert "maximum_drawdown_points" in result
    with pytest.raises(FileExistsError):
        run_stage("sealed", [sources[2]], directory)


def test_rejects_wrong_product_and_date_window(tmp_path):
    path = tmp_path / "wrong.jsonl"
    capture(path, "2025-03-10", "2025-03-07")
    with pytest.raises(ValueError, match="window"):
        run_stage("development", [path], tmp_path / "round")
    content = path.read_text().replace("SPX500_USD", "DE30_EUR")
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="SPX500_USD"):
        run_stage("sealed", [path], tmp_path / "round")


def test_cli_publishes_exploratory_stage_without_credentials(tmp_path, capsys):
    path = tmp_path / "development.jsonl"
    capture(path, "2023-03-10", "2023-03-09")
    result = main(["--stage", "development", "--capture", str(path),
                   "--directory", str(tmp_path / "round")])
    assert result == 0
    assert json.loads(capsys.readouterr().out)["account_fill_claim"] is False


def test_overnight_cfd_bars_are_excluded_from_regular_session(tmp_path):
    path = tmp_path / "development.jsonl"
    capture(path, "2023-03-10", "2023-03-09")
    extra = session("2023-03-10", start=Decimal("100"), first=Decimal("101"),
                    penultimate=Decimal("102"), final=Decimal("103"))[0]
    extra = extra.model_copy(update={
        "start": extra.start - timedelta(hours=1),
        "end": extra.end - timedelta(hours=1),
        "available_at": extra.available_at - timedelta(hours=1),
        "source_key": "overnight",
    })
    with path.open("a", encoding="utf-8") as stream:
        stream.write(extra.model_dump_json() + "\n")
    result = run_stage("development", [path], tmp_path / "new-attempt")
    assert result["eligible_days"] == 1
