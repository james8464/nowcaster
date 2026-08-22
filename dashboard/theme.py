from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

INK = "#17212B"
BLUE = "#2667FF"
GOLD = "#C99000"
ORANGE = "#E66A2C"
SLATE = "#667085"
PALE = "#F4F7FB"
GRID = "#E6EAF0"


def apply_theme() -> None:
    st.markdown(
        f"""
        <style>
        :root {{ --ink: {INK}; --blue: {BLUE}; --pale: {PALE}; }}
        .stApp {{ background: #FAFBFD; color: var(--ink); }}
        [data-testid="stMetric"] {{ background: white; border: 1px solid #E6EAF0; border-radius: 12px; padding: 16px; }}
        [data-testid="stSidebar"] {{ border-right: 1px solid #E6EAF0; }}
        .mode-badge {{ display:inline-block; padding:5px 10px; border-radius:999px; background:#EAF0FF;
          color:#1849A9; font-size:12px; font-weight:700; letter-spacing:.04em; text-transform:uppercase; }}
        .guardrail {{ border-left: 3px solid {GOLD}; padding: 9px 12px; background: #FFFAEB; font-size: 13px; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def style_figure(figure: go.Figure, *, height: int = 380, left_margin: int = 90) -> go.Figure:
    figure.update_layout(
        height=height,
        margin=dict(l=left_margin, r=20, t=60, b=45),
        paper_bgcolor="white",
        plot_bgcolor="white",
        font=dict(family="Inter, -apple-system, BlinkMacSystemFont, sans-serif", color=INK),
        colorway=[BLUE, GOLD, ORANGE, SLATE],
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        hoverlabel=dict(bgcolor="white"),
    )
    figure.update_xaxes(showgrid=False, linecolor=GRID)
    figure.update_yaxes(gridcolor=GRID, zerolinecolor=SLATE)
    return figure
