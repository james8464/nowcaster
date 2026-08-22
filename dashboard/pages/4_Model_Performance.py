from __future__ import annotations

import plotly.express as px
import streamlit as st

from dashboard.components import empty_state, page_header
from dashboard.data import default_database_url, load_model_performance, load_overview
from dashboard.theme import BLUE, GOLD, apply_theme, style_figure

apply_theme()
database_url = default_database_url()
overview = load_overview(database_url)
page_header("Model Performance", "Expanding-window forecast accuracy and feature-set ablations", overview)

performance = load_model_performance(database_url)
if performance.empty:
    empty_state("No model evaluation rows are available.")
else:
    horizon = st.sidebar.selectbox("Horizon", sorted(performance["horizon_days"].unique()))
    filtered = performance[performance["horizon_days"] == horizon].copy()
    best = filtered.sort_values("mae").iloc[0]
    cards = st.columns(4)
    cards[0].metric("Best specification", f"{best.model_name} · {best.ablation}")
    cards[1].metric("MAE", f"{best.mae / 1e6:.1f}M")
    cards[2].metric("MAPE", f"{best.mape:.1%}")
    cards[3].metric("OOS observations", int(best.n))
    comparison = px.bar(
        filtered.sort_values("mae"),
        x="ablation",
        y="mae",
        color="model_name",
        barmode="group",
        hover_data=["rmse", "mape", "directional_accuracy", "n"],
        labels={"ablation": "Feature set", "mae": "Mean absolute error", "model_name": "Model"},
        title="Forecast MAE by model and feature set",
        color_discrete_sequence=[BLUE, GOLD, "#667085", "#E66A2C"],
    )
    st.plotly_chart(style_figure(comparison), width="stretch", theme=None)
    st.dataframe(filtered.sort_values("mae"), width="stretch", hide_index=True)
    st.caption(
        "Lower error is better. All rows are out-of-sample expanding-window predictions; no random split is used."
    )
