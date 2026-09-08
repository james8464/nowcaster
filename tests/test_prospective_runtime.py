"""Real persistent runtime; doubles are confined to the public feed boundary."""

import asyncio
import importlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    return importlib.import_module("src.research.prospective_runtime")


def test_source_freeze_detects_changed_study_script(tmp_path):
    # Removing study scripts from the freeze would let a changed collector run.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "engine.py").write_text("x = 1\n")
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts" / "run_prospective_study.py"
    script.write_text("x = 1\n")
    before = runtime().study_source_hash(tmp_path)
    script.write_text("x = 2\n")
    assert runtime().study_source_hash(tmp_path) != before


def discovery(tmp_path):
    spec = runtime().load_registry(ROOT).resolve("macd_histogram_trend").spec
    candidate = dict(
        symbol="BTCUSDT",
        strategy_id=spec.strategy_id,
        strategy_definition_hash=spec.definition_hash,
        stop_atr=1,
        target_atr=1.5,
        maximum_bars=6,
        screen_passed=False,
    )
    from src.strategies.types import canonical_hash

    candidate["candidate_id"] = canonical_hash({k: v for k, v in candidate.items() if k != "screen_passed"})
    path = tmp_path / "discovery.json"
    path.write_text(json.dumps({"candidates": [candidate], "promotable": False}))
    return path


def test_registration_rejects_overwrite_and_retains_campaign_counter(tmp_path):
    now = datetime(2026, 9, 8, tzinfo=UTC)
    source = discovery(tmp_path)
    first = runtime().register_study(source, tmp_path / "one", root=ROOT, now=now)
    with pytest.raises((FileExistsError, ValueError)):
        runtime().register_study(source, tmp_path / "one", root=ROOT, now=now)
    second = runtime().register_study(source, tmp_path / "two", root=ROOT, now=now)
    assert (first.study_number, second.study_number) == (1, 2)
    assert second.predecessor_study_id == first.study_id
    assert len((tmp_path / "campaigns.jsonl").read_text().splitlines()) == 2
    assert first.starts_at > now
    assert first.ends_at - first.starts_at == timedelta(days=90)


def test_registration_rejects_past_start_before_creating_campaign(tmp_path):
    now = datetime(2026, 9, 8, tzinfo=UTC)
    with pytest.raises(ValueError, match="future"):
        runtime().register_study(
            discovery(tmp_path), tmp_path / "one", root=ROOT, now=now, starts_at=now - timedelta(seconds=1)
        )
    assert not (tmp_path / "campaigns.jsonl").exists()


