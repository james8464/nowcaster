"""Transactional public-quote paper accounting, isolated from trading repositories.

SQLite is the sole durable store. An advisory exclusive writer lock spans its
lifetime. Decisions and economic events are append-only and hash-chained;
materialized state has a separate digest. These are corruption checks, not
signatures against an attacker able to rewrite the whole database.
"""

from __future__ import annotations

import copy
import fcntl
import json
import sqlite3
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from src.live_monitor.types import MarketQuote
from src.research.prospective_statistics import bootstrap_screen
from src.research.prospective_types import StudyManifest
from src.strategies.types import canonical_hash, canonical_json

D = Decimal


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("explicit UTC timestamp required")


def _minute(value: datetime) -> int:
    return int(value.timestamp()) // 60


def _positive(value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError("finite positive Decimal required")


def _floor(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


class ProspectiveLedger:
    def __init__(self, path: Path, manifest: StudyManifest):
        self.path, self.manifest = Path(path), manifest
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = self.path.with_suffix(self.path.suffix + ".lock").open("a+")
        self._conn = None
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock.close()
            raise RuntimeError("another writer owns the prospective ledger") from exc
        try:
            self._conn = sqlite3.connect(self.path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS journal (
                    seq INTEGER PRIMARY KEY, event_key TEXT UNIQUE NOT NULL,
                    kind TEXT NOT NULL, at TEXT NOT NULL, payload TEXT NOT NULL,
                    previous_hash TEXT NOT NULL, hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), payload TEXT NOT NULL, hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS valuations (
                    candidate_id TEXT NOT NULL, minute INTEGER NOT NULL, event_key TEXT NOT NULL,
                    PRIMARY KEY(candidate_id, minute));
                CREATE INDEX IF NOT EXISTS journal_kind ON journal(kind);
            """)
            found = self._conn.execute("SELECT payload FROM journal WHERE seq=1").fetchone()
            if found:
                if not self.verify_integrity():
                    raise ValueError("ledger integrity failure")
                if json.loads(found[0]) != manifest.model_dump(mode="json"):
                    raise ValueError("manifest mismatch; register a new study")
                self._state = json.loads(self._conn.execute("SELECT payload FROM state").fetchone()[0])
            else:
                if self._conn.execute("SELECT COUNT(*) FROM state").fetchone()[0]:
                    raise ValueError("ledger integrity failure: missing manifest")
                self._state = dict(
                    last_at=None,
                    quote_count=0,
                    rejected_quotes=0,
                    gaps=[],
                    last_quotes={},
                    accounts={
                        c.candidate_id: dict(
                            cash="10000",
                            pending=None,
                            position=None,
                            closed_trades=0,
                            tainted_trades=0,
                            late_closed_trades=0,
                            closed_entry_notional="0",
                            fills=0,
                            decisions=0,
                            fees="0",
                            slippage="0",
                            peak="10000",
                            drawdown="0",
                            last_bid=None,
                            mark_at=None,
                            benchmark_entry=None,
                        )
                        for c in manifest.candidates
                    },
                )
                with self._conn:
                    self._append("manifest", "manifest", manifest.registered_at, manifest.model_dump(mode="json"))
                    self._save()
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if not self._lock.closed:
            fcntl.flock(self._lock, fcntl.LOCK_UN)
            self._lock.close()

    def _append(self, key: str, kind: str, at: datetime, payload: dict):
        tail = self._conn.execute("SELECT seq,hash FROM journal ORDER BY seq DESC LIMIT 1").fetchone()
        seq, previous = (tail[0] + 1, tail[1]) if tail else (1, "0" * 64)
        values = dict(seq=seq, event_key=key, kind=kind, at=at.isoformat(), payload=payload, previous_hash=previous)
        self._conn.execute(
            "INSERT INTO journal VALUES(?,?,?,?,?,?,?)",
            (seq, key, kind, values["at"], canonical_json(payload), previous, canonical_hash(values)),
        )

    def _save(self):
        self._state["journal_head"] = self._conn.execute(
            "SELECT hash FROM journal ORDER BY seq DESC LIMIT 1"
        ).fetchone()[0]
        self._conn.execute(
            "INSERT OR REPLACE INTO state VALUES(1,?,?)", (canonical_json(self._state), canonical_hash(self._state))
        )

    def _transaction(self, operation):
        old = copy.deepcopy(self._state)
        try:
            with self._conn:
                operation()
                self._save()
        except BaseException:
            self._state = old
            raise

    def _clock(self, now: datetime):
        _utc(now)
        if self._state["last_at"] and now < _at(self._state["last_at"]):
            raise ValueError("backward clock; observation rejected")
        self._state["last_at"] = now.isoformat()

    def _freeze_end(self):
        """One transactional cutoff; later execution can never rewrite study evidence."""
        if self._state.get("fixed_end") is not None:
            return
        end = self.manifest.ends_at
        self._clock(end)
        for candidate in self.manifest.candidates:
            previous = self._state["last_quotes"].get(candidate.symbol)
            since = _at(previous["at"]) if previous else self.manifest.starts_at
            if (end - since).total_seconds() > self.manifest.maximum_gap_seconds:
                self._gap(end, "unobserved_at_fixed_end", symbol=candidate.symbol, since=since)
        snapshot = dict(
            accounts=copy.deepcopy(self._state["accounts"]),
            coverage={c.candidate_id: self._coverage(c, end) for c in self.manifest.candidates},
            quote_count=self._state["quote_count"],
            rejected_quotes=self._state["rejected_quotes"],
            gaps=len(self._state["gaps"]),
        )
        self._state["fixed_end"] = snapshot
        self._append("fixed_end", "fixed_end", end, snapshot)

    def verify_integrity(self) -> bool:
        try:
            previous, expected = "0" * 64, 1
            for seq, key, kind, at, payload, prev, digest in self._conn.execute("SELECT * FROM journal ORDER BY seq"):
                values = dict(seq=seq, event_key=key, kind=kind, at=at, payload=json.loads(payload), previous_hash=prev)
                if seq != expected or prev != previous or canonical_hash(values) != digest:
                    return False
                previous, expected = digest, seq + 1
            state = self._conn.execute("SELECT payload,hash FROM state WHERE singleton=1").fetchone()
            if not state or canonical_hash(json.loads(state[0])) != state[1] or expected == 1:
                return False
            if json.loads(state[0]).get("journal_head") != previous:
                return False
            # Index integrity: every sampled valuation is linked to its immutable event.
            indexed = self._conn.execute("""SELECT v.candidate_id,v.minute,j.kind,j.payload FROM valuations v
                LEFT JOIN journal j ON j.event_key=v.event_key""")
            for candidate, minute, kind, payload in indexed:
                if kind != "valuation":
                    return False
                event = json.loads(payload)
                if event["candidate_id"] != candidate or event["minute"] != minute:
                    return False
            return (
                self._conn.execute("SELECT COUNT(*) FROM valuations").fetchone()[0]
                == self._conn.execute("SELECT COUNT(*) FROM journal WHERE kind='valuation'").fetchone()[0]
            )
        except (ValueError, KeyError, TypeError, sqlite3.Error):
            return False

    def record_signal(
        self,
        candidate_id: str,
        *,
        decision_at: datetime,
        bar_end: datetime,
        reference_price: Decimal,
        atr: Decimal,
        signal_id: str,
    ):
        _utc(decision_at)
        _utc(bar_end)
        _positive(reference_price)
        _positive(atr)
        candidate = next((c for c in self.manifest.candidates if c.candidate_id == candidate_id), None)
        if candidate is None or not signal_id or len(signal_id) > 256:
            raise ValueError("unknown candidate or invalid signal identity")
        decision = dict(
            candidate_id=candidate_id,
            signal_id=signal_id,
            decision_at=decision_at.isoformat(),
            bar_end=bar_end.isoformat(),
            reference_price=str(reference_price),
            atr=str(atr),
        )
        key = "signal:" + signal_id
        found = self._conn.execute("SELECT payload FROM journal WHERE event_key=?", (key,)).fetchone()
        if found:
            if json.loads(found[0])["decision"] != decision:
                raise ValueError("conflicting signal identity")
            return
        if not self.manifest.starts_at <= bar_end <= decision_at < self.manifest.ends_at:
            raise ValueError("signal outside prospective window or unfinalized bar")
        if (decision_at - bar_end).total_seconds() > 30:
            raise ValueError("old/seed bar cannot produce a prospective entry")
        stop = reference_price - atr * D(candidate.stop_atr)
        target = reference_price + atr * D(str(candidate.target_atr))
        if stop <= 0 or (target / reference_price - 1) * 10000 < self.manifest.minimum_target_bps:
            raise ValueError("invalid stop or insufficient target distance")

        def operation():
            self._clock(decision_at)
            account = self._state["accounts"][candidate_id]
            account["decisions"] += 1
            frozen = decision | dict(
                stop_distance=str(atr * D(candidate.stop_atr)),
                target_distance=str(atr * D(str(candidate.target_atr))),
                expires_at=(bar_end + timedelta(hours=candidate.maximum_bars)).isoformat(),
                pending_until=(decision_at + timedelta(seconds=30)).isoformat(),
            )
            accepted = account["pending"] is None and account["position"] is None
            if accepted:
                account["pending"] = frozen
            self._append(
                key,
                "decision",
                decision_at,
                dict(decision=decision, frozen=frozen, accepted=accepted, reason=None if accepted else "account_busy"),
            )

        self._transaction(operation)

    def _gap(self, at: datetime, reason: str, *, symbol: str | None = None, since: datetime | None = None):
        since = since or self.manifest.starts_at
        gap = dict(at=at.isoformat(), since=since.isoformat(), reason=reason, symbol=symbol)
        self._state["gaps"].append(gap)
        for candidate in self.manifest.candidates:
            if symbol is not None and candidate.symbol != symbol:
                continue
            account = self._state["accounts"][candidate.candidate_id]
            if account["pending"]:
                self._append(
                    "cancel:" + account["pending"]["signal_id"],
                    "cancellation",
                    at,
                    dict(
                        candidate_id=candidate.candidate_id,
                        signal_id=account["pending"]["signal_id"],
                        reason="observation_gap",
                    ),
                )
                account["pending"] = None
            if account["position"]:
                account["position"]["tainted"] = True
        self._append("gap:" + canonical_hash(gap), "gap", at, gap)

    def record_gap(self, *, at: datetime, reason: str):
        _utc(at)
        if not reason:
            raise ValueError("gap reason required")
        # Explicit resume gaps cover from the last trustworthy quote, not restart time.
        last_times = [_at(q["at"]) for q in self._state["last_quotes"].values()]
        since = min(last_times) if last_times else self.manifest.starts_at
        gap = dict(at=at.isoformat(), since=since.isoformat(), reason=reason, symbol=None)
        if self._conn.execute("SELECT 1 FROM journal WHERE event_key=?", ("gap:" + canonical_hash(gap),)).fetchone():
            return

        def operation():
            if at > self.manifest.ends_at:
                self._freeze_end()
            self._clock(at)
            self._gap(at, reason, since=since)

        self._transaction(operation)

    def on_quote(self, quote: MarketQuote, *, now: datetime, lot_step: Decimal, min_notional: Decimal):
        _utc(now)
        _positive(lot_step)
        _positive(min_notional)
        previous = self._state["last_quotes"].get(quote.symbol)
        # Receipt/processing clocks identify deliveries, not new displayed liquidity.
        source_id = canonical_hash(
            dict(
                provider=quote.provider,
                feed=quote.feed,
                symbol=quote.symbol,
                sequence=quote.sequence,
                provider_time=quote.provider_time.isoformat() if quote.sequence is None else None,
            )
        )
        source_hash = canonical_hash(quote.model_dump(mode="json", exclude={"received_at", "processed_at"}))
        event_key = "quote:" + source_id
        recorded = self._conn.execute("SELECT payload FROM journal WHERE event_key=?", (event_key,)).fetchone()
        prior_hash = json.loads(recorded[0])["source_hash"] if recorded else None
        if previous and previous["source_id"] == source_id:
            prior_hash = previous["source_hash"]
        if prior_hash is not None:
            if prior_hash != source_hash:
                raise ValueError("conflicting provider observation")
            return
        if (
            previous
            and quote.sequence is not None
            and previous["sequence"] is not None
            and quote.sequence <= previous["sequence"]
        ):
            raise ValueError("conflicting or backward quote sequence")
        fresh = (
            quote.provider == "binance"
            and quote.feed == "spot"
            and self.manifest.starts_at <= quote.provider_time <= quote.received_at <= quote.processed_at <= now
            and (now - quote.provider_time).total_seconds() <= self.manifest.maximum_quote_age_seconds
        )

        def operation():
            if now > self.manifest.ends_at:
                self._freeze_end()
            self._clock(now)
            if not fresh:
                self._state["rejected_quotes"] += 1
                return
            if previous and quote.provider_time < _at(previous["provider_time"]):
                raise ValueError("backward provider clock")
            last = _at(previous["at"]) if previous else self.manifest.starts_at
            if (now - last).total_seconds() > self.manifest.maximum_gap_seconds:
                self._gap(now, "quote_gap_over_30_seconds", symbol=quote.symbol, since=last)
            self._state["quote_count"] += 1
            self._state["last_quotes"][quote.symbol] = dict(
                at=now.isoformat(),
                source_id=source_id,
                source_hash=source_hash,
                sequence=quote.sequence,
                provider_time=quote.provider_time.isoformat(),
            )
            filled = []
            for candidate in self.manifest.candidates:
                if candidate.symbol != quote.symbol:
                    continue
                account = self._state["accounts"][candidate.candidate_id]
                pending = account["pending"]
                if pending and (now > _at(pending["pending_until"]) or now >= self.manifest.ends_at):
                    self._append(
                        "cancel:" + pending["signal_id"],
                        "cancellation",
                        now,
                        dict(
                            candidate_id=candidate.candidate_id, signal_id=pending["signal_id"], reason="entry_expired"
                        ),
                    )
                    account["pending"] = pending = None
                if pending and (quote.provider_time - _at(pending["decision_at"])).total_seconds() >= 0.25:
                    fill = self._enter(account, pending, quote, now, lot_step, min_notional)
                    if fill:
                        filled.append(fill | {"candidate_id": candidate.candidate_id})
                position = account["position"]
                if position and not any(f["candidate_id"] == candidate.candidate_id for f in filled):
                    fill = self._exit(account, position, quote, now, lot_step, min_notional)
                    if fill:
                        filled.append(fill | {"candidate_id": candidate.candidate_id})
                account["last_bid"], account["mark_at"] = str(quote.bid), now.isoformat()
                if account["benchmark_entry"] is None and now < self.manifest.ends_at:
                    account["benchmark_entry"] = str(quote.ask * D("1.0005") * D("1.001"))
                equity = self._equity(account)
                account["peak"] = str(max(equity, D(account["peak"])))
                account["drawdown"] = str(max(D(account["drawdown"]), 1 - equity / D(account["peak"])))
                minute = _minute(now)
                if (
                    now <= self.manifest.ends_at
                    and not self._conn.execute(
                        "SELECT 1 FROM valuations WHERE candidate_id=? AND minute=?", (candidate.candidate_id, minute)
                    ).fetchone()
                ):
                    key = f"valuation:{candidate.candidate_id}:{minute}"
                    payload = dict(
                        candidate_id=candidate.candidate_id,
                        minute=minute,
                        equity=str(equity),
                        observed_at=now.isoformat(),
                        quote=quote.model_dump(mode="json"),
                    )
                    self._append(key, "valuation", now, payload)
                    self._conn.execute("INSERT INTO valuations VALUES(?,?,?)", (candidate.candidate_id, minute, key))
            if filled:
                self._append(
                    event_key,
                    "fills",
                    now,
                    dict(
                        source_id=source_id,
                        source_hash=source_hash,
                        quote=quote.model_dump(mode="json"),
                        fills=filled,
                        lot_step=str(lot_step),
                        min_notional=str(min_notional),
                    ),
                )

        self._transaction(operation)

    def _enter(self, account, pending, quote, now, lot_step, min_notional):
        price = quote.ask * D("1.0005")
        cost = price * D("1.001")
        stop = price - D(pending["stop_distance"])
        target = price + D(pending["target_distance"])
        risk = cost - stop * D(".9995") * D(".999")
        spread = (quote.ask - quote.bid) / quote.bid * 10000
        if spread > 10 or risk <= 0 or stop <= 0 or (target / price - 1) * 10000 < 68:
            return None
        quantity = _floor(min(D(account["cash"]) * D(".25") / cost, D("25") / risk), lot_step)
        if quantity <= 0 or quote.ask_size is None or quantity > quote.ask_size or quantity * price < min_notional:
            return None
        fee, slip = quantity * price * D(".001"), quantity * quote.ask * D(".0005")
        account["cash"] = str(D(account["cash"]) - quantity * cost)
        account["fees"] = str(D(account["fees"]) + fee)
        account["slippage"] = str(D(account["slippage"]) + slip)
        account["fills"] += 1
        account["position"] = pending | dict(
            intended_entry=str(price),
            stop=str(stop),
            target=str(target),
            quantity=str(quantity),
            initial_quantity=str(quantity),
            entry_cost=str(quantity * cost),
            entry_notional=str(quantity * price),
            proceeds="0",
            entered_at=now.isoformat(),
            trigger=None,
            tainted=False,
        )
        account["pending"] = None
        return dict(
            side="buy",
            signal_id=pending["signal_id"],
            quantity=str(quantity),
            price=str(price),
            fee=str(fee),
            frozen_plan=dict(
                entry=str(price),
                stop=str(stop),
                target=str(target),
                expires_at=pending["expires_at"],
                planned_loss=str(quantity * risk),
            ),
        )

    def _exit(self, account, position, quote, now, lot_step, min_notional):
        if position["trigger"] is None:
            if quote.bid <= D(position["stop"]):
                position["trigger"] = "stop"
            elif now >= min(_at(position["expires_at"]), self.manifest.ends_at):
                position["trigger"] = "expiry"
            elif quote.bid >= D(position["target"]):
                position["trigger"] = "target"
        if position["trigger"] is None or quote.bid_size is None:
            return None
        quantity = _floor(min(D(position["quantity"]), quote.bid_size), lot_step)
        price = quote.bid * D(".9995")
        if quantity <= 0 or quantity * price < min_notional:
            return None
        fee, slip = quantity * price * D(".001"), quantity * quote.bid * D(".0005")
        proceeds = quantity * price - fee
        account["cash"] = str(D(account["cash"]) + proceeds)
        account["fees"] = str(D(account["fees"]) + fee)
        account["slippage"] = str(D(account["slippage"]) + slip)
        account["fills"] += 1
        position["quantity"] = str(D(position["quantity"]) - quantity)
        position["proceeds"] = str(D(position["proceeds"]) + proceeds)
        if D(position["quantity"]) == 0:
            pnl = D(position["proceeds"]) - D(position["entry_cost"])
            trade = position | dict(
                closed_at=now.isoformat(),
                net_pnl=str(pnl),
                stressed_pnl=str(pnl - D(position["entry_notional"]) * D(".0034")),
            )
            self._append("trade:" + position["signal_id"], "closed_trade", now, trade)
            account["closed_trades"] += 1
            account["tainted_trades"] += int(position["tainted"])
            account["late_closed_trades"] += int(now > self.manifest.ends_at)
            account["closed_entry_notional"] = str(D(account["closed_entry_notional"]) + D(position["entry_notional"]))
            account["position"] = None
        return dict(
            side="sell",
            signal_id=position["signal_id"],
            quantity=str(quantity),
            price=str(price),
            fee=str(fee),
            trigger=position["trigger"],
            tainted=position["tainted"],
        )

    @staticmethod
    def _equity(account):
        position = account["position"]
        return D(account["cash"]) + (
            D(position["quantity"]) * D(account["last_bid"] or "0") * D(".9995") * D(".999") if position else D("0")
        )

    def _coverage(self, candidate, until):
        start_minute, end_minute = _minute(self.manifest.starts_at), _minute(until)
        marks = {}
        for (payload,) in self._conn.execute(
            """SELECT j.payload FROM valuations v JOIN journal j
                ON j.event_key=v.event_key WHERE v.candidate_id=? AND v.minute>=? AND v.minute<=?""",
            (candidate.candidate_id, start_minute, end_minute),
        ):
            event = json.loads(payload)
            marks[event["minute"]] = event
        invalid = set()
        for gap in self._state["gaps"]:
            if gap["symbol"] in (None, candidate.symbol):
                invalid.update(
                    range(max(start_minute, _minute(_at(gap["since"]))), min(end_minute, _minute(_at(gap["at"]))) + 1)
                )
        covered = len({minute for minute in marks if start_minute <= minute < end_minute} - invalid)
        expected = max(0, end_minute - start_minute)
        # Exclude partial boundary days; never select isolated profitable trade days.
        first_day = (start_minute + 1439) // 1440
        last_day = end_minute // 1440
        daily = []
        all_complete = True
        for day in range(first_day, last_day):
            a, b = day * 1440, (day + 1) * 1440
            if (
                any(i not in marks or i in invalid for i in range(a, b))
                or b not in marks
                or _at(marks[a]["observed_at"]).second > 2
                or _at(marks[b]["observed_at"]).second > 2
            ):
                all_complete = False
                continue
            daily.append(float(D(marks[b]["equity"]) / D(marks[a]["equity"]) - 1))
        return dict(
            observed_minutes=covered,
            expected_minutes=expected,
            fraction=covered / expected if expected else 0.0,
            complete_daily_returns=daily,
            all_full_utc_days_complete=all_complete,
        )

    def summary(self, *, now: datetime) -> dict:
        _utc(now)
        if self._state["last_at"] and now < _at(self._state["last_at"]):
            raise ValueError("backward summary clock")
        ended = now >= self.manifest.ends_at
        if ended and self._state.get("fixed_end") is None:
            self._transaction(self._freeze_end)
        fixed = self._state.get("fixed_end") if ended else None
        rows = []
        for candidate in self.manifest.candidates:
            live_account = self._state["accounts"][candidate.candidate_id]
            account = fixed["accounts"][candidate.candidate_id] if fixed else live_account
            equity = self._equity(account)
            pnl = equity - D("10000")
            stress = D(account["closed_entry_notional"]) * D(".0034")
            if account["position"]:
                stress += D(account["position"]["entry_notional"]) * D(".0034")
            coverage = fixed["coverage"][candidate.candidate_id] if fixed else self._coverage(candidate, now)
            tainted = account["tainted_trades"]
            mark_deadline = self.manifest.ends_at if fixed else now
            stale = account["mark_at"] is None or (mark_deadline - _at(account["mark_at"])).total_seconds() > 2
            screening = (
                bootstrap_screen(
                    coverage["complete_daily_returns"],
                    study_number=self.manifest.study_number,
                    candidate_count=len(self.manifest.candidates),
                )
                if ended
                else None
            )
            reasons = []
            if not candidate.screen_passed:
                reasons.append("failed_historical_screen")
            if account["closed_trades"] < 100:
                reasons.append("fewer_than_100_closed_trades")
            if account["position"] or account["pending"]:
                reasons.append("outstanding_position_or_pending_entry")
            if live_account["late_closed_trades"]:
                reasons.append("liquidation_after_fixed_end")
            if coverage["fraction"] < 0.99:
                reasons.append("minute_coverage_below_99_percent")
            if tainted:
                reasons.append("gap_tainted_trades")
            if pnl <= 0 or pnl - stress <= 0:
                reasons.append("nonpositive_net_or_stressed_pnl")
            if D(account["drawdown"]) >= D(".1"):
                reasons.append("drawdown_at_least_10_percent")
            if not coverage["all_full_utc_days_complete"]:
                reasons.append("incomplete_daily_observations")
            if ended and (not screening or screening["lower_bound"] is None or screening["lower_bound"] <= 0):
                reasons.append("bootstrap_screen_not_positive_or_unresolved")
            if stale and account["position"]:
                reasons.append("stale_open_valuation")
            benchmark = None
            if account["benchmark_entry"] and account["last_bid"]:
                benchmark = str(D(account["last_bid"]) * D(".9995") * D(".999") / D(account["benchmark_entry"]) - 1)
            rows.append(
                dict(
                    candidate_id=candidate.candidate_id,
                    symbol=candidate.symbol,
                    strategy_id=candidate.strategy_id,
                    historical_screen_passed=candidate.screen_passed,
                    status="collecting"
                    if not ended
                    else ("insufficient_evidence" if reasons else "positive_paper_evidence"),
                    reasons=reasons,
                    cash=account["cash"],
                    equity=str(equity),
                    net_pnl=str(pnl),
                    stressed_pnl=str(pnl - stress),
                    net_return=str(pnl / D("10000")),
                    stressed_return=str((pnl - stress) / D("10000")),
                    costs=dict(fees=account["fees"], slippage=account["slippage"], additional_stress=str(stress)),
                    position_quantity=account["position"]["quantity"] if account["position"] else "0",
                    open_exposure=str(equity - D(account["cash"])),
                    pending_entry=account["pending"] is not None,
                    closed_trades=account["closed_trades"],
                    tainted_trades=tainted,
                    fills=account["fills"],
                    decisions=account["decisions"],
                    maximum_drawdown=account["drawdown"],
                    valuation_at=account["mark_at"],
                    valuation_stale=stale,
                    buy_and_hold_return=benchmark,
                    coverage=copy.deepcopy(coverage),
                    bootstrap=screening,
                    post_end_liquidation=dict(
                        cash=live_account["cash"],
                        equity=str(self._equity(live_account)),
                        net_pnl=str(self._equity(live_account) - D("10000")),
                        closed_trades=live_account["closed_trades"] - account["closed_trades"],
                        fills=live_account["fills"] - account["fills"],
                        position_quantity=live_account["position"]["quantity"] if live_account["position"] else "0",
                        valuation_at=live_account["mark_at"],
                    )
                    if fixed
                    else None,
                )
            )
        status = (
            "collecting"
            if not ended
            else (
                "positive_paper_evidence"
                if any(r["status"] == "positive_paper_evidence" for r in rows)
                else "insufficient_evidence"
            )
        )
        return dict(
            schema_version=1,
            study_id=self.manifest.study_id,
            study_number=self.manifest.study_number,
            paper_only=True,
            status=status,
            starts_at=self.manifest.starts_at.isoformat(),
            ends_at=self.manifest.ends_at.isoformat(),
            updated_at=now.isoformat(),
            candidates=rows,
            evidence_counts=dict(
                quotes=fixed["quote_count"] if fixed else self._state["quote_count"],
                rejected_quotes=fixed["rejected_quotes"] if fixed else self._state["rejected_quotes"],
                decisions=sum(r["decisions"] for r in rows),
                fills=sum(r["fills"] for r in rows),
                gaps=fixed["gaps"] if fixed else len(self._state["gaps"]),
            ),
            limitations=[
                "Independent 10,000 USDT paper accounts approximate USD; never sum them into portfolio performance.",
                "Observed public ticker sizes do not guarantee executable liquidity or real fills.",
                "Fees are frozen model assumptions, not the user's fee tier; stops can gap beyond initial risk.",
                "Stress deducts another 34 bps of entry notional per trade, "
                "in addition to quote spread, fees and slippage.",
                "Approximate seven-day moving-block screening is not a calibrated probability of profit.",
                "Bootstrap assessment requires every full UTC day complete; missing days are not selectively omitted.",
                "Hash checks detect corruption; they are not externally signed tamper-proof attestation.",
                "No automatic real-money promotion or qualified-alert eligibility.",
            ],
        )
