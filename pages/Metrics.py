"""Admin-only usage dashboard: unique users, visits, and report downloads.

Not linked from anywhere in the UI (the sidebar nav is hidden app-wide) — reach
it directly via its URL, e.g. http://localhost:8501/Metrics
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import streamlit as st

from modules import analytics

st.set_page_config(page_title="Metrics — Climate Zone Finder", page_icon="📊", layout="wide")

if not st.session_state.get("metrics_authed"):
    st.title("🔒 Metrics")

    if not analytics.admin_exists():
        st.caption("No admin account exists yet. Create one now — this only happens once.")
        with st.form("admin_setup"):
            new_username = st.text_input("Admin user ID")
            new_password = st.text_input("Password", type="password")
            confirm_password = st.text_input("Confirm password", type="password")
            create = st.form_submit_button("Create admin account")
        if create:
            if not new_username.strip():
                st.error("Please enter an admin user ID.")
            elif len(new_password) < 8:
                st.error("Password must be at least 8 characters.")
            elif new_password != confirm_password:
                st.error("Passwords do not match.")
            else:
                analytics.create_admin(new_username.strip(), new_password)
                st.session_state["metrics_authed"] = True
                st.session_state["metrics_admin_username"] = new_username.strip()
                st.rerun()
        st.stop()

    st.caption("Admin access only.")
    with st.form("metrics_login"):
        username = st.text_input("Admin user ID")
        pwd = st.text_input("Password", type="password")
        unlock = st.form_submit_button("Sign in")
    if unlock:
        if analytics.verify_admin(username.strip(), pwd):
            st.session_state["metrics_authed"] = True
            st.session_state["metrics_admin_username"] = username.strip()
            st.rerun()
        else:
            st.error("Incorrect user ID or password.")
    st.stop()

st.title("📊 Usage Metrics")
st.caption(f"Signed in as **{st.session_state.get('metrics_admin_username', 'admin')}**")

data = analytics.get_summary_metrics()

col1, col2, col3 = st.columns(3)
col1.metric("Unique Users (logged in)", data["total_users"])
col2.metric("Total Sessions / Visits", data["total_visits"])
col3.metric("Total Report Downloads", data["total_downloads"])

st.divider()

left, right = st.columns(2)

with left:
    st.subheader("Downloads by Report Type")
    if data["downloads_by_report"]:
        df = pd.DataFrame(data["downloads_by_report"]).set_index("report_type")
        st.bar_chart(df)
    else:
        st.info("No downloads recorded yet.")

with right:
    st.subheader("Visits Over Time")
    if data["visits_by_day"]:
        df = pd.DataFrame(data["visits_by_day"]).set_index("day")
        st.line_chart(df)
    else:
        st.info("No visits recorded yet.")

st.subheader("Downloads Over Time")
if data["downloads_by_day"]:
    df = pd.DataFrame(data["downloads_by_day"]).set_index("day")
    st.line_chart(df)
else:
    st.info("No downloads recorded yet.")

st.divider()

st.subheader("Registered Users")
if data["users"]:
    st.dataframe(pd.DataFrame(data["users"]), width="stretch", hide_index=True)
else:
    st.info("No users have logged in yet.")

st.subheader("Recent Downloads")
if data["recent_downloads"]:
    st.dataframe(pd.DataFrame(data["recent_downloads"]), width="stretch", hide_index=True)
else:
    st.info("No downloads recorded yet.")

st.divider()

with st.expander("➕ Add another admin user"):
    with st.form("add_admin"):
        add_username = st.text_input("New admin user ID")
        add_password = st.text_input("New admin password", type="password")
        add_confirm = st.text_input("Confirm password", type="password")
        add_submit = st.form_submit_button("Create")
    if add_submit:
        if not add_username.strip():
            st.error("Please enter a user ID.")
        elif len(add_password) < 8:
            st.error("Password must be at least 8 characters.")
        elif add_password != add_confirm:
            st.error("Passwords do not match.")
        else:
            try:
                analytics.create_admin(add_username.strip(), add_password)
                st.success(f"Admin '{add_username.strip()}' created.")
            except sqlite3.IntegrityError:
                st.error("That user ID already exists.")

if st.button("Lock"):
    st.session_state["metrics_authed"] = False
    st.rerun()
