from __future__ import annotations

import plotly.express as px
import streamlit as st

from dashboard.components import empty_state, page_header
from dashboard.data import default_database_url, load_data_quality, load_overview
from dashboard.theme import BLUE, apply_theme, style_figure

apply_theme()
database_url = default_database_url()
overview = load_overview(database_url)
page_header("Data Quality", "Source lineage, freshness, coverage, and validation exceptions", overview)

cards = st.columns(4)
cards[0].metric("Validation issues", overview.quality_issue_count)
cards[1].metric("Financial quarters", overview.company_quarter_count)
cards[2].metric("Attention rows", f"{overview.alternative_observation_count:,}")
cards[3].metric("Latest refresh", overview.latest_refresh or "Not run")
issues, coverage = load_data_quality(database_url)
if coverage.empty:
    empty_state("No source-coverage records are available.")
else:
    chart = px.bar(
        coverage.sort_values("rows"),
        x="rows",
        y="dataset",
        orientation="h",
        hover_data=["source", "latest_refresh"],
        labels={"rows": "Persisted rows", "dataset": "Dataset"},
        title="Persisted coverage by dataset",
        color_discrete_sequence=[BLUE],
    )
    st.plotly_chart(style_figure(chart, left_margin=170), width="stretch", theme=None)
    st.dataframe(coverage, width="stretch", hide_index=True)
if issues.empty:
    st.success("No persisted validation exceptions. This does not imply source completeness or absence of model risk.")
else:
    st.subheader("Validation exceptions")
    st.dataframe(issues, width="stretch", hide_index=True)
st.warning(
    "Demo macro files are latest-revised FRED snapshots and are intentionally excluded from historical features. "
    "Yahoo price snapshots use an unofficial endpoint with no service-level guarantee."
)
