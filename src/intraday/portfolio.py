"""Shared-equity, account-quote paper tickets. Never sends broker orders."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from src.intraday.contracts import InstrumentSpec, MarketQuote
from src.intraday.eligibility import EligibilityResult
from src.intraday.journal import PaperEvent, PaperJournal
from src.intraday.paper import FXConversion
from src.intraday.strategies import SetupDecision

D = Decimal


class LivePaperPortfolio:
    def __init__(self, directory: Path, protocol_hash: str, *, initial_cash: Decimal):
        if initial_cash <= 0:
            raise ValueError("initial paper cash must be positive")
        self.journal = PaperJournal(directory, protocol_hash)
        self.initial_cash = initial_cash
        self.equity = initial_cash
        self.high_water = initial_cash
        self.positions: dict[str, dict[str, str]] = {}
        self.daily_pnl: dict[str, Decimal] = {}
        self.daily_entries: dict[str, int] = {}
        for event in self.journal.events():
            payload = event.payload
            if event.kind == "opened":
                symbol = payload["broker_symbol"]
                if symbol in self.positions:
                    raise ValueError("duplicate retained position")
                self.positions[symbol] = payload
                day = event.occurred_at.date().isoformat()
                self.daily_entries[day] = self.daily_entries.get(day, 0) + 1
            elif event.kind == "closed":
                symbol = payload["broker_symbol"]
                if symbol not in self.positions:
                    raise ValueError("retained close has no position")
                del self.positions[symbol]
                pnl = D(payload["net_pnl_gbp"])
                if not pnl.is_finite():
                    raise ValueError("invalid retained P&L")
                self.equity += pnl
                self.high_water = max(self.high_water, self.equity)
                day = event.occurred_at.date().isoformat()
                self.daily_pnl[day] = self.daily_pnl.get(day, D(0)) + pnl

    def _append(self, kind: str, at: datetime, payload: dict[str, str]) -> PaperEvent:
        with self.journal as writer:
            return writer.append(kind, at, payload)

    def _reject(self, quote: MarketQuote, reason: str, plan: SetupDecision) -> PaperEvent:
        return self._append("no_trade", quote.received_at, {
            "broker_symbol": quote.instrument.broker_symbol, "strategy_id": plan.strategy_id,
            "reason": reason, "source_key": quote.source_key,
        })

    @property
    def open_notional_gbp(self) -> Decimal:
        return sum((D(item["notional_gbp"]) for item in self.positions.values()), D(0))

    def on_decision(
        self,
        instrument: InstrumentSpec,
        plan: SetupDecision,
        quote: MarketQuote,
        eligibility: EligibilityResult,
    ) -> PaperEvent:
        if quote.instrument != instrument or eligibility.instrument != instrument:
            raise ValueError("paper product identity mismatch")
        if plan.status != "ready":
            return self._reject(quote, "no_trade_plan", plan)
        if not eligibility.paper_eligible or eligibility.costs is None or eligibility.conversion_rate is None:
            return self._reject(quote, "product_not_paper_eligible", plan)
        if (
            eligibility.evaluated_at > quote.received_at
            or quote.received_at - eligibility.evaluated_at > timedelta(seconds=15)
        ):
            return self._reject(quote, "product_eligibility_stale", plan)
        if quote.status != "tradeable" or quote.received_at - quote.observed_at > timedelta(seconds=5):
            return self._reject(quote, "account_quote_unavailable", plan)
        if quote.observed_at <= plan.decision_at:
            return self._reject(quote, "quote_precedes_confirmed_decision", plan)
        if (quote.received_at < plan.entry_at or quote.received_at - plan.decision_at > timedelta(seconds=15)
                or quote.received_at >= plan.exit_by):
            return self._reject(quote, "entry_quote_stale", plan)
        symbol = instrument.broker_symbol
        day = quote.received_at.date().isoformat()
        if symbol in self.positions:
            return self._reject(quote, "position_already_open", plan)
        if self.daily_entries.get(day, 0) >= 3:
            return self._reject(quote, "daily_entry_limit", plan)
        if self.daily_pnl.get(day, D(0)) <= -self.initial_cash * D("0.01"):
            return self._reject(quote, "daily_loss_limit", plan)
        if self.equity <= self.high_water * D("0.95"):
            return self._reject(quote, "drawdown_halt", plan)
        costs = eligibility.costs
        rate = eligibility.conversion_rate
        conversion_fee_fraction = costs.conversion_fee_fraction or D(0)
        entry = quote.ask + costs.slippage_points if plan.direction == "long" else quote.bid - costs.slippage_points
        distance = entry - plan.stop if plan.direction == "long" else plan.stop - entry
        if distance <= 0 or entry <= 0:
            return self._reject(quote, "invalid_stop_distance", plan)
        risk_per_unit = (
            (distance + costs.slippage_points) * instrument.point_value
            + costs.commission_per_unit + costs.financing_per_unit
        ) * rate * (D(1) + conversion_fee_fraction)
        notional_per_unit = entry * instrument.point_value * rate
        if risk_per_unit <= 0 or notional_per_unit <= 0:
            return self._reject(quote, "invalid_product_value", plan)
        remaining_notional = max(D(0), self.equity - self.open_notional_gbp)
        units = min(self.equity * D("0.0025") / risk_per_unit,
                    remaining_notional / notional_per_unit).to_integral_value(rounding=ROUND_DOWN)
        if units <= 0:
            return self._reject(quote, "below_minimum_paper_size_or_leverage_cap", plan)
        notional = units * notional_per_unit
        margin = eligibility.broker_margin_rate
        if margin is None or margin <= 0:
            return self._reject(quote, "broker_margin_unavailable", plan)
        estimated_cost = (
            (quote.ask - quote.bid + costs.slippage_points * 2) * instrument.point_value
            + costs.commission_per_unit + costs.financing_per_unit
        ) * units * rate * (D(1) + conversion_fee_fraction)
        payload = {
            "broker_symbol": symbol, "product_label": eligibility.product_label,
            "product": instrument.product, "market": instrument.market,
            "strategy_id": plan.strategy_id, "direction": plan.direction,
            "decided_at": plan.decision_at.isoformat(), "opened_at": quote.received_at.isoformat(),
            "entry": str(entry), "entry_bid": str(quote.bid), "entry_ask": str(quote.ask),
            "stop": str(plan.stop), "target": str(plan.target), "exit_by": plan.exit_by.isoformat(),
            "units": str(units), "point_value": str(instrument.point_value),
            "quote_currency": instrument.quote_currency, "conversion_rate": str(rate),
            "notional_gbp": str(notional), "effective_leverage": str((self.open_notional_gbp + notional) / self.equity),
            "broker_margin_rate": str(margin), "margin_estimate_gbp": str(notional * margin),
            "estimated_roundtrip_cost_gbp": str(estimated_cost),
            "slippage_points": str(costs.slippage_points),
            "commission_per_unit": str(costs.commission_per_unit),
            "financing_per_unit": str(costs.financing_per_unit),
            "conversion_fee_fraction": str(conversion_fee_fraction),
            "cost_source": costs.source, "cost_observed_at": costs.observed_at.isoformat(),
            "account_feed_hash": quote.account_feed_hash, "evidence_hash": plan.evidence_hash,
            "source_key": quote.source_key,
        }
        event = self._append("opened", quote.received_at, payload)
        self.positions[symbol] = payload
        self.daily_entries[day] = self.daily_entries.get(day, 0) + 1
        return event

    def on_quote(self, quote: MarketQuote, conversion: FXConversion | None) -> PaperEvent | None:
        payload = self.positions.get(quote.instrument.broker_symbol)
        if payload is None:
            return None
        if quote.status != "tradeable" or quote.received_at - quote.observed_at > timedelta(seconds=5):
            return None
        opened_at = datetime.fromisoformat(payload["opened_at"])
        if quote.account_feed_hash != payload["account_feed_hash"] or quote.observed_at <= opened_at:
            return None
        if payload["quote_currency"] == "GBP":
            rate = D(1)
        elif (conversion is None or conversion.from_currency != payload["quote_currency"]
              or conversion.to_currency != "GBP" or conversion.observed_at > quote.received_at
              or quote.received_at - conversion.observed_at > timedelta(seconds=15)):
            return None
        else:
            rate = conversion.rate
        direction = payload["direction"]
        executable = quote.bid if direction == "long" else quote.ask
        stop, target = D(payload["stop"]), D(payload["target"])
        if direction == "long":
            reason = "stop" if executable <= stop else "target" if executable >= target else None
        else:
            reason = "stop" if executable >= stop else "target" if executable <= target else None
        if reason is None and quote.received_at >= datetime.fromisoformat(payload["exit_by"]):
            reason = "time_limit"
        if reason is None:
            return None
        slippage = D(payload["slippage_points"])
        exit_price = executable - slippage if direction == "long" else executable + slippage
        signed = exit_price - D(payload["entry"]) if direction == "long" else D(payload["entry"]) - exit_price
        units = D(payload["units"])
        gross = signed * units * D(payload["point_value"]) * rate
        commission = D(payload["commission_per_unit"]) * units * rate
        financing = D(payload["financing_per_unit"]) * units * rate
        conversion_fee = (abs(gross) + commission + financing) * D(payload.get("conversion_fee_fraction", "0"))
        net = gross - commission - financing - conversion_fee
        closed = {
            "broker_symbol": quote.instrument.broker_symbol, "strategy_id": payload["strategy_id"],
            "direction": direction, "exit_reason": reason, "exit_price": str(exit_price),
            "exit_bid": str(quote.bid), "exit_ask": str(quote.ask),
            "units": str(units), "gross_pnl_gbp": str(gross),
            "commission_gbp": str(commission), "financing_gbp": str(financing),
            "conversion_fee_gbp": str(conversion_fee),
            "net_pnl_gbp": str(net), "conversion_rate": str(rate), "source_key": quote.source_key,
        }
        event = self._append("closed", quote.received_at, closed)
        del self.positions[quote.instrument.broker_symbol]
        self.equity += net
        self.high_water = max(self.high_water, self.equity)
        day = quote.received_at.date().isoformat()
        self.daily_pnl[day] = self.daily_pnl.get(day, D(0)) + net
        return event
