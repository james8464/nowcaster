from __future__ import annotations

import plotly.express as px
import streamlit as st

from dashboard.components import empty_state, page_header, research_guardrail
from dashboard.data import default_database_url, load_forecast_monitor, load_overview
from dashboard.theme import BLUE, ORANGE, apply_theme, style_figure

apply_theme()
database_url = default_database_url()
overview = load_overview(database_url)
page_header("Forecast Monitor", "Ranked historical model-to-expectation divergences", overview)
research_guardrail()

horizon = st.sidebar.selectbox("Forecast horizon", [7, 14, 30, 1], index=0)
frame = load_forecast_monitor(database_url, horizon)
if frame.empty:
    empty_state(f"No variant signals are available at a {horizon}-day horizon.")
else:
    model_options = sorted(frame["model_name"].unique())
    selected_models = st.sidebar.multiselect("Models", model_options, default=model_options)
    filtered = frame[frame["model_name"].isin(selected_models)].copy()
    chart_frame = filtered.head(25).sort_values("variant")
    chart = px.bar(
        chart_frame,
        x="variant",
        y=chart_frame["company_id"] + " · " + chart_frame["fiscal_quarter"],
        orientation="h",
        color="variant_bucket",
        color_discrete_map={
            "strongly_positive": BLUE,
            "positive": "#7EA1FF",
            "neutral": "#98A2B3",
            "negative": "#F2A47B",
            "strongly_negative": ORANGE,
        },
        hover_data=["model_name", "ablation", "confidence_score", "expectation_mode"],
        labels={"variant": "Forecast vs expectation", "y": "Company-quarter", "variant_bucket": "Variant bucket"},
        title="Largest absolute variants",
    )
    chart.add_vline(x=0, line_color="#344054", line_width=1)
    st.plotly_chart(style_figure(chart, height=560), width="stretch", theme=None)
    st.caption("Expectation mode is a historical proxy in demo data, not actual Wall Street consensus.")
    visible = filtered[
        [
            "company_id",
            "fiscal_quarter",
            "model_name",
            "forecast_revenue",
            "expectation_revenue",
            "variant",
            "variant_zscore",
            "confidence_score",
        ]
    ].head(100)
    st.dataframe(visible, width="stretch", hide_index=True)
