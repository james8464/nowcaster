from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

NOW = datetime(2026, 8, 31, 6, 30, tzinfo=UTC)


def wire(kind, payload, sequence=0):
    return {
        "schema_version": 1,
        "event_id": f"{sequence + 1:064x}",
        "sequence": sequence,
        "event_type": kind,
        "emitted_at": NOW.isoformat(),
        "payload": payload,
    }


def ready():
    return {
        "status": "live",
        "qualified_cohorts": 0,
        "cohort_hash": "0" * 64,
        "readiness_receipt_id": None,
    }


def health(reason="subscribed"):
    return {
        "provider": "binance",
        "feed": "spot",
        "status": "healthy",
        "reason": reason,
        "occurred_at": NOW.isoformat(),
    }


def quote(symbol="BTCUSDT", delay=0):
    return {
        "provider": "binance",
        "feed": "spot",
        "symbol": symbol,
        "bid": "99.9",
        "ask": "100",
        "last": "99.95",
        "tick_size": "0.01",
        "sequence": None,
        "provider_time": NOW.isoformat(),
        "received_at": (NOW - timedelta(milliseconds=80)).isoformat(),
        "processed_at": (NOW + timedelta(seconds=delay)).isoformat(),
    }


def test_live_report_distinguishes_clock_offset_queue_latency_and_profitability():
    from scripts.validate_live_monitor import LiveObservation

    observation = LiveObservation(("BTCUSDT",))
    observation.accept(wire("ready", ready()), observed_at=NOW)
    observation.accept(wire("provider_health", health(), 1), observed_at=NOW)
    observation.accept(wire("quote", quote(delay=2), 2), observed_at=NOW + timedelta(seconds=3))
    observation.accept(
        wire(
            "decision",
            {
                "symbol": "BTCUSDT",
                "decision_time": NOW.isoformat(),
                "provider": "binance",
                "feed": "spot",
                "status": "abstain",
                "reasons": ["qualified_evidence_unavailable"],
            },
            3,
        ),
        observed_at=NOW + timedelta(seconds=3),
    )
    report = observation.report(exit_code=0, live_seconds=10, stderr_present=False)
    asset = report["assets"]["BTCUSDT"]
    assert asset["provider_to_receive_ms"]["p50"] == -80
    assert asset["receive_to_processing_ms"]["p50"] == 2080
    assert asset["provider_to_observer_ms"]["p50"] == 3000
    assert report["decision_reasons"] == {"qualified_evidence_unavailable": 1}
    assert report["profitability"] == "not_assessed_no_qualified_entries"
    assert report["issues"] == []
    assert report["scope"] == "connectivity_only"


def test_live_report_flags_missing_asset_stale_queue_and_wire_gaps():
    from scripts.validate_live_monitor import LiveObservation

    observation = LiveObservation(("BTCUSDT", "ETHUSDT"))
    observation.accept(wire("ready", ready()), observed_at=NOW)
    observation.accept(wire("quote", quote(delay=40), 2), observed_at=NOW + timedelta(seconds=41))
    report = observation.report(exit_code=0, live_seconds=900, stderr_present=False)
    assert "wire_sequence_gap" in report["issues"]
    assert "stale_quotes:BTCUSDT" in report["issues"]
    assert "missing_quotes:ETHUSDT" in report["issues"]
    assert "insufficient_finalized_bars:BTCUSDT" in report["issues"]
    assert "insufficient_decision_windows:BTCUSDT" in report["issues"]


def test_live_report_does_not_count_waiting_for_quote_as_a_second_decision_window():
    from scripts.validate_live_monitor import LiveObservation

    observation = LiveObservation(("BTCUSDT",))
    for index, reason in enumerate(("awaiting_post_finalization_quote", "qualified_evidence_unavailable")):
        observation.accept(
            wire(
                "decision",
                {
                    "provider": "binance",
                    "feed": "spot",
                    "symbol": "BTCUSDT",
                    "decision_time": NOW.isoformat(),
                    "status": "abstain",
                    "reasons": [reason],
                },
                index,
            ),
            observed_at=NOW,
        )
    assert (
        observation.report(exit_code=0, live_seconds=10, stderr_present=False)["assets"]["BTCUSDT"]["decision_windows"]
        == 1
    )


def test_live_report_rejects_an_actionable_entry_in_the_isolated_unqualified_probe():
    from scripts.validate_live_monitor import LiveObservation

    observation = LiveObservation(("BTCUSDT",))
    observation.accept(wire("notification_request", {"category": "entry"}), observed_at=NOW)
    report = observation.report(exit_code=1, live_seconds=10, stderr_present=True)
    assert "unexpected_notification" in report["issues"]
    assert "engine_exit_failure" in report["issues"]
    assert "engine_stderr" in report["issues"]


def test_live_report_cannot_pass_a_feed_that_falls_silent_after_one_fresh_quote():
    from scripts.validate_live_monitor import LiveObservation

    observation = LiveObservation(("BTCUSDT",))
    observation.accept(wire("ready", ready()), observed_at=NOW)
    observation.accept(wire("quote", quote(), 1), observed_at=NOW)
    report = observation.report(exit_code=0, live_seconds=900, stderr_present=False)
    assert "stale_quotes:BTCUSDT" in report["issues"]


def test_live_report_rejects_wrong_feed_unsafe_known_events_and_missing_health():
    from scripts.validate_live_monitor import LiveObservation

    observation = LiveObservation(("BTCUSDT",))
    observation.accept(wire("ready", ready()), observed_at=NOW)
    wrong = {**quote(), "provider": "alpaca", "feed": "iex"}
    observation.accept(wire("quote", wrong, 1), observed_at=NOW)
    observation.accept(wire("setup_snapshot", {"symbol": "BTCUSDT"}, 2), observed_at=NOW)

    report = observation.report(exit_code=0, live_seconds=10, stderr_present=False)

    assert "unexpected_market_identity" in report["issues"]
    assert "unexpected_event_type:setup_snapshot" in report["issues"]
    assert "missing_healthy_subscription" in report["issues"]


def test_live_report_strictly_rejects_an_unknown_wire_type():
    from scripts.validate_live_monitor import LiveObservation

    with pytest.raises(ValidationError):
        LiveObservation(("BTCUSDT",)).accept(wire("made_up", {}, 0), observed_at=NOW)


def test_only_a_signed_manifest_bound_bundle_helper_is_classified_as_packaged(tmp_path, monkeypatch):
    from scripts import validate_live_monitor
    from scripts.engine_manifest import build_manifest

    root = tmp_path / "project"
    helper = root / "build" / "Nowcaster.app" / "Contents" / "Helpers" / "nowcaster-engine"
    manifest = helper.parent.parent / "Resources" / "engine-manifest.json"
    helper.parent.mkdir(parents=True)
    manifest.parent.mkdir(parents=True)
    helper.write_bytes(b"engine")
    helper.chmod(0o700)
    monkeypatch.setattr(validate_live_monitor.sys, "platform", "linux")

    with pytest.raises(ValueError, match="manifest"):
        validate_live_monitor._engine_kind(root, helper)

    manifest.write_text(json.dumps(build_manifest(root, helper)), encoding="utf-8")
    assert validate_live_monitor._engine_kind(root, helper) == "packaged"

    helper.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="manifest verification"):
        validate_live_monitor._engine_kind(root, helper)