def test_changed_source_refuses_before_feed_is_opened(tmp_path):
    source = discovery(tmp_path)
    directory = tmp_path / "one"
    runtime().register_study(source, directory, root=ROOT)
    payload = json.loads((directory / "manifest.json").read_text())
    payload["source_hash"] = "0" * 64
    (directory / "manifest.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="source"):
        asyncio.run(runtime().run_study(directory, root=ROOT, duration_seconds=0.02, transport=ForbiddenTransport()))


def test_finished_cash_only_study_stops_without_network(tmp_path):
    directory = tmp_path / "old"
    start = datetime.now(UTC) - timedelta(days=91)
    runtime().register_study(
        discovery(tmp_path), directory, root=ROOT, now=start - timedelta(minutes=3), starts_at=start
    )
    summary = asyncio.run(
        runtime().run_study(directory, root=ROOT, duration_seconds=0.02, transport=ForbiddenTransport())
    )
    assert summary["status"] == "insufficient_evidence"
    assert summary["evidence_counts"]["quotes"] == 0


def test_removed_campaign_registry_refuses_collection(tmp_path):
    _, directory, _ = registered(tmp_path)
    (directory.parent / "campaigns.jsonl").unlink()
    with pytest.raises(ValueError, match="campaign"):
        asyncio.run(runtime().run_study(directory, root=ROOT, duration_seconds=0.02, transport=ForbiddenTransport()))


def test_future_bar_records_clock_problem_instead_of_silently_skipping(tmp_path):
    from src.research.prospective import ProspectiveLedger

    now, directory, manifest = registered(tmp_path)
    with ProspectiveLedger(directory / "ledger.sqlite", manifest) as ledger:
        processor = runtime().StudyProcessor(manifest, runtime().load_registry(ROOT), ledger)
        processor.on_bar(bar(now), now=now)
        assert ledger.summary(now=now)["evidence_counts"]["gaps"] == 1


class ForbiddenTransport:
    async def seed(self, *args):
        raise AssertionError("changed source reached public feed")


def bar(start, *, interval="1m", price=Decimal("120"), repaired=False):
    from src.live_monitor.types import MarketBar

    end = start + (timedelta(hours=1) if interval == "1h" else timedelta(minutes=1))
    return MarketBar(
        provider="binance",
        feed="spot",
        symbol="BTCUSDT",
        interval=interval,
        start=start,
        end=end,
        available_at=end,
        received_at=end,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=10,
        finalized=True,
        revision=0,
        repair_verified=repaired,
    )


def registered(tmp_path):
    now = datetime(2026, 9, 8, tzinfo=UTC)
    directory = tmp_path / "one"
    manifest = runtime().register_study(
        discovery(tmp_path), directory, root=ROOT, now=now - timedelta(minutes=3), starts_at=now - timedelta(minutes=1)
    )
    return now, directory, manifest


def test_only_complete_fresh_live_hour_can_emit_persisted_decision(tmp_path):
    # Dropping freshness, completeness, or seed isolation would create extra signals.
    from src.research.prospective import ProspectiveLedger

    now, directory, manifest = registered(tmp_path)
    with ProspectiveLedger(directory / "ledger.sqlite", manifest) as ledger:
        processor = runtime().StudyProcessor(manifest, runtime().load_registry(ROOT), ledger)
        processor.seed(
            [
                bar(now - timedelta(hours=100 - i), interval="1h", price=Decimal(100) + Decimal(i) / 10)
                for i in range(100)
            ]
        )
        assert ledger.summary(now=now)["candidates"][0]["decisions"] == 0
        for i in range(59):
            minute = bar(now + timedelta(minutes=i))
            processor.on_bar(minute, now=minute.end)
        assert ledger.summary(now=now + timedelta(minutes=59))["candidates"][0]["decisions"] == 0
        final = bar(now + timedelta(minutes=59))
        processor.on_bar(final, now=final.end + timedelta(seconds=1))
        assert ledger.summary(now=final.end + timedelta(seconds=1))["candidates"][0]["decisions"] == 1
        processor.on_bar(final, now=final.end + timedelta(seconds=2))
        assert ledger.summary(now=final.end + timedelta(seconds=2))["candidates"][0]["decisions"] == 1
    with ProspectiveLedger(directory / "ledger.sqlite", manifest) as ledger:
        assert ledger.summary(now=final.end + timedelta(seconds=3))["candidates"][0]["decisions"] == 1


@pytest.mark.parametrize("kind", ["repaired", "old", "gap", "short"])
def test_repaired_old_or_incomplete_live_hour_never_emits(tmp_path, kind):
    from src.research.prospective import ProspectiveLedger

    now, directory, manifest = registered(tmp_path)
    with ProspectiveLedger(directory / "ledger.sqlite", manifest) as ledger:
        processor = runtime().StudyProcessor(manifest, runtime().load_registry(ROOT), ledger)
        processor.seed(
            [
                bar(now - timedelta(hours=100 - i), interval="1h", price=Decimal(100) + Decimal(i) / 10)
                for i in range(100)
            ]
        )
        for i in range(60):
            if kind == "gap" and i == 10:
                continue
            minute = bar(
                now + timedelta(minutes=i),
                repaired=kind == "repaired",
                price=Decimal("90") if kind == "short" else Decimal("120"),
            )
            observed = minute.end + timedelta(seconds=10 if kind == "old" else 0)
            processor.on_bar(minute, now=observed)
        assert ledger.summary(now=now + timedelta(hours=1, seconds=10))["candidates"][0]["decisions"] == 0


class StalledPublicFeed:
    async def seed(self, symbols, now):
        return [], {s: (Decimal("0.00001"), Decimal("5")) for s in symbols}

    async def stream(self, symbols):
        await asyncio.sleep(100)
        yield None


def test_bounded_run_writes_summary_even_when_feed_stalls(tmp_path):
    _, directory, _ = registered(tmp_path)
    summary = asyncio.run(
        runtime().run_study(directory, root=ROOT, duration_seconds=0.04, transport=StalledPublicFeed())
    )
    assert summary["paper_only"] is True
    assert summary["collector"]["state"] == "stopped"
    assert json.loads((directory / "summary.json").read_text())["updated_at"] == summary["updated_at"]
    assert (directory / "report.md").is_file()


def test_cli_register_twice_rejects_and_status_reads_evidence(tmp_path):
    from scripts.run_prospective_study import main

    path = discovery(tmp_path)
    directory = tmp_path / "cli"
    args = ["register", "--discovery", str(path), "--directory", str(directory)]
    assert main(args) == 0
    assert main(args) == 2
    assert main(["status", "--directory", str(directory)]) == 0
    assert len((tmp_path / "campaigns.jsonl").read_text().splitlines()) == 1


def test_periodic_summary_survives_stalled_seed(tmp_path):
    # A heartbeat tied only to incoming events would never publish this intermediate report.
    _, directory, _ = registered(tmp_path)

    class StalledSeed:
        async def seed(self, symbols, now):
            await asyncio.sleep(100)

    async def check():
        task = asyncio.create_task(
            runtime().run_study(
                directory, root=ROOT, duration_seconds=0.2, heartbeat_seconds=0.02, transport=StalledSeed()
            )
        )
        await asyncio.sleep(0.07)
        first = json.loads((directory / "summary.json").read_text())["updated_at"]
        await asyncio.sleep(0.07)
        second = json.loads((directory / "summary.json").read_text())["updated_at"]
        await task
        return first, second

    first, second = asyncio.run(check())
    assert second > first


def test_public_transport_checks_lot_rules_and_only_seeds_closed_hours():
    import httpx
    import respx

    now = datetime(2026, 9, 8, 12, tzinfo=UTC)
    metadata = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "status": "TRADING",
                "isSpotTradingAllowed": True,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.00001"},
                    {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
                ],
            }
        ]
    }
    opened = int((now - timedelta(hours=1)).timestamp() * 1000)
    row = [opened, "100", "101", "99", "100", "10", opened + 3599999, "1000", 20, "5", "500", "0"]
    with respx.mock(assert_all_mocked=True) as router:
        router.get("https://api.binance.com/api/v3/exchangeInfo").mock(return_value=httpx.Response(200, json=metadata))
        route = router.get("https://api.binance.com/api/v3/klines")
        route.mock(return_value=httpx.Response(200, json=[row]))
        bars, rules = asyncio.run(runtime().PublicBinanceTransport().seed(("BTCUSDT",), now))
        assert rules == {"BTCUSDT": (Decimal("0.00001"), Decimal("5"))}
        assert len(bars) == 1 and bars[0].repair_verified and bars[0].end == now
        row[6] += 3600000
        route.mock(return_value=httpx.Response(200, json=[row]))
        with pytest.raises(ValueError, match="incomplete"):
            asyncio.run(runtime().PublicBinanceTransport().seed(("BTCUSDT",), now))


