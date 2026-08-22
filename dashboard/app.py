from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="Alternative-Data Earnings Nowcaster",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

pages = [
    st.Page("pages/1_Overview.py", title="Overview", icon="📊", default=True),
    st.Page("pages/2_Company_Research.py", title="Company Research", icon="🏢"),
    st.Page("pages/3_Forecast_Monitor.py", title="Forecast Monitor", icon="🎯"),
    st.Page("pages/4_Model_Performance.py", title="Model Performance", icon="📈"),
    st.Page("pages/5_Event_Study.py", title="Event Study", icon="🔬"),
    st.Page("pages/6_Data_Quality.py", title="Data Quality", icon="🛡️"),
]
st.navigation(pages, position="sidebar").run()
