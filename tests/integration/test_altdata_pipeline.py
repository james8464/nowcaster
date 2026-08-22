from __future__ import annotations

import json
from pathlib import Path

from src.database.engine import Database
from src.ingestion.macro import macro_rows, parse_fred
from src.ingestion.wikipedia import parse_pageviews, wikipedia_rows


def test_alternative_and_macro_observations_persist_idempotently(tmp_path):
    wiki_payload = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "alternative" / "wikipedia_sample.json").read_text()
    )
    macro_payload = json.loads((Path(__file__).parents[1] / "fixtures" / "macro" / "fred_sample.json").read_text())
    database = Database.from_url(f"duckdb:///{tmp_path / 'alternative.duckdb'}")
    database.initialize()

    wiki_count = database.upsert(
        "alternative_data_daily", wikipedia_rows(parse_pageviews(wiki_payload, company_id="SBUX"), source="test")
    )
    macro_count = database.upsert("macro_data", macro_rows(parse_fred(macro_payload, "RSAFS"), source="test"))

    assert wiki_count == 2
    assert macro_count == 2
    assert database.scalar("select count(*) from alternative_data_daily") == 2
    assert database.scalar("select count(*) from macro_data") == 2
