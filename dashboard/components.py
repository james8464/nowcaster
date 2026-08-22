from __future__ import annotations

import streamlit as st

from dashboard.data import OverviewView


def page_header(title: str, subtitle: str, overview: OverviewView) -> None:
    left, right = st.columns([4, 1])
    with left:
        st.title(title)
        st.caption(subtitle)
    with right:
        st.markdown(f'<div class="mode-badge">{overview.data_mode.replace("_", " ")}</div>', unsafe_allow_html=True)
        st.caption(f"Refresh: {overview.latest_refresh or 'not run'}")


def research_guardrail() -> None:
    st.markdown(
        '<div class="guardrail"><strong>Research guardrail:</strong> confidence is a model-quality score, not a '
        "probability of profit. Demo expectations and event dates are transparent proxies.</div>",
        unsafe_allow_html=True,
    )


def empty_state(message: str) -> None:
    st.info(f"{message} Run `make demo` to populate the research database.")
