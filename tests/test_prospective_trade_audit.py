"""Real retained-ledger audits; expectations use hand-calculated economics."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from src.research.prospective import ProspectiveLedger
from src.strategies.types import canonical_hash, canonical_json
from tests.unit.test_prospective import START, D, manifest, observe, quote, signal, timedelta

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_prospective_trades.py"


@pytest.fixture
def study(tmp_path):
    directory = tmp_path / "study"
    directory.mkdir()
    registration = manifest()
    (directory / "manifest.json").write_text(registration.model_dump_json())
    with ProspectiveLedger(directory / "ledger.sqlite", registration) as ledger:
        yield directory, ledger


def audit(directory, seconds=2, output=None):
    args = [
        sys.executable,
        str(SCRIPT),
        "--directory",
        str(directory),
        "--now",
        (START + timedelta(seconds=seconds)).isoformat(),
    ]
    if output is not None:
        args += ["--output-dir", str(output)]
    return subprocess.run(args, capture_output=True, text=True, env=os.environ | {"PYTHONDONTWRITEBYTECODE": "1"})


def result(directory, seconds=2):
    process = audit(directory, seconds)
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


def retained(directory):
    # SHM reader slots are SQLite internals; durable DB/WAL and dataset rows must not change.
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir() if not p.name.endswith("-shm")
    }


def rewrite(directory, change):
    """Rehash synthetic evidence to distinguish semantic checks from hash checks."""
    with sqlite3.connect(directory / "ledger.sqlite") as conn:
        rows = conn.execute(
            "SELECT seq,event_key,kind,at,payload,previous_hash,hash FROM journal ORDER BY seq"
        ).fetchall()
        state = json.loads(conn.execute("SELECT payload FROM state").fetchone()[0])
        columns = ("seq", "event_key", "kind", "at", "payload", "previous_hash", "hash")
        events = [dict(zip(columns, row, strict=True)) for row in rows]
        for event in events:
            event["payload"] = json.loads(event["payload"])
        change(events, state)
        previous = "0" * 64
        for event in events:
            event["previous_hash"] = previous
            previous = canonical_hash({k: v for k, v in event.items() if k != "hash"})
            conn.execute(
                "UPDATE journal SET payload=?,previous_hash=?,hash=? WHERE seq=?",
                (canonical_json(event["payload"]), event["previous_hash"], previous, event["seq"]),
            )
        state["journal_head"] = previous
        conn.execute("UPDATE state SET payload=?,hash=?", (canonical_json(state), canonical_hash(state)))


def test_open_plan_is_not_overdue_and_read_does_not_touch_writer_evidence(study):
    directory, ledger = study
    signal(ledger)
    observe(ledger, quote())
    before = retained(directory)
    report = result(directory)
    assert retained(directory) == before
    trade = report["trades"][0]
    assert trade["status"] == "awaiting_plan_exit"
    assert trade["inspection_needed"] is False
    assert trade["screen_passed"] is False
    assert trade["entry_cost"] == "1112.2213877775"
    assert trade["remaining_quantity"] == "11.1"
    assert D(trade["stop"]) == D("98.100025")
    assert D(trade["target"]) == D("103.100025")
    assert trade["held_seconds"] == 1
    assert report["quotes"]["BTCUSDT"]["stale"] is False
    assert result(directory) == report


@pytest.mark.parametrize(
    "bid,seconds,outcome,pnl",
    [
        ("104", 3, "target", "40.4475894225"),
        ("97", 3, "stop", "-37.1358994275"),
        ("100", 21601, "expiry", "-3.8858327775"),
    ],
)
def test_completed_results_include_final_fill_and_costs_once(study, bid, seconds, outcome, pnl):
    directory, ledger = study
    signal(ledger)
    observe(ledger, quote())
    observe(ledger, quote(seconds, bid=bid, ask=str(D(bid) + D(".05"))))
    trade = result(directory, seconds)["trades"][0]
    assert trade["status"] == "completed"
    assert trade["trigger"] == outcome
    assert trade["recorded_matches_recomputed"] is True
    assert D(trade["net_pnl"]) == D(pnl)
    assert D(trade["stress_charge"]) == D("3.7777749435")
    assert D(trade["stressed_pnl"]) == D(pnl) - D("3.7777749435")
    assert len(trade["fills"]) == 2
    assert trade["tainted"] is (outcome == "expiry")
    original = trade
    observe(ledger, quote(seconds + 1, bid="200", ask="200.05"))
    assert result(directory, seconds + 1)["trades"][0] == original


def test_partial_close_then_full_close_uses_all_proceeds(study):
    directory, ledger = study
    signal(ledger)
    observe(ledger, quote())
    observe(ledger, quote(2, bid="104", ask="104.05", size="5"))
    trade = result(directory)["trades"][0]
    assert trade["status"] == "awaiting_eligible_exit_quote"
    assert trade["remaining_quantity"] == "6.1"
    assert D(trade["net_exit_proceeds"]) == D("519.22026")
    assert trade["net_pnl"] is None
    observe(ledger, quote(3, bid="103", ask="103.05"))
    trade = result(directory, 3)["trades"][0]
    assert trade["trigger"] == "target"  # Trigger remains latched even after retracing.
    assert D(trade["net_pnl"]) == D("34.3567363725")
    assert len(trade["fills"]) == 3


def test_overdue_with_insufficient_size_does_not_assert_stuck(study):
    directory, ledger = study
    signal(ledger)
    observe(ledger, quote())
    trade = result(directory, 21601)["trades"][0]
    assert trade["status"] == "awaiting_eligible_exit_quote"
    assert trade["trigger"] is None
    observe(ledger, quote(21601, size=".01"))
    report = result(directory, 21604)
    assert report["trades"][0]["trigger"] == "expiry"
    assert report["trades"][0]["inspection_needed"] is True
    assert report["quotes"]["BTCUSDT"]["stale"] is True
    assert "every quote" in " ".join(report["limitations"])


def test_rejections_and_unfilled_decisions_are_separate(study):
    directory, ledger = study
    signal(ledger)
    signal(ledger, signal_id="busy", seconds=0.1)
    report = result(directory)
    assert report["trades"] == []
    assert report["rejected_decisions"][0]["reason"] == "account_busy"
    assert report["unfilled_decisions"][0]["status"] == "pending_entry"
    observe(ledger, quote(31))
    assert result(directory, 31)["unfilled_decisions"][0]["reason"] == "observation_gap"


def test_late_liquidation_distinct_from_fixed_end_open_position(study):
    directory, ledger = study
    end = 90 * 86400
    observe(ledger, quote(end - 11))
    at = START + timedelta(seconds=end - 10)
    ledger.record_signal("c" * 64, decision_at=at, bar_end=at, reference_price=D("100"), atr=D("2"), signal_id="late")
    observe(ledger, quote(end - 9))
    observe(ledger, quote(end + 1, bid="104", ask="104.05"))
    report = result(directory, end + 1)
    assert report["trades"][0]["post_fixed_end"] is True
    assert report["trades"][0]["trigger"] == "expiry"
    assert report["fixed_end"]["accounts"]["c" * 64]["position"]["quantity"] == "11.1"


@pytest.mark.parametrize("mutation", ["pnl", "quantity", "stop", "expiry", "outcome", "latency", "nonfinite"])
def test_recomputed_hash_cannot_hide_semantic_corruption(study, mutation):
    directory, ledger = study
    signal(ledger)
    observe(ledger, quote())
    observe(ledger, quote(3, bid="104", ask="104.05"))
    ledger.close()

    def change(events, state):
        closed = next(e["payload"] for e in events if e["kind"] == "closed_trade")
        if mutation == "pnl":
            closed["net_pnl"] = "999"
        elif mutation == "quantity":
            closed["initial_quantity"] = "99"
        elif mutation == "stop":
            closed["stop"] = "99"
        elif mutation == "expiry":
            closed["expires_at"] = (START + timedelta(hours=12)).isoformat()
        elif mutation == "outcome":
            closed["trigger"] = "stop"
        elif mutation == "latency":
            fill = next(e["payload"] for e in events if e["kind"] == "fills")
            fill["quote"]["received_at"] = (START + timedelta(seconds=0.1)).isoformat()
        else:
            closed["entry_cost"] = "NaN"

    rewrite(directory, change)
    process = audit(directory, 3)
    assert process.returncode != 0
    assert "audit failed" in process.stderr.lower()
    assert not process.stdout


@pytest.mark.parametrize("target", ["journal", "state", "manifest"])
def test_corruption_fails_closed(study, target):
    directory, ledger = study
    signal(ledger)
    assert result(directory)["unfilled_decisions"]
    ledger.close()
    if target == "manifest":
        data = json.loads((directory / "manifest.json").read_text())
        data["source_hash"] = "0" * 64
        (directory / "manifest.json").write_text(json.dumps(data))
    else:
        with sqlite3.connect(directory / "ledger.sqlite") as conn:
            conn.execute(f"UPDATE {target} SET payload='{{}}'")
    assert audit(directory).returncode != 0


def test_distinct_quotes_at_same_clock_time_can_finish_a_partial_exit(study):
    directory, ledger = study
    signal(ledger)
    observe(ledger, quote(sequence=1))
    observe(ledger, quote(2, bid="104", ask="104.05", size="5", sequence=2))
    observe(ledger, quote(2, bid="104", ask="104.05", sequence=3))
    assert D(result(directory)["trades"][0]["net_pnl"]) == D("40.4475894225")


def test_future_same_time_gap_does_not_rewrite_completed_trade(study):
    directory, ledger = study
    signal(ledger)
    observe(ledger, quote())
    observe(ledger, quote(3, bid="104", ask="104.05"))
    original = result(directory, 3)["trades"][0]
    ledger.record_gap(at=START + timedelta(seconds=3), reason="later_event")
    assert result(directory, 3)["trades"][0] == original


def test_later_busy_decision_keeps_its_own_proposal_without_moving_live_expiry(study):
    directory, ledger = study
    signal(ledger)
    observe(ledger, quote())
    later = START + timedelta(hours=1)
    ledger.record_signal(
        "c" * 64, decision_at=later, bar_end=later, reference_price=D("100"), atr=D("2"), signal_id="later-proposal"
    )
    report = result(directory, 3600)
    assert report["trades"][0]["expires_at"] == "2026-09-09T06:00:00+00:00"
    assert report["rejected_decisions"][0]["frozen"]["expires_at"] == "2026-09-09T07:00:00+00:00"


def test_explicit_inspection_time_cannot_pretend_to_replay_older_state(study):
    directory, ledger = study
    signal(ledger, seconds=5)
    assert audit(directory, seconds=2).returncode != 0


@pytest.mark.parametrize("mutation", ["pending", "false_rejection"])
def test_decision_state_cannot_be_forged_even_after_rehash(study, mutation):
    directory, ledger = study
    signal(ledger)
    assert result(directory)["unfilled_decisions"]
    ledger.close()

    def change(events, state):
        event = next(e for e in events if e["kind"] == "decision")
        if mutation == "pending":
            state["accounts"]["c" * 64]["pending"]["signal_id"] = "invented"
            event["payload"]["accepted"] = False
            event["payload"]["reason"] = "account_busy"
        else:
            event["payload"]["accepted"] = False
            event["payload"]["reason"] = "account_busy"
            state["accounts"]["c" * 64]["pending"] = None

    rewrite(directory, change)
    assert audit(directory).returncode != 0


@pytest.mark.parametrize(
    "mutation", ["early_expiry", "fill_taint", "mark_nan", "phantom_position", "closed_notional", "decisions"]
)
def test_additional_rehashed_semantic_corruption_fails_closed(study, mutation):
    directory, ledger = study
    signal(ledger)
    observe(ledger, quote())
    observe(ledger, quote(3, bid="104", ask="104.05"))
    assert result(directory, 3)["trades"][0]["status"] == "completed"
    ledger.close()

    def change(events, state):
        closed = next(e["payload"] for e in events if e["kind"] == "closed_trade")
        sell = [e["payload"]["fills"][0] for e in events if e["kind"] == "fills"][-1]
        account = state["accounts"]["c" * 64]
        if mutation == "early_expiry":
            closed["trigger"] = sell["trigger"] = "expiry"
        elif mutation == "fill_taint":
            sell["tainted"] = True
        elif mutation == "mark_nan":
            account["last_bid"] = "NaN"
        elif mutation == "phantom_position":
            account["position"] = closed.copy()
        elif mutation == "closed_notional":
            account["closed_entry_notional"] = "0"
        else:
            account["decisions"] = 99

    rewrite(directory, change)
    process = audit(directory, 3)
    assert process.returncode != 0
    assert "audit failed" in process.stderr.lower()


def test_rehashed_fixed_end_cannot_change_original_position_levels(study):
    directory, ledger = study
    end = 90 * 86400
    observe(ledger, quote(end - 11))
    at = START + timedelta(seconds=end - 10)
    ledger.record_signal("c" * 64, decision_at=at, bar_end=at, reference_price=D("100"), atr=D("2"), signal_id="late")
    observe(ledger, quote(end - 9))
    observe(ledger, quote(end + 1, bid="104", ask="104.05"))
    assert result(directory, end + 1)["fixed_end"]
    ledger.close()

    def change(events, state):
        fixed = next(e["payload"] for e in events if e["kind"] == "fixed_end")
        fixed["accounts"]["c" * 64]["position"]["stop"] = "1"
        state["fixed_end"] = fixed

    rewrite(directory, change)
    assert audit(directory, end + 1).returncode != 0


def test_rehashed_entry_cannot_bypass_frozen_spread_limit(study):
    directory, ledger = study
    signal(ledger)
    observe(ledger, quote())
    assert result(directory)["trades"][0]["initial_quantity"] == "11.1"
    ledger.close()

    def change(events, state):
        payload = next(e["payload"] for e in events if e["kind"] == "fills")
        payload["quote"]["bid"] = "99"
        payload["source_hash"] = canonical_hash(
            {k: v for k, v in payload["quote"].items() if k not in ("received_at", "processed_at")}
        )
        state["last_quotes"]["BTCUSDT"]["source_hash"] = payload["source_hash"]

    rewrite(directory, change)
    process = audit(directory)
    assert process.returncode != 0
    assert "entry spread" in process.stderr


@pytest.mark.parametrize("atr,quantity", [(".5", "26"), ("2", "11.0")])
def test_rehashed_entry_quantity_must_match_cash_risk_and_lot_sizing(study, atr, quantity):
    directory, ledger = study
    ledger.record_signal(
        "c" * 64, decision_at=START, bar_end=START, reference_price=D("100"), atr=D(atr), signal_id="sizing"
    )
    observe(ledger, quote())
    assert result(directory)["trades"]
    ledger.close()

    def change(events, state):
        fill = next(e["payload"]["fills"][0] for e in events if e["kind"] == "fills")
        account = state["accounts"]["c" * 64]
        position = account["position"]
        units = D(quantity)
        price = D("100.100025")
        fee = units * price * D(".001")
        cost = units * price + fee
        fill["quantity"], fill["fee"] = quantity, str(fee)
        fill["frozen_plan"]["planned_loss"] = str(
            units * (price * D("1.001") - (price - D(atr)) * D(".9995") * D(".999"))
        )
        position.update(
            quantity=quantity, initial_quantity=quantity, entry_cost=str(cost), entry_notional=str(units * price)
        )
        account.update(cash=str(D("10000") - cost), fees=str(fee), slippage=str(units * D("100.05") * D(".0005")))

    rewrite(directory, change)
    process = audit(directory)
    assert process.returncode != 0
    assert "entry quantity" in process.stderr


def test_next_entry_uses_cash_after_partial_and_same_time_final_exit(study):
    directory, ledger = study
    signal(ledger)
    observe(ledger, quote(sequence=1))
    observe(ledger, quote(2, bid="97", ask="97.05", size="5", sequence=2))
    assert result(directory)["trades"][0]["remaining_quantity"] == "6.1"
    observe(ledger, quote(2, bid="97", ask="97.05", size="100", sequence=3))
    at = START + timedelta(seconds=2)
    ledger.record_signal(
        "c" * 64, decision_at=at, bar_end=at, reference_price=D("1"), atr=D(".005"), signal_id="after-loss"
    )
    observe(ledger, quote(3, bid="1", ask="1.0005", size="10000", sequence=4))
    report = result(directory, 3)
    assert report["trades"][0]["status"] == "completed"
    assert report["trades"][1]["initial_quantity"] == "2485.7"


def test_rehashed_fixed_end_cannot_omit_a_candidate_account(study):
    directory, ledger = study
    ledger.summary(now=START + timedelta(days=90))
    assert result(directory, 90 * 86400)["fixed_end"]["accounts"]
    ledger.close()

    def change(events, state):
        fixed = next(e["payload"] for e in events if e["kind"] == "fixed_end")
        fixed["accounts"] = {}
        state["fixed_end"] = fixed

    rewrite(directory, change)
    process = audit(directory, 90 * 86400)
    assert process.returncode != 0
    assert "fixed end candidate/account" in process.stderr


def test_outputs_preserve_collisions_and_reject_source_descendants(study, tmp_path):
    directory, ledger = study
    signal(ledger)
    observe(ledger, quote())
    output = tmp_path / "audits"
    process = audit(directory, output=output)
    assert process.returncode == 0, process.stderr
    assert len(list(output.glob("*.json"))) == 1
    assert len(list(output.glob("*.md"))) == 1
    existing = next(output.glob("*.md"))
    existing.write_text("retained original")
    process = audit(directory, output=output)
    assert process.returncode != 0
    assert existing.read_text() == "retained original"
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    before = retained(directory)
    for destination in (directory, directory / "nested", alias / "nested"):
        assert audit(directory, output=destination).returncode != 0
    assert retained(directory) == before
