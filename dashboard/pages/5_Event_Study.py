from __future__ import annotations

import plotly.express as px
import streamlit as st

from dashboard.components import empty_state, page_header, research_guardrail
from dashboard.data import default_database_url, load_event_study, load_overview
from dashboard.theme import BLUE, apply_theme, style_figure

apply_theme()
database_url = default_database_url()
overview = load_overview(database_url)
page_header("Event Study", "Market-adjusted returns after pre-event variant signals", overview)
research_guardrail()

frame = load_event_study(database_url)
if frame.empty:
    empty_state("No event-return observations are available.")
else:
    window_options = sorted({(int(row.window_start), int(row.window_end)) for row in frame.itertuples()})
    window = st.sidebar.selectbox("Event window", window_options, format_func=lambda item: f"[{item[0]}, +{item[1]}]")
    filtered = frame[(frame["window_start"] == window[0]) & (frame["window_end"] == window[1])].copy()
    summary = filtered.groupby("variant_bucket", as_index=False).agg(
        mean_abnormal_return=("abnormal_return", "mean"), n=("abnormal_return", "count")
    )
    chart = px.bar(
        summary.sort_values("mean_abnormal_return"),
        x="mean_abnormal_return",
        y="variant_bucket",
        orientation="h",
        hover_data=["n"],
        labels={"mean_abnormal_return": "Mean market-adjusted return", "variant_bucket": "Variant bucket"},
        title="Abnormal return by variant bucket",
        color_discrete_sequence=[BLUE],
    )
    chart.add_vline(x=0, line_color="#344054", line_width=1)
    st.plotly_chart(style_figure(chart), width="stretch", theme=None)
    scatter = px.scatter(
        filtered,
        x="variant_zscore",
        y="abnormal_return",
        color="company_id",
        hover_data=["event_date", "variant_bucket", "expectation_mode"],
        trendline="ols" if len(filtered) >= 20 else None,
        labels={"variant_zscore": "Variant z-score", "abnormal_return": "Market-adjusted return"},
        title="Variant score and subsequent abnormal return",
    )
    scatter.add_hline(y=0, line_color="#667085", line_width=1)
    st.plotly_chart(style_figure(scatter), width="stretch", theme=None)
    st.caption(
        f"n={len(filtered):,}. Repeated model signals and overlapping events mean points are not independent; "
        "statistical significance must be interpreted cautiously."
    )
