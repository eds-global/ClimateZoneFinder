"""Usage analytics: login gate, session/page-view logging, and report-download tracking.

Backed by a local SQLite file so no extra infrastructure is required. The DB lives
at <project_root>/data/analytics.db (mount that folder as a volume in Docker so
data survives container restarts — see docker-compose.yml).
"""

import hashlib
import hmac
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "analytics.db"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@st.cache_resource(show_spinner=False)
def _ensure_schema() -> bool:
    """Create tables once per server process."""
    conn = _connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY,
                name TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                visit_count INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                session_id TEXT NOT NULL,
                email TEXT,
                event_type TEXT NOT NULL,
                report_type TEXT,
                detail TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()
    return True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log_event(event_type: str, report_type: str | None = None, detail: str | None = None) -> None:
    _ensure_schema()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO events (timestamp, session_id, email, event_type, report_type, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                _now(),
                st.session_state.get("session_id"),
                st.session_state.get("user_email"),
                event_type,
                report_type,
                detail,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def log_page_view(page_name: str) -> None:
    """Record a visit to a specific page (Home, Analysis, ...).

    Deduplicated per browser session so widget interactions (which rerun the
    whole script) don't produce a flood of duplicate rows.
    """
    flag_key = f"_page_view_logged_{page_name}"
    if st.session_state.get(flag_key):
        return
    st.session_state[flag_key] = True
    _log_event("page_view", detail=page_name)


def log_download(report_type: str, detail: str | None = None) -> None:
    """Record that a report/file was generated and downloaded."""
    _log_event("download", report_type=report_type, detail=detail)


def _record_login(email: str, name: str) -> None:
    now = _now()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO users (email, name, first_seen, last_seen, visit_count)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(email) DO UPDATE SET
                name = excluded.name,
                last_seen = excluded.last_seen,
                visit_count = visit_count + 1
            """,
            (email, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    _log_event("app_visit")


def require_login() -> str:
    """Gate the calling page behind a name + email form.

    Call this immediately after st.set_page_config() on every page. Returns the
    logged-in email once known; otherwise renders the sign-in form and calls
    st.stop(), halting the rest of the page for this run.
    """
    _ensure_schema()

    if st.session_state.get("user_email"):
        return st.session_state["user_email"]

    if "session_id" not in st.session_state:
        st.session_state["session_id"] = uuid.uuid4().hex

    st.markdown(
        '<style>'
        'section[data-testid="stSidebar"] { display: none !important; }'
        'button[kind="header"] { display: none !important; }'
        '</style>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<h2 style="text-align:center; color:#a85c42; margin-top:60px;">CLIMATE ZONE FINDER</h2>'
        '<p style="text-align:center; color:#666;">Please enter your details to continue</p>',
        unsafe_allow_html=True,
    )

    _, mid_col, _ = st.columns([1, 1.2, 1])
    with mid_col:
        with st.form("login_form"):
            name = st.text_input("Name")
            email = st.text_input("Email")
            submitted = st.form_submit_button("Continue", width=200)

        if submitted:
            name = name.strip()
            email = email.strip().lower()
            if not name:
                st.error("Please enter your name.")
            elif not EMAIL_RE.match(email):
                st.error("Please enter a valid email address.")
            else:
                st.session_state["user_email"] = email
                st.session_state["user_name"] = name
                _record_login(email, name)
                st.rerun()

    st.stop()


def get_summary_metrics(recent_limit: int = 200) -> dict:
    """Aggregate data for the admin metrics dashboard."""
    _ensure_schema()
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        total_users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        total_visits = conn.execute(
            "SELECT COUNT(*) AS c FROM events WHERE event_type = 'app_visit'"
        ).fetchone()["c"]
        total_downloads = conn.execute(
            "SELECT COUNT(*) AS c FROM events WHERE event_type = 'download'"
        ).fetchone()["c"]

        downloads_by_report = conn.execute(
            "SELECT COALESCE(report_type, 'Unknown') AS report_type, COUNT(*) AS downloads "
            "FROM events WHERE event_type = 'download' "
            "GROUP BY report_type ORDER BY downloads DESC"
        ).fetchall()

        downloads_by_day = conn.execute(
            "SELECT substr(timestamp, 1, 10) AS day, COUNT(*) AS downloads "
            "FROM events WHERE event_type = 'download' GROUP BY day ORDER BY day"
        ).fetchall()

        visits_by_day = conn.execute(
            "SELECT substr(timestamp, 1, 10) AS day, COUNT(*) AS visits "
            "FROM events WHERE event_type = 'app_visit' GROUP BY day ORDER BY day"
        ).fetchall()

        users = conn.execute(
            "SELECT email, name, first_seen, last_seen, visit_count "
            "FROM users ORDER BY last_seen DESC"
        ).fetchall()

        recent_downloads = conn.execute(
            "SELECT timestamp, email, report_type, detail FROM events "
            "WHERE event_type = 'download' ORDER BY timestamp DESC LIMIT ?",
            (recent_limit,),
        ).fetchall()

        return {
            "total_users": total_users,
            "total_visits": total_visits,
            "total_downloads": total_downloads,
            "downloads_by_report": [dict(r) for r in downloads_by_report],
            "downloads_by_day": [dict(r) for r in downloads_by_day],
            "visits_by_day": [dict(r) for r in visits_by_day],
            "users": [dict(r) for r in users],
            "recent_downloads": [dict(r) for r in recent_downloads],
        }
    finally:
        conn.close()


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000).hex()


def admin_exists() -> bool:
    """Whether any admin account has been created yet."""
    _ensure_schema()
    conn = _connect()
    try:
        return conn.execute("SELECT 1 FROM admin_users LIMIT 1").fetchone() is not None
    finally:
        conn.close()


def create_admin(username: str, password: str) -> None:
    """Create a new admin account with a securely hashed password."""
    salt = os.urandom(16)
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO admin_users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
            (username, _hash_password(password, salt), salt.hex(), _now()),
        )
        conn.commit()
    finally:
        conn.close()


def verify_admin(username: str, password: str) -> bool:
    """Check a username/password pair against stored admin credentials."""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT password_hash, salt FROM admin_users WHERE username = ?", (username,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return False
    computed = _hash_password(password, bytes.fromhex(row["salt"]))
    return hmac.compare_digest(computed, row["password_hash"])
