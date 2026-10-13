"""Compare chart hypotheses with retained account-quote decisions without equating fills."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_SYMBOL = "DE30_EUR"
_STRATEGY = "opening_range_15"


@dataclass(frozen=True)
class PineParityRow:
    bar_close_utc: str
    status: str
    pine_direction: str | None
    nowcaster_direction: str | None
    account_delay_ms: int | None


@dataclass(frozen=True)
class PineParityReport:
    rows: tuple[PineParityRow, ...]
    direction_matches: int
    discrepancies: int


def compare_pine_decisions(pine_rows: list[dict], journal_rows: list[dict]) -> PineParityReport:
    """Join only the fixed 07:20 UTC OR15 decision, never chart/order prices."""
    pine: dict[datetime, str] = {}
    for row in pine_rows:
        if (
            row.get("schema_version") != 1
            or row.get("source") != "tradingview_chart"
            or row.get("broker_symbol") != _SYMBOL
            or row.get("strategy_id") != _STRATEGY
            or row.get("direction") not in {"long", "short", "no_trade"}
            or type(row.get("bar_close_utc_ms")) is not int
        ):
            raise ValueError("invalid Pine decision")
        try:
            close = datetime.fromtimestamp(row["bar_close_utc_ms"] / 1000, UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError("invalid Pine timestamp") from exc
        if (close.hour, close.minute, close.second, close.microsecond) != (7, 20, 0, 0):
            raise ValueError("Pine decision is not at the fixed opening-range close")
        if close in pine:
            raise ValueError("duplicate Pine decision")
        pine[close] = row["direction"]

    account: dict[datetime, tuple[str, int]] = {}
    for event in journal_rows:
        if event.get("kind") != "decision":
            continue
        payload = event.get("payload", {})
        if payload.get("broker_symbol") != _SYMBOL or payload.get("strategy_id") != _STRATEGY:
            continue
        observed = datetime.fromisoformat(event["at"])
        if observed.tzinfo is None or observed.utcoffset() != timedelta(0):
            raise ValueError("account decision time must be UTC")
        # OR15 evaluates the 07:15–07:20 bar before the 07:25 bar can close.
        # A late account quote can validly journal a no-trade after 07:20:15.
        if observed.hour != 7 or not 20 <= observed.minute < 25:
            continue
        close = observed.replace(minute=20, second=0, microsecond=0)
        delay_ms = int((observed - close) / timedelta(milliseconds=1))
        if payload.get("status") == "no_trade" and payload.get("direction") == "":
            direction = "no_trade"
        elif payload.get("status") == "ready" and payload.get("direction") in {"long", "short"}:
            direction = payload["direction"]
        else:
            raise ValueError("invalid account decision")
        if delay_ms > 15_000 and direction != "no_trade":
            raise ValueError("late account trade is not a causal OR15 decision")
        if close in account:
            raise ValueError("duplicate account decision")
        account[close] = (direction, delay_ms)

    rows: list[PineParityRow] = []
    for close in sorted(pine.keys() | account.keys()):
        account_direction, delay_ms = account.get(close, (None, None))
        if close not in pine:
            status = "missing_pine"
        elif close not in account:
            status = "missing_nowcaster"
        elif pine[close] == account_direction:
            if account_direction == "no_trade" and delay_ms > 15_000:
                status = "late_account_abstention"
            else:
                status = "abstention_match" if account_direction == "no_trade" else "direction_match"
        else:
            status = "direction_mismatch"
        rows.append(
            PineParityRow(
                close.isoformat().replace("+00:00", "Z"), status, pine.get(close), account_direction, delay_ms
            )
        )
    return PineParityReport(
        tuple(rows),
        sum(row.status == "direction_match" for row in rows),
        sum(row.status not in {"direction_match", "abstention_match"} for row in rows),
    )
