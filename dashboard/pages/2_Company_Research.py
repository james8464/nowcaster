from __future__ import annotations

import plotly.express as px
import streamlit as st

from dashboard.components import empty_state, page_header, research_guardrail
from dashboard.data import default_database_url, load_company_research, load_overview
from dashboard.theme import BLUE, GOLD, apply_theme, style_figure

apply_theme()
database_url = default_database_url()
overview = load_overview(database_url)
page_header("Company Research", "Fundamentals, public attention, and forecast history at issuer grain", overview)
research_guardrail()

companies = ["SBUX", "MCD", "COST"] if overview.company_count else []
if not companies:
    empty_state("No companies are available.")
else:
    company = st.sidebar.selectbox("Company", companies)
    view = load_company_research(database_url, company)
    if view.fundamentals.empty:
        empty_state(f"No quarterly fundamentals are available for {company}.")
    else:
        latest = view.fundamentals.iloc[-1]
        cards = st.columns(3)
        cards[0].metric("Latest reported revenue", f"{latest.revenue / 1e9:.2f}B")
        cards[1].metric("Reported quarter", latest.fiscal_quarter)
        cards[2].metric("Available date", str(latest.available_date))
        fundamentals_chart = px.line(
            view.fundamentals,
            x="period_end",
            y="revenue",
            markers=True,
            title="Quarterly reported revenue",
            labels={"period_end": "Fiscal period end", "revenue": "Revenue"},
        )
        fundamentals_chart.update_traces(line_color=BLUE, marker_symbol="circle")
        st.plotly_chart(style_figure(fundamentals_chart), width="stretch", theme=None)
    if not view.attention.empty:
        monthly = (
            view.attention.assign(month=lambda data: data["observation_date"].astype(str).str[:7])
            .groupby("month", as_index=False)["value"]
            .mean()
        )
        attention_chart = px.line(
            monthly,
            x="month",
            y="value",
            title="Monthly mean Wikipedia pageviews",
            labels={"month": "Month", "value": "Daily pageviews"},
        )
        attention_chart.update_traces(line_color=GOLD)
        st.plotly_chart(style_figure(attention_chart), width="stretch", theme=None)
        st.caption("Source: Wikimedia Analytics public snapshot; one-day availability lag is enforced.")
