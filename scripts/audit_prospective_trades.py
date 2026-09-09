#!/usr/bin/env python3
"""Read one retained prospective snapshot without opening the ledger writer.

Only stdlib is imported. Canonical hashing matches the ledger for JSON values.
Hash consistency detects corruption, not an adversary rewriting all evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

D = Decimal


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def at(value):
    parsed = datetime.fromisoformat(value)
    require(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0), "explicit UTC timestamp required")
    return parsed


def number(value, *, positive=False):
    require(isinstance(value, (str, int)) and not isinstance(value, bool), "invalid economic number")
    parsed = D(value)
    require(parsed.is_finite() and (not positive or parsed > 0), "nonfinite or invalid economic number")
    return parsed


def equal(actual, expected, label):
    require(number(actual) == expected, f"economic mismatch: {label}")


def reference(event):
    return {key: event[key] for key in ("seq", "event_key", "hash", "at")}


def read_snapshot(directory):
    manifest = json.loads((directory / "manifest.json").read_bytes())
    path = (directory / "ledger.sqlite").resolve(strict=True)
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        rows = conn.execute("SELECT seq,event_key,kind,at,payload,previous_hash,hash FROM journal ORDER BY seq")
        previous, events = "0" * 64, []
        for seq, key, kind, time, payload, prev, checksum in rows:
            event = dict(seq=seq, event_key=key, kind=kind, at=time, payload=json.loads(payload), previous_hash=prev)
            require(
                seq == len(events) + 1 and prev == previous and digest(event) == checksum,
                f"journal integrity failure at sequence {seq}",
            )
            at(time)
            events.append(event | {"hash": checksum})
            previous = checksum
        require(
            events and events[0]["kind"] == "manifest" and events[0]["payload"] == manifest, "manifest binding failure"
        )
        states = conn.execute("SELECT singleton,payload,hash FROM state").fetchall()
        require(len(states) == 1 and states[0][0] == 1, "state singleton integrity failure")
        state = json.loads(states[0][1])
        require(
            digest(state) == states[0][2] and state.get("journal_head") == previous, "state hash/head integrity failure"
        )
        valuations = [
            (e["payload"]["candidate_id"], e["payload"]["minute"], e["event_key"])
            for e in events
            if e["kind"] == "valuation"
        ]
        require(
            sorted(valuations) == sorted(conn.execute("SELECT candidate_id,minute,event_key FROM valuations")),
            "valuation index integrity failure",
        )
        gaps = [e["payload"] for e in events if e["kind"] == "gap"]
        if "gaps" in state:
            require(state["gaps"] == gaps, "legacy gap integrity failure")
        else:
            require(state["gap_count"] == len(gaps), "gap count integrity failure")
            intervals = []
            for symbol in sorted(c["symbol"] for c in manifest["candidates"]):
                merged = []
                for lo, hi in sorted(
                    (int(at(g["since"]).timestamp()) // 60, int(at(g["at"]).timestamp()) // 60)
                    for g in gaps
                    if g["symbol"] in (None, symbol)
                ):
                    if lo > hi:
                        continue
                    if merged and lo <= merged[-1][1] + 1:
                        merged[-1][1] = max(merged[-1][1], hi)
                    else:
                        merged.append([lo, hi])
                intervals.extend((symbol, lo, hi) for lo, hi in merged)
            require(
                intervals
                == conn.execute(
                    "SELECT symbol,start_minute,end_minute FROM gap_intervals ORDER BY symbol,start_minute"
                ).fetchall(),
                "gap index integrity failure",
            )
        conn.rollback()
    return manifest, state, events


def validate_decision(event, candidate, manifest):
    payload = event["payload"]
    decision, frozen = payload["decision"], payload["frozen"]
    require(all(frozen[key] == value for key, value in decision.items()), "decision/frozen identity mismatch")
    decided, bar = at(decision["decision_at"]), at(decision["bar_end"])
    require(
        at(manifest["starts_at"]) <= bar <= decided < at(manifest["ends_at"])
        and (decided - bar).total_seconds() <= 30
        and at(event["at"]) == decided,
        "decision causality failure",
    )
    atr = number(decision["atr"], positive=True)
    number(decision["reference_price"], positive=True)
    equal(frozen["stop_distance"], atr * D(str(candidate["stop_atr"])), "stop distance")
    equal(frozen["target_distance"], atr * D(str(candidate["target_atr"])), "target distance")
    require(at(frozen["expires_at"]) == bar + timedelta(hours=candidate["maximum_bars"]), "changed frozen expiry")
    require(
        at(frozen["pending_until"]) == decided + timedelta(seconds=manifest["pending_lifetime_seconds"]),
        "changed pending lifetime",
    )
    require(type(payload["accepted"]) is bool, "invalid accepted flag")


def validate_fill(event, fill, candidate, manifest):
    payload, time = event["payload"], at(event["at"])
    quote = payload["quote"]
    provider, received, processed = (at(quote[key]) for key in ("provider_time", "received_at", "processed_at"))
    require(
        quote["provider"] == "binance" and quote["feed"] == "spot" and quote["symbol"] == candidate["symbol"],
        "fill quote provenance mismatch",
    )
    require(
        at(manifest["starts_at"]) <= provider <= processed <= time
        and received <= processed
        and (provider - received).total_seconds() <= manifest["maximum_provider_clock_lead_seconds"]
        and (time - provider).total_seconds() <= manifest["maximum_quote_age_seconds"],
        "ineligible fill quote time",
    )
    require(fill["side"] in ("buy", "sell"), "unknown fill side")
    quantity, price = number(fill["quantity"], positive=True), number(fill["price"], positive=True)
    bid, ask = number(quote["bid"], positive=True), number(quote["ask"], positive=True)
    require(ask >= bid, "crossed fill quote")
    step = number(payload["lot_step"], positive=True)
    require(quantity % step == 0, "fill quantity violates lot step")
    require(quantity * price >= number(payload["min_notional"], positive=True), "fill below minimum notional")
    buy = fill["side"] == "buy"
    require(
        quantity <= number(quote["ask_size" if buy else "bid_size"], positive=True), "fill exceeds displayed quantity"
    )
    equal(fill["price"], (ask if buy else bid) * (D("1.0005") if buy else D(".9995")), "modeled fill price")
    equal(fill["fee"], quantity * price * D(".001"), "fill fee")
    source_quote = {k: v for k, v in quote.items() if k not in ("received_at", "processed_at")}
    require(digest(source_quote) == payload["source_hash"], "fill source hash mismatch")
    return (
        reference(event)
        | fill
        | {"quote": quote, "modeled_slippage": str(quantity * (ask if buy else bid) * D(".0005"))}
    )


def reconcile(decision_event, fills, closed_event, account, candidate, manifest, now, gaps):
    decision = decision_event["payload"]["decision"]
    frozen = decision_event["payload"]["frozen"]
    require(decision_event["payload"]["accepted"], "fill attached to rejected decision")
    require(
        fills[0]["side"] == "buy" and sum(f["side"] == "buy" for f in fills) == 1,
        "trade must have exactly one original entry",
    )
    entry, exits = fills[0], fills[1:]
    entered = at(entry["at"])
    threshold = at(decision["decision_at"]) + timedelta(milliseconds=manifest["entry_latency_ms"])
    require(
        all(at(entry["quote"][key]) >= threshold for key in ("provider_time", "received_at"))
        and decision_event["seq"] < entry["seq"]
        and entered <= at(frozen["pending_until"])
        and entered < at(manifest["ends_at"]),
        "entry decision/provider/receipt latency failure",
    )
    quantity, price = number(entry["quantity"]), number(entry["price"])
    plan = entry["frozen_plan"]
    stop, target = price - number(frozen["stop_distance"]), price + number(frozen["target_distance"])
    equal(plan["entry"], price, "frozen entry")
    equal(plan["stop"], stop, "frozen stop")
    equal(plan["target"], target, "frozen target")
    require(stop > 0 and (target / price - 1) * 10000 >= manifest["minimum_target_bps"], "invalid entry levels")
    require(plan["expires_at"] == frozen["expires_at"], "changed entry expiry")
    notional = quantity * price
    cost = notional + number(entry["fee"])
    equal(plan["planned_loss"], quantity * (price * D("1.001") - stop * D(".9995") * D(".999")), "planned loss")
    require(number(plan["planned_loss"]) <= D("25"), "entry exceeds frozen risk budget")
    remaining, proceeds, trigger = quantity, D("0"), None
    previous_time, previous_seq = entered, entry["seq"]
    for fill in exits:
        require(
            fill["side"] == "sell" and at(fill["at"]) >= previous_time and fill["seq"] > previous_seq,
            "exit before entry or prior exit",
        )
        previous_time, previous_seq = at(fill["at"]), fill["seq"]
        require(fill["trigger"] in ("stop", "target", "expiry"), "invalid exit trigger")
        if fill["trigger"] == "expiry":
            require(
                at(fill["at"]) >= min(at(frozen["expires_at"]), at(manifest["ends_at"])),
                "expiry fill before frozen deadline",
            )
        expected_taint = any(
            g["symbol"] in (None, candidate["symbol"]) and entry["seq"] < g["seq"] < fill["seq"] for g in gaps
        )
        require(type(fill["tainted"]) is bool and fill["tainted"] == expected_taint, "fill taint mismatch")
        require(trigger is None or trigger == fill["trigger"], "changed latched exit trigger")
        trigger = fill["trigger"]
        remaining -= number(fill["quantity"])
        require(remaining >= 0, "exited more than entry quantity")
        proceeds += number(fill["quantity"]) * number(fill["price"]) - number(fill["fee"])
    recorded = closed_event["payload"] if closed_event else account["position"]
    require(recorded is not None, "filled trade missing open or closed state")
    for key, value in frozen.items():
        require(recorded[key] == value, f"changed frozen field: {key}")
    for key, value in {
        "intended_entry": price,
        "stop": stop,
        "target": target,
        "initial_quantity": quantity,
        "quantity": remaining,
        "entry_notional": notional,
        "entry_cost": cost,
        "proceeds": proceeds,
    }.items():
        equal(recorded[key], value, key)
    require(at(recorded["entered_at"]) == entered, "changed entry timestamp")
    require(recorded["trigger"] in (None, "stop", "target", "expiry"), "invalid recorded trigger")
    require(trigger is None or trigger == recorded["trigger"], "recorded outcome differs from fills")
    trigger = recorded["trigger"]
    finished = at(recorded["closed_at"]) if closed_event else now
    require(finished >= entered, "inspection predates entry")
    tainted = any(
        g["symbol"] in (None, candidate["symbol"])
        and entry["seq"] < g["seq"]
        and (not closed_event or g["seq"] < closed_event["seq"])
        for g in gaps
    )
    require(type(recorded["tainted"]) is bool and recorded["tainted"] == tainted, "taint evidence mismatch")
    pnl, stress = proceeds - cost, notional * D(".0034")
    if closed_event:
        require(
            remaining == 0 and exits and finished == at(exits[-1]["at"]) and finished == at(closed_event["at"]),
            "closed trade quantity/time mismatch",
        )
        equal(recorded["net_pnl"], pnl, "closed net P&L")
        equal(recorded["stressed_pnl"], pnl - stress, "closed stressed P&L")
    else:
        require(remaining > 0, "open trade has no remaining quantity")
    overdue = now >= min(at(frozen["expires_at"]), at(manifest["ends_at"]))
    status = (
        "completed"
        if closed_event
        else ("awaiting_eligible_exit_quote" if overdue or trigger is not None else "awaiting_plan_exit")
    )
    return dict(
        candidate_id=candidate["candidate_id"],
        signal_id=decision["signal_id"],
        symbol=candidate["symbol"],
        strategy_id=candidate["strategy_id"],
        strategy_definition_hash=candidate["strategy_definition_hash"],
        screen_passed=candidate["screen_passed"],
        decision=decision,
        decision_evidence=reference(decision_event),
        frozen_decision=frozen,
        entry_evidence=reference(entry),
        closed_evidence=reference(closed_event) if closed_event else None,
        entry=entry["price"],
        stop=str(stop),
        target=str(target),
        expires_at=frozen["expires_at"],
        entered_at=recorded["entered_at"],
        closed_at=recorded.get("closed_at"),
        held_seconds=(finished - entered).total_seconds(),
        initial_quantity=str(quantity),
        remaining_quantity=str(remaining),
        fills=fills,
        entry_notional=str(notional),
        entry_cost=str(cost),
        net_exit_proceeds=str(proceeds),
        fees=str(sum(number(f["fee"]) for f in fills)),
        modeled_slippage=str(sum(number(f["modeled_slippage"]) for f in fills)),
        net_pnl=str(pnl) if closed_event else None,
        stress_charge=str(stress),
        stressed_pnl=str(pnl - stress) if closed_event else None,
        recorded_net_pnl=recorded.get("net_pnl"),
        recorded_stressed_pnl=recorded.get("stressed_pnl"),
        recorded_matches_recomputed=True if closed_event else None,
        status=status,
        inspection_needed=status == "awaiting_eligible_exit_quote",
        trigger=trigger,
        tainted=tainted,
        post_fixed_end=finished > at(manifest["ends_at"]) if closed_event else False,
    )


def validate_account(account, owned, decision_count):
    complete = [t for t in owned if t["status"] == "completed"]
    open_trades = [t for t in owned if t["status"] != "completed"]
    require(
        len(open_trades) <= 1 and (account["position"] is not None) == bool(open_trades),
        "account position/trade mismatch",
    )
    require(account["decisions"] == decision_count, "account decision count mismatch")
    require(
        account["closed_trades"] == len(complete) and account["fills"] == sum(len(t["fills"]) for t in owned),
        "account trade/fill count mismatch",
    )
    require(
        account["tainted_trades"] == sum(t["tainted"] for t in complete)
        and account["late_closed_trades"] == sum(t["post_fixed_end"] for t in complete),
        "account taint/late count mismatch",
    )
    equal(
        account["closed_entry_notional"],
        sum((number(t["entry_notional"]) for t in complete), D("0")),
        "account closed entry notional",
    )
    equal(
        account["cash"],
        D("10000") + sum(number(t["net_exit_proceeds"]) - number(t["entry_cost"]) for t in owned),
        "account cash",
    )
    for key in ("fees", "slippage"):
        equal(
            account[key],
            sum((number(t["fees" if key == "fees" else "modeled_slippage"]) for t in owned), D("0")),
            f"account {key}",
        )
    if account["last_bid"] is not None:
        number(account["last_bid"], positive=True)
        at(account["mark_at"])


def audit(directory, now=None):
    manifest, state, events = read_snapshot(directory)
    now = now or datetime.now(UTC)
    require(
        now >= at(state["last_at"] or manifest["registered_at"]),
        "inspection time predates retained snapshot; --now is not a historical replay",
    )
    # These factors are the frozen implemented execution contract, not tunable audit assumptions.
    for key, value in {
        "schema_version": 1,
        "slippage_bps": 5,
        "fee_bps": 10,
        "additional_stress_bps": 34,
        "entry_latency_ms": 250,
    }.items():
        require(manifest[key] == value, f"unsupported frozen contract: {key}")
    candidates = {c["candidate_id"]: c for c in manifest["candidates"]}
    require(set(state["accounts"]) == set(candidates), "candidate/account mismatch")
    decisions, fills, closed, cancellations = {}, {}, {}, {}
    gaps = [e["payload"] | {"seq": e["seq"]} for e in events if e["kind"] == "gap"]
    active = {}
    for event in events:
        payload, kind = event["payload"], event["kind"]
        if kind == "decision":
            identity = (payload["decision"]["candidate_id"], payload["decision"]["signal_id"])
            require(identity not in decisions, "duplicate decision")
            validate_decision(event, candidates[identity[0]], manifest)
            require(payload["accepted"] == (identity[0] not in active), "decision acceptance/account_busy mismatch")
            if payload["accepted"]:
                active[identity[0]] = (identity, None)
            decisions[identity] = event
        elif kind == "fills":
            for fill in payload["fills"]:
                identity = (fill["candidate_id"], fill["signal_id"])
                require(
                    identity[0] in active and active[identity[0]][0] == identity,
                    "fill does not match active account decision",
                )
                remaining = active[identity[0]][1]
                if fill["side"] == "buy":
                    require(remaining is None, "entry overlaps active position")
                    active[identity[0]] = (identity, number(fill["quantity"], positive=True))
                else:
                    require(remaining is not None, "exit before entry")
                    remaining -= number(fill["quantity"], positive=True)
                    require(remaining >= 0, "exit exceeds position")
                    if remaining:
                        active[identity[0]] = (identity, remaining)
                    else:
                        del active[identity[0]]
                fills.setdefault(identity, []).append(validate_fill(event, fill, candidates[identity[0]], manifest))
        elif kind in ("closed_trade", "cancellation"):
            identity = (payload["candidate_id"], payload["signal_id"])
            collection = closed if kind == "closed_trade" else cancellations
            require(identity not in collection, f"duplicate {kind}")
            collection[identity] = event
            if kind == "cancellation":
                require(active.get(identity[0]) == (identity, None), "cancellation without matching pending entry")
                del active[identity[0]]
    require(
        set(fills) <= set(decisions) and set(closed) <= set(fills) and set(cancellations) <= set(decisions),
        "orphan trade evidence",
    )
    trades, rejected, unfilled = [], [], []
    for identity, event in decisions.items():
        payload, account = event["payload"], state["accounts"][identity[0]]
        if identity in fills:
            require(identity not in cancellations, "canceled decision has fills")
            trades.append(
                reconcile(
                    event, fills[identity], closed.get(identity), account, candidates[identity[0]], manifest, now, gaps
                )
            )
        else:
            item = payload | {"evidence": reference(event)}
            if payload["accepted"]:
                cancel = cancellations.get(identity)
                item.update(
                    status="canceled" if cancel else "pending_entry",
                    reason=cancel["payload"]["reason"] if cancel else None,
                    cancellation_evidence=reference(cancel) if cancel else None,
                )
                require(cancel is not None or account["pending"] == payload["frozen"], "missing pending decision")
                unfilled.append(item)
            else:
                require(payload["reason"] == "account_busy", "unknown decision rejection")
                rejected.append(item)
    for candidate_id, account in state["accounts"].items():
        owned = [t for t in trades if t["candidate_id"] == candidate_id]
        validate_account(account, owned, sum(identity[0] == candidate_id for identity in decisions))
        pending = active.get(candidate_id)
        expected_pending = (
            decisions[pending[0]]["payload"]["frozen"] if pending is not None and pending[1] is None else None
        )
        require(account["pending"] == expected_pending, "account pending state mismatch")
    fixed_events = [e for e in events if e["kind"] == "fixed_end"]
    require(
        len(fixed_events) <= 1 and state.get("fixed_end") == (fixed_events[0]["payload"] if fixed_events else None),
        "fixed end snapshot mismatch",
    )
    if fixed_events:
        fixed = fixed_events[0]
        require(at(fixed["at"]) == at(manifest["ends_at"]), "fixed end time mismatch")
        for candidate_id, account in fixed["payload"]["accounts"].items():
            owned = []
            for identity, original_fills in fills.items():
                before = [f for f in original_fills if f["seq"] < fixed["seq"]]
                if identity[0] != candidate_id or not before:
                    continue
                completion = closed.get(identity)
                if completion and completion["seq"] >= fixed["seq"]:
                    completion = None
                owned.append(
                    reconcile(
                        decisions[identity],
                        before,
                        completion,
                        account,
                        candidates[candidate_id],
                        manifest,
                        at(fixed["at"]),
                        [g for g in gaps if g["seq"] < fixed["seq"]],
                    )
                )
            validate_account(
                account,
                owned,
                sum(identity[0] == candidate_id and e["seq"] < fixed["seq"] for identity, e in decisions.items()),
            )
    quotes = {}
    for candidate in candidates.values():
        last = state["last_quotes"].get(candidate["symbol"])
        account = state["accounts"][candidate["candidate_id"]]
        age = (now - at(last["provider_time"])).total_seconds() if last else None
        if last:
            require(age >= 0, "inspection time precedes latest quote")
        quotes[candidate["symbol"]] = dict(
            last_quote=last,
            provider_age_seconds=age,
            stale=age is None or age > manifest["maximum_quote_age_seconds"],
            mark_bid=account["last_bid"],
            mark_at=account["mark_at"],
        )
    return dict(
        schema_version=1,
        inspected_at=now.isoformat(),
        study_id=digest(manifest),
        study_directory=str(directory.resolve()),
        journal_head=state["journal_head"],
        journal_events=len(events),
        state_hash=digest(state),
        manifest=manifest,
        auditor_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        integrity="verified_hash_chain_state_manifest_and_indexes",
        paper_only=True,
        trades=trades,
        rejected_decisions=rejected,
        unfilled_decisions=unfilled,
        quotes=quotes,
        fixed_end=state.get("fixed_end"),
        limitations=[
            "The ledger retains decisions, fills and minute valuations, not every quote or indicator context.",
            "This audit cannot prove every intratrade crossing or reconstruct every indicator input.",
            "Hashes establish retained self-consistency, not external authenticity or full no-repaint proof.",
            "A latched trigger may precede the first fill on an unretained quote; the initial crossing is not proven.",
            "Quote/mark age is measured at inspection; report publication age is separate.",
            "Fees and modeled slippage are disclosed; fill prices include slippage, which is not deducted twice.",
            "Historical screen failures and feed-gap taint remain; this audit assigns no confidence or qualification.",
        ],
    )


def markdown(report):
    lines = [
        "# Prospective paper trade audit",
        "",
        f"Inspected: {report['inspected_at']}",
        f"Study: `{report['study_id']}`",
        f"Journal head: `{report['journal_head']}`",
        "",
        "| Symbol / signal | Status | Entry | Stop | Target | Expiry | Net / stressed P&L | Tainted |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for trade in report["trades"]:
        safe_signal = trade["signal_id"].replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {trade['symbol']} / {safe_signal} | {trade['status']} | {trade['entry']} | "
            f"{trade['stop']} | {trade['target']} | {trade['expires_at']} | "
            f"{trade['net_pnl']} / {trade['stressed_pnl']} | {trade['tainted']} |"
        )
    lines.extend(
        [
            "",
            f"Rejected decisions: {len(report['rejected_decisions'])}. "
            f"Unfilled decisions: {len(report['unfilled_decisions'])}.",
            "",
            "Full original identifiers, fill evidence and accounting are in the accompanying JSON.",
            "",
        ]
    )
    lines.extend(f"- {limitation}" for limitation in report["limitations"])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--now", help="Explicit UTC inspection timestamp; defaults to current UTC")
    args = parser.parse_args()
    try:
        directory = args.directory.resolve(strict=True)
        if args.output_dir is not None:
            output = args.output_dir.resolve()
            require(not output.is_relative_to(directory), "output directory must be outside retained study")
        report = audit(directory, at(args.now) if args.now else None)
        content = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
        if args.output_dir is not None:
            stem = "trade-audit-" + digest(report)
            artifacts = {output / (stem + ".json"): content, output / (stem + ".md"): markdown(report)}
            require(
                not any(p.exists() or p.is_symlink() for p in artifacts),
                "output collision; original evidence preserved",
            )
            output.mkdir(parents=True, exist_ok=True)
            for path, data in artifacts.items():
                with path.open("x") as stream:
                    stream.write(data)
            print(json.dumps({"outputs": [str(p) for p in artifacts]}))
        else:
            print(content, end="")
    except (ValueError, KeyError, TypeError, IndexError, InvalidOperation, OSError, sqlite3.Error) as exc:
        print(f"Audit failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