def test_disconnection_is_retained_in_ledger_and_report(tmp_path):
    from src.live_monitor.types import MonitorHealth, ProviderHealthEvent

    _, directory, _ = registered(tmp_path)

    class DisconnectedFeed(StalledPublicFeed):
        async def stream(self, symbols):
            yield ProviderHealthEvent(
                provider="binance",
                feed="spot",
                status=MonitorHealth.RECONNECTING,
                reason="test_disconnect",
                occurred_at=datetime.now(UTC),
            )
            await asyncio.sleep(100)

    summary = asyncio.run(
        runtime().run_study(directory, root=ROOT, duration_seconds=0.04, transport=DisconnectedFeed())
    )
    assert summary["evidence_counts"]["gaps"] == 3  # start, actual disconnect, controlled stop


def test_missing_ledger_refuses_to_reset_existing_campaign(tmp_path):
    _, directory, _ = registered(tmp_path)
    (directory / "ledger.sqlite").unlink()
    with pytest.raises(ValueError, match="ledger"):
        asyncio.run(runtime().run_study(directory, root=ROOT, duration_seconds=0.02, transport=ForbiddenTransport()))


def test_invalid_feed_fails_closed_and_publishes_failure(tmp_path):
    _, directory, _ = registered(tmp_path)

    class InvalidFeed:
        async def seed(self, symbols, now):
            raise ValueError("invalid provider metadata")

    with pytest.raises(ValueError, match="metadata"):
        asyncio.run(runtime().run_study(directory, root=ROOT, duration_seconds=0.04, transport=InvalidFeed()))
    report = json.loads((directory / "summary.json").read_text())
    assert report["collector"]["state"] == "failed"
    assert report["collector"]["last_error"] == "ValueError"
    assert report["evidence_counts"]["gaps"] == 3


def test_hour_boundary_refreshes_context_without_retroactive_decisions(tmp_path):
    from freezegun import freeze_time

    class BoundaryFeed(StalledPublicFeed):
        async def stream(self, symbols):
            yield bar(datetime(2026, 9, 8, 0, 59, tzinfo=UTC))
            await asyncio.sleep(100)

    with freeze_time("2026-09-08T01:00:00Z", real_asyncio=True):
        _, directory, _ = registered(tmp_path)
        summary = asyncio.run(
            runtime().run_study(directory, root=ROOT, duration_seconds=0.05, transport=BoundaryFeed())
        )
    assert summary["collector"]["context_refreshes"] == 1
    assert summary["evidence_counts"]["decisions"] == 0


@pytest.mark.parametrize("repair", [False, True])
def test_mid_hour_start_repair_warms_only_later_complete_live_hour(tmp_path, repair):
    from src.research.prospective import ProspectiveLedger

    now, directory, manifest = registered(tmp_path)
    with ProspectiveLedger(directory / "ledger.sqlite", manifest) as ledger:
        processor = runtime().StudyProcessor(manifest, runtime().load_registry(ROOT), ledger)
        processor.seed(
            [
                bar(now - timedelta(hours=100 - i), interval="1h", price=Decimal(100) + Decimal(i) / 10)
                for i in range(100)
            ]
        )
        for i in range(30, 60):
            minute = bar(now + timedelta(minutes=i), price=Decimal(110))
            processor.on_bar(minute, now=minute.end)
        if repair:
            processor.seed([bar(now, interval="1h", price=Decimal(110), repaired=True)])
        assert ledger.summary(now=now + timedelta(hours=1))["evidence_counts"]["decisions"] == 0
        for i in range(60, 120):
            minute = bar(now + timedelta(minutes=i))
            processor.on_bar(minute, now=minute.end)
        assert ledger.summary(now=now + timedelta(hours=2))["evidence_counts"]["decisions"] == int(repair)
