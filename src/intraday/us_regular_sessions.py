"""Fixed NYSE full-session exclusions for this 2022–2025 research round.

Sources: ICE/NYSE 2022–24 holiday and early-close release
https://www.nasdaq.com/press-release/nyse-group-announces-2022-2023-and-2024-holiday-and-early-closings-calendar-2021-12
ICE/NYSE 2025 release
https://s2.q4cdn.com/154085107/files/doc_news/NYSE-Group-Announces-2025-2026-and-2027-Holiday-and-Early-Closings-Calendar-2024.pdf
and NYSE's January 9, 2025 special-closure notice
https://www.nyse.com/publicdocs/nyse/markets/american-options/rule-interpretations/2025/National_Day_of_Mourning_20250102.pdf

This is the cash-market calendar used to test a SPY-inspired CFD hypothesis;
it is not a statement of the CFD's own trading hours or fill availability.
"""

from datetime import date

_EXCLUDED = frozenset(
    date.fromisoformat(value)
    for value in (
        # 2022: holidays and full-session-ineligible early close.
        "2022-01-17", "2022-02-21", "2022-04-15", "2022-05-30", "2022-06-20",
        "2022-07-04", "2022-09-05", "2022-11-24", "2022-11-25", "2022-12-26",
        # 2023.
        "2023-01-02", "2023-01-16", "2023-02-20", "2023-04-07", "2023-05-29",
        "2023-06-19", "2023-07-03", "2023-07-04", "2023-09-04", "2023-11-23",
        "2023-11-24", "2023-12-25",
        # 2024.
        "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27",
        "2024-06-19", "2024-07-03", "2024-07-04", "2024-09-02", "2024-11-28",
        "2024-11-29", "2024-12-24", "2024-12-25",
        # 2025, including the January 9 national day of mourning.
        "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18",
        "2025-05-26", "2025-06-19", "2025-07-03", "2025-07-04", "2025-09-01",
        "2025-11-27", "2025-11-28", "2025-12-24", "2025-12-25",
    )
)


def is_full_us_cash_session(day: date) -> bool:
    """Fail closed outside the registered years and for known closures."""
    return 2022 <= day.year <= 2025 and day.weekday() < 5 and day not in _EXCLUDED
