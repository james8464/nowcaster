"""Observed-close account periods and explicitly retrospective replay reports."""

from __future__ import annotations

from decimal import Context, Decimal, localcontext

import pandas as pd


def period_changes(curve: list[dict], start: str, end: str) -> dict:
    """Carry marks through calendar periods; midnight closes end the prior period."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError("period bounds require ordered timezone-aware timestamps")
    result = {}
    with localcontext(Context(prec=28)):
        for label, frequency in (("months", "M"), ("years", "Y")):
            periods = pd.period_range(
                start.tz_localize(None), (end - pd.Timedelta(nanoseconds=1)).tz_localize(None), freq=frequency
            )
            grouped = {str(period): [] for period in periods}
            previous = None
            for row in curve:
                at = pd.Timestamp(row["at"])
                if not start < at <= end or (previous is not None and at <= previous):
                    raise ValueError("equity curve must contain ordered observed closes within the window")
                previous = at
                key = str((at - pd.Timedelta(nanoseconds=1)).tz_localize(None).to_period(frequency))
                grouped[key].append(row)
            balance = Decimal("10000")
            rows = []
            for period in periods:
                calendar_start = period.start_time.tz_localize("UTC")
                calendar_end = (period + 1).start_time.tz_localize("UTC")
                observed = grouped[str(period)]
                final = Decimal(str(observed[-1]["equity"])) if observed else balance
                expected = int((min(end, calendar_end) - max(start, calendar_start)) / pd.Timedelta(hours=1))
                rows.append(
                    dict(
                        period=str(period),
                        starting_equity=str(balance),
                        ending_equity=str(final),
                        equity_change=str(final - balance),
                        return_=None if balance == 0 else str(final / balance - 1),
                        observed_closes=len(observed),
                        expected_hours=expected,
                        missing_hours=expected - len(observed),
                        partial_calendar_period=start > calendar_start or end < calendar_end,
                    )
                )
                rows[-1]["return"] = rows[-1].pop("return_")
                balance = final
            result[label] = rows
    return result


def quality_profile(bars: pd.DataFrame, start: str, end: str, *, invalid_boundary_rows: int) -> dict:
    opened = pd.to_datetime(bars.open_timestamp, utc=True)
    closed = pd.to_datetime(bars.close_timestamp, utc=True)
    expected = int((pd.Timestamp(end) - pd.Timestamp(start)) / pd.Timedelta(hours=1))
    gaps = []
    for previous, current in zip(closed.iloc[:-1], opened.iloc[1:], strict=True):
        if current > previous:
            gaps.append(
                dict(
                    missing_start=previous.isoformat(),
                    missing_end=current.isoformat(),
                    missing_hours=int((current - previous) / pd.Timedelta(hours=1)),
                )
            )
    leading = int((opened.iloc[0] - pd.Timestamp(start)) / pd.Timedelta(hours=1)) if len(bars) else expected
    trailing = int((pd.Timestamp(end) - closed.iloc[-1]) / pd.Timedelta(hours=1)) if len(bars) else 0
    return dict(
        requested_start=start,
        requested_end_exclusive=end,
        expected_hours=expected,
        observed_hours=len(bars),
        missing_hours=expected - len(bars),
        leading_missing_hours=leading,
        trailing_missing_hours=trailing,
        internal_missing_hours=sum(gap["missing_hours"] for gap in gaps),
        gap_count=len(gaps),
        gaps=gaps,
        first_open=None if bars.empty else opened.iloc[0].isoformat(),
        last_close=None if bars.empty else closed.iloc[-1].isoformat(),
        invalid_boundary_rows=invalid_boundary_rows,
        complete_hour_coverage=expected == len(bars),
    )


def render_report(result: dict) -> str:
    protocol = result["protocol"]
    lines = [
        "# Fixed-rule historical account replay",
        "",
        "This is retrospective development evidence after a 24-trial selection search. All inspected history "
        "remains development data, including any inspected tail. This is not independent validation; failed "
        "historical screens remain binding. No promotion, alerts, account access or orders are authorized.",
        "",
        "Each asset and cost scenario has an independent 10,000 USDT account. Accounts are never added. "
        "Marked equity includes modeled liquidation costs; realized P&L includes completed trades only. "
        "No synthetic terminal liquidation is applied.",
        "",
        "Execution uses close-time archive availability and next-bar-open full fills, not historical quote, "
        "queue, latency or partial-fill evidence. Archives are not point-in-time vintages. Lot sizes are model "
        "assumptions; borrowing, funding, taxes and volume capacity are omitted. Stops win ambiguous bars; "
        "expiry precedes targets after stop checking. Gaps cancel pending entries and close held positions "
        "at the first subsequently observed opening; gap losses remain included.",
        "",
        "Base per-side fee/spread/slippage are 10/2/5 bps. The doubled-cost scenario doubles each input "
        "and changes fills, sizing and cash; it is distinct from the prospective flat extra 34 bps deduction. "
        "Drawdown measures observed hourly closes, not intrabar or tick drawdown.",
        "",
        f"Window: {protocol['start']} through exclusive {protocol['end_exclusive']}.",
        f"Attempt: {protocol['attempt_number']}; source identity: `{protocol['source_identity']}`.",
        f"Discovery SHA-256: `{protocol['discovery_sha256']}`.",
        f"Archive manifest identity: `{protocol['archive_manifest_hash']}`.",
        f"Runtime: `{json_text(protocol['runtime'])}`.",
        "Protocol, raw archive pins, result JSON and per-asset hash-chained events are retained alongside "
        "this report. Hashes demonstrate self-consistency, not independent attestation.",
        "",
    ]
    for asset in result["assets"]:
        quality = asset["quality"]
        lines.extend(
            [
                f"## {asset['candidate']['symbol']}",
                "",
                f"Candidate `{asset['candidate']['candidate_id']}`; original screen passed: "
                f"{asset['candidate']['screen_passed']}.",
                f"Observed {quality['observed_hours']} / {quality['expected_hours']} hours; "
                f"missing {quality['missing_hours']} (leading {quality['leading_missing_hours']}, "
                f"internal {quality['internal_missing_hours']}, trailing {quality['trailing_missing_hours']}). "
                f"Internal gaps: {quality['gap_count']}; excluded invalid-boundary rows: "
                f"{quality['invalid_boundary_rows']}. First open: {quality['first_open']}; "
                f"last close: {quality['last_close']}.",
                "",
            ]
        )
        for scenario in asset["scenarios"]:
            counts = scenario["counts"]
            lines.extend(
                [
                    f"### {'Base' if scenario['cost_multiplier'] == 1 else 'doubled-cost'} account",
                    "",
                    "| Cash | Marked equity | Realized P&L | Marked net P&L | Observed-close max drawdown |",
                    "|---:|---:|---:|---:|---:|",
                    f"| {scenario['cash']} | {scenario['marked_equity']} | {scenario['realized_pnl']} | "
                    f"{scenario['net_pnl']} | {scenario['maximum_drawdown']} |",
                    "",
                    f"Completed trades: {counts['completed_trades']}; wins: {counts['wins']}; "
                    f"losses: {counts['losses']}; breakeven: {counts['breakeven']}. "
                    f"Ambiguous exits: {counts['ambiguous_exits']}; gap-tainted exits: {counts['gap_tainted_exits']}.",
                    f"Fees: {scenario['fees']}; spread cost: {scenario['spread_cost']}; "
                    f"slippage cost: {scenario['slippage_cost']}. "
                    f"Marked liquidation value of retained exposure: {scenario['marked_liquidation_value']}.",
                    f"Retained open position: `{json_text(scenario['open_position'])}`.",
                    f"Retained pending entry: `{json_text(scenario['pending_entry'])}`.",
                    "",
                    "Period changes carry prior equity forward. Midnight closes belong to the period just "
                    "ended. Calendar partial periods and missing-hour coverage are separate labels.",
                    "",
                ]
            )
            for label in ("years", "months"):
                lines.extend(
                    [
                        f"{label.capitalize()}:",
                        "",
                        "| Period | Calendar | Start | End | Equity change | Return | Missing hours |",
                        "|---|---|---:|---:|---:|---:|---:|",
                    ]
                )
                for period in scenario["periods"][label]:
                    lines.append(
                        f"| {period['period']} | {'partial' if period['partial_calendar_period'] else 'complete'} | "
                        f"{period['starting_equity']} | {period['ending_equity']} | {period['equity_change']} | "
                        f"{period['return'] if period['return'] is not None else 'undefined (zero start)'} | "
                        f"{period['missing_hours']} |"
                    )
                lines.append("")
    return "\n".join(lines)


def json_text(value):
    import json

    return json.dumps(value, sort_keys=True, allow_nan=False)
