from __future__ import annotations

import plotly.express as px
import streamlit as st

from dashboard.components import empty_state, page_header, research_guardrail
from dashboard.data import default_database_url, load_model_performance, load_overview
from dashboard.theme import apply_theme, style_figure

apply_theme()
database_url = default_database_url()
overview = load_overview(database_url)
page_header(
    "Alternative-Data Earnings Nowcaster",
    "Point-in-time fundamental forecasting and expectation-variant research",
    overview,
)
research_guardrail()

columns = st.columns(5)
metrics = [
    ("Companies", overview.company_count),
    ("Company-quarters", overview.company_quarter_count),
    ("Attention observations", f"{overview.alternative_observation_count:,}"),
    ("OOS forecasts", f"{overview.historical_forecast_count:,}"),
    ("Event windows", f"{overview.event_return_count:,}"),
]
for column, (label, value) in zip(columns, metrics, strict=True):
    column.metric(label, value)

performance = load_model_performance(database_url)
if performance.empty:
    empty_state("No out-of-sample model results are available.")
else:
    st.subheader("Forecast error by specification")
    st.caption("Mean absolute revenue error; lower is better. Sample counts are shown in hover details.")
    chart = px.bar(
        performance.sort_values("mae"),
        x="mae",
        y="ablation",
        color="model_name",
        orientation="h",
        hover_data=["n", "horizon_days", "mape"],
        labels={"mae": "Mean absolute error", "ablation": "Feature set", "model_name": "Model"},
    )
    chart.update_traces(marker_line_color="#17212B", marker_line_width=0.5)
    st.plotly_chart(style_figure(chart, left_margin=170), width="stretch", theme=None)
    st.caption("Source: persisted expanding-window forecasts in DuckDB. Revenue is reported in issuer filing units.")
