from src.background_research.preparation import prepare_background_research
from src.research.day_trader_context import DayTraderContextProtocol
from src.research.round_two_registry import load_round_protocol
from tests.background_ui_acceptance import prepare


def test_native_fixture_uses_collector_compatible_feature_history(tmp_path):
    root = tmp_path / "UIAcceptanceFixtures" / "native"
    prepare(root)
    protocol = load_round_protocol(root / "PaperResearch/paper-desk-v1")
    DayTraderContextProtocol(round_protocol=protocol)
    assert not (root / "BackgroundResearch").exists()  # Native app owns registration.
    result = prepare_background_research(
        source_directory=root / "PaperResearch/paper-desk-v1",
        output=root / "preflight-only/campaign.json",
        campaign_id="preflight-only",
        seed=42,
        created_at="2026-09-30T00:00:00Z",
    )
    assert result["event"] == "prepared"
