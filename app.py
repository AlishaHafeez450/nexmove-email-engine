# =========================================================
# NEXMOVE ENTERPRISE EMAIL ENGINE
# Premium Professional UI + Persistent Data Storage
# Sidebar: Dashboard, Email Accounts, Compose Email, Logout
# =========================================================

from __future__ import annotations

from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import base64
import json
import re
import smtplib
import time
import uuid

import pandas as pd
import streamlit as st

try:
    from PIL import Image
except ImportError:
    Image = None

# =========================================================
# APP PATHS + PAGE CONFIG
# =========================================================

APP_DIR = Path(__file__).resolve().parent
SESSION_FILE = APP_DIR / "session.json"
DATA_FILE = APP_DIR / "user_data.json"
LOGO_FILE = APP_DIR / "logonew.png"


def load_page_icon():
    """Use the NEXMOVE logo as the browser tab icon when available."""
    if Image is not None and LOGO_FILE.exists():
        try:
            return Image.open(LOGO_FILE)
        except Exception:
            return "📧"
    return "📧"


st.set_page_config(
    page_title="NEXMOVE",
    page_icon=load_page_icon(),
    layout="wide",
    initial_sidebar_state="expanded",
)

# =========================================================
# CONSTANTS
# =========================================================

EMPLOYEES_DB = {
    "admin123": "pass786",
    "Emily": "secure123",
    "member2": "welcome2026",
}

EMAIL_REGEX = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
DAILY_EMAIL_LIMIT = 500
SEND_DELAY_SECONDS = 10

EMAIL_COLUMN_CANDIDATES = [
    "email",
    "email id",
    "email_id",
    "email address",
    "email_address",
    "gmail",
    "gmail address",
    "recipient",
    "recipient email",
    "to",
]

# =========================================================
# THEME COLORS
# =========================================================
# No gradients used anywhere in this UI.

COLOR_BG = "#F4FFFC"
COLOR_CARD = "#FFFFFF"
COLOR_PRIMARY = "#21C7B7"       # NEXMOVE sea green
COLOR_PRIMARY_DARK = "#0EA5A3"
COLOR_PRIMARY_SOFT = "#E8FFFB"
COLOR_TEXT = "#0F172A"
COLOR_MUTED = "#64748B"
COLOR_BORDER = "#BFEFE8"
COLOR_SUCCESS = "#16A34A"
COLOR_DANGER = "#DC2626"
COLOR_WARNING = "#D97706"
COLOR_INFO = "#21C7B7"

# =========================================================
# JSON HELPERS
# =========================================================


def load_json_file(file_path: Path, default_value: Any) -> Any:
    """Safely load JSON data from a file."""
    if not file_path.exists():
        return default_value

    try:
        with file_path.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
        return loaded
    except (json.JSONDecodeError, OSError):
        return default_value


def save_json_file(file_path: Path, data: Any) -> None:
    """Safely save JSON data to a file."""
    with file_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


def default_user_data() -> Dict[str, Any]:
    return {
        "user_emails_db": {},
        "email_counters": {},
        "campaigns": {},
        "send_logs": {},
    }


def migrate_user_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Keep old user_data.json compatible with this newer version."""
    migrated = default_user_data()

    if isinstance(data, dict):
        migrated["user_emails_db"] = data.get("user_emails_db", {}) or {}
        migrated["email_counters"] = data.get("email_counters", {}) or {}
        migrated["campaigns"] = data.get("campaigns", {}) or {}
        migrated["send_logs"] = data.get("send_logs", {}) or {}

    return migrated


def load_saved_session() -> Optional[Dict[str, Any]]:
    return load_json_file(SESSION_FILE, None)


def save_session(user: str) -> None:
    save_json_file(
        SESSION_FILE,
        {
            "logged_in": True,
            "current_user": user,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        },
    )


def clear_session() -> None:
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()


def load_user_data() -> Dict[str, Any]:
    data = load_json_file(DATA_FILE, default_user_data())
    return migrate_user_data(data)


def save_user_data() -> None:
    data = {
        "user_emails_db": st.session_state.user_emails_db,
        "email_counters": st.session_state.email_counters,
        "campaigns": st.session_state.campaigns,
        "send_logs": st.session_state.send_logs,
    }
    save_json_file(DATA_FILE, data)


# =========================================================
# APP HELPERS
# =========================================================


def now_label() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def short_campaign_id() -> str:
    return f"CMP-{uuid.uuid4().hex[:8].upper()}"


def is_valid_email(email: str) -> bool:
    if not isinstance(email, str):
        return False
    return bool(re.match(EMAIL_REGEX, email.strip()))


def encode_secret(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("utf-8")


def decode_secret(value: str) -> str:
    return base64.b64decode(value.encode("utf-8")).decode("utf-8")


def ensure_user_initialized(user_id: str) -> None:
    if "user_emails_db" not in st.session_state:
        st.session_state.user_emails_db = {}
    if "email_counters" not in st.session_state:
        st.session_state.email_counters = {}
    if "campaigns" not in st.session_state:
        st.session_state.campaigns = {}
    if "send_logs" not in st.session_state:
        st.session_state.send_logs = {}

    if user_id:
        st.session_state.user_emails_db.setdefault(user_id, [])
        st.session_state.campaigns.setdefault(user_id, [])
        st.session_state.send_logs.setdefault(user_id, [])


def get_user_accounts(user_id: str) -> List[Dict[str, Any]]:
    ensure_user_initialized(user_id)
    return st.session_state.user_emails_db.get(user_id, [])


def get_user_campaigns(user_id: str) -> List[Dict[str, Any]]:
    ensure_user_initialized(user_id)
    return st.session_state.campaigns.get(user_id, [])


def get_user_logs(user_id: str) -> List[Dict[str, Any]]:
    ensure_user_initialized(user_id)
    return st.session_state.send_logs.get(user_id, [])


def get_total_sent_for_user(user_id: str) -> int:
    accounts = get_user_accounts(user_id)
    total = 0
    for account in accounts:
        total += int(st.session_state.email_counters.get(account.get("email", ""), 0))
    return total


def get_logo_image() -> None:
    if LOGO_FILE.exists():
        st.image(str(LOGO_FILE), width=210)
    else:
        st.markdown("## NEXMOVE")


def get_logo_base64() -> str:
    """Return the logo as base64 for centered HTML rendering."""
    if LOGO_FILE.exists():
        try:
            return base64.b64encode(LOGO_FILE.read_bytes()).decode("utf-8")
        except Exception:
            return ""
    return ""


def detect_email_column(df: pd.DataFrame) -> Optional[str]:
    normalized_map = {str(col).strip().lower(): col for col in df.columns}

    for candidate in EMAIL_COLUMN_CANDIDATES:
        if candidate in normalized_map:
            return normalized_map[candidate]

    # Fallback: find any column name containing email.
    for normalized_name, original_name in normalized_map.items():
        if "email" in normalized_name:
            return original_name

    return None


def read_leads_file(uploaded_file) -> Tuple[pd.DataFrame, int, int]:
    if uploaded_file.name.lower().endswith(".csv"):
        df = pd.read_csv(uploaded_file)
    else:
        df = pd.read_excel(uploaded_file)

    if df.empty:
        raise ValueError("Uploaded file is empty.")

    email_column = detect_email_column(df)
    if not email_column:
        raise ValueError(
            "File must contain an email column. Accepted names: email, email id, email address, recipient email."
        )

    original_count = len(df)
    emails = df[email_column].astype(str).str.strip().str.lower()

    cleaned_df = pd.DataFrame({"email": emails})
    cleaned_df = cleaned_df[cleaned_df["email"].apply(is_valid_email)]
    valid_before_dedup = len(cleaned_df)

    cleaned_df = cleaned_df.drop_duplicates(subset=["email"]).reset_index(drop=True)
    duplicate_or_invalid_removed = original_count - len(cleaned_df)

    return cleaned_df, valid_before_dedup, duplicate_or_invalid_removed


def add_campaign(user_id: str, campaign: Dict[str, Any]) -> None:
    ensure_user_initialized(user_id)
    st.session_state.campaigns[user_id].append(campaign)
    save_user_data()


def add_send_log(user_id: str, log: Dict[str, Any]) -> None:
    ensure_user_initialized(user_id)
    st.session_state.send_logs[user_id].append(log)


def save_logs_after_campaign() -> None:
    save_user_data()


def build_email_body(body: str, sender_email: str) -> str:
    clean_body = body.strip()
    footer = (
        "\n\n---\n"
        f"Sent by {sender_email}\n"
        "If you do not want to receive future emails, please reply with unsubscribe."
    )
    return clean_body + footer


# =========================================================
# SESSION INITIALIZATION
# =========================================================

saved_data = load_user_data()

if "user_emails_db" not in st.session_state:
    st.session_state.user_emails_db = saved_data.get("user_emails_db", {})

if "email_counters" not in st.session_state:
    st.session_state.email_counters = saved_data.get("email_counters", {})

if "campaigns" not in st.session_state:
    st.session_state.campaigns = saved_data.get("campaigns", {})

if "send_logs" not in st.session_state:
    st.session_state.send_logs = saved_data.get("send_logs", {})

if "page" not in st.session_state:
    st.session_state.page = "dashboard"

if "last_success" not in st.session_state:
    st.session_state.last_success = []

if "last_failed" not in st.session_state:
    st.session_state.last_failed = []

saved_session = load_saved_session()

if "logged_in" not in st.session_state:
    if saved_session and saved_session.get("logged_in"):
        saved_user = saved_session.get("current_user")
        if saved_user in EMPLOYEES_DB:
            st.session_state.logged_in = True
            st.session_state.current_user = saved_user
            ensure_user_initialized(saved_user)
        else:
            st.session_state.logged_in = False
            st.session_state.current_user = None
            clear_session()
    else:
        st.session_state.logged_in = False
        st.session_state.current_user = None

# =========================================================
# CSS
# =========================================================

st.markdown(
    f"""
<style>
    :root {{
        --nexmove-primary: {COLOR_PRIMARY};
        --nexmove-primary-dark: {COLOR_PRIMARY_DARK};
        --nexmove-soft: {COLOR_PRIMARY_SOFT};
        --nexmove-bg: {COLOR_BG};
        --nexmove-card: {COLOR_CARD};
        --nexmove-border: {COLOR_BORDER};
        --nexmove-text: {COLOR_TEXT};
        --nexmove-muted: {COLOR_MUTED};
    }}

    .stApp {{
        background: var(--nexmove-bg);
    }}

    header[data-testid="stHeader"] {{
        background: transparent;
    }}

    .block-container {{
        padding-top: 1.25rem;
        padding-left: 2rem;
        padding-right: 2rem;
        padding-bottom: 2rem;
        max-width: 1420px;
    }}

    section[data-testid="stSidebar"] {{
        background: #ffffff !important;
        border-right: 1px solid var(--nexmove-border);
        box-shadow: 8px 0 24px rgba(33, 199, 183, 0.08);
    }}

    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
    section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {{
        color: var(--nexmove-muted) !important;
    }}

    section[data-testid="stSidebar"] .stButton > button {{
        width: 100%;
        height: 48px;
        border-radius: 14px;
        border: 1px solid var(--nexmove-border) !important;
        background: #ffffff !important;
        color: var(--nexmove-text) !important;
        text-align: left;
        padding-left: 16px;
        font-weight: 800;
        box-shadow: 0 6px 16px rgba(15, 23, 42, 0.04);
        transition: all 0.18s ease-in-out;
    }}

    section[data-testid="stSidebar"] .stButton > button:hover {{
        background: var(--nexmove-primary) !important;
        color: #ffffff !important;
        border-color: var(--nexmove-primary) !important;
        transform: translateY(-1px);
        box-shadow: 0 10px 24px rgba(33, 199, 183, 0.22);
    }}

    h1, h2, h3 {{
        color: var(--nexmove-text) !important;
        letter-spacing: -0.025em;
    }}

    p, span, label {{
        color: var(--nexmove-text);
    }}

    div[data-testid="stMetric"] {{
        background: #ffffff;
        border: 1px solid var(--nexmove-border);
        border-top: 5px solid var(--nexmove-primary);
        padding: 20px;
        border-radius: 22px;
        box-shadow: 0 12px 28px rgba(33, 199, 183, 0.10);
    }}

    div[data-testid="stMetricLabel"] {{
        color: var(--nexmove-muted) !important;
        font-weight: 800;
    }}

    div[data-testid="stMetricValue"] {{
        color: var(--nexmove-primary-dark) !important;
        font-weight: 900;
    }}

    div[data-testid="stForm"] {{
        background: #ffffff;
        border: 1px solid var(--nexmove-border);
        border-radius: 24px;
        padding: 26px;
        box-shadow: 0 14px 34px rgba(33, 199, 183, 0.10);
    }}

    div[data-testid="stVerticalBlockBorderWrapper"] {{
        border-color: var(--nexmove-border) !important;
        border-radius: 22px !important;
        box-shadow: 0 12px 28px rgba(33, 199, 183, 0.08);
        background: #ffffff;
    }}

    .stTextInput div[data-baseweb="input"],
    .stTextArea textarea,
    .stSelectbox div[data-baseweb="select"],
    div[data-baseweb="select"] {{
        border-radius: 14px !important;
        border-color: var(--nexmove-border) !important;
        background: #ffffff !important;
    }}

    .stTextInput div[data-baseweb="input"]:focus-within,
    .stTextArea textarea:focus,
    .stSelectbox div[data-baseweb="select"]:focus-within {{
        border-color: var(--nexmove-primary) !important;
        box-shadow: 0 0 0 3px rgba(33, 199, 183, 0.18) !important;
    }}

    .stButton > button,
    .stFormSubmitButton > button {{
        border-radius: 14px !important;
        border: 1px solid var(--nexmove-primary) !important;
        background: var(--nexmove-primary) !important;
        color: #ffffff !important;
        font-weight: 900 !important;
        height: 46px;
        box-shadow: 0 8px 18px rgba(33, 199, 183, 0.22);
    }}

    .stButton > button:hover,
    .stFormSubmitButton > button:hover {{
        background: var(--nexmove-primary-dark) !important;
        border-color: var(--nexmove-primary-dark) !important;
        color: #ffffff !important;
        transform: translateY(-1px);
    }}

    div[data-testid="stDataFrame"] {{
        border: 1px solid var(--nexmove-border);
        border-radius: 18px;
        overflow: hidden;
        box-shadow: 0 10px 24px rgba(33, 199, 183, 0.07);
    }}

    div[data-testid="stAlert"] {{
        border-radius: 16px;
        border: 1px solid var(--nexmove-border);
    }}

    .stProgress > div > div > div > div {{
        background-color: var(--nexmove-primary) !important;
    }}

    hr {{
        border-color: var(--nexmove-border) !important;
    }}

    .compact-stat {{
        background: #ffffff;
        border: 1px solid var(--nexmove-border);
        border-left: 4px solid var(--nexmove-primary);
        border-radius: 14px;
        padding: 8px 12px;
        min-height: 52px;
        box-shadow: 0 6px 14px rgba(33, 199, 183, 0.07);
    }}

    .compact-stat span {{
        display: block;
        color: var(--nexmove-muted);
        font-size: 0.78rem;
        font-weight: 800;
        line-height: 1.1;
    }}

    .compact-stat strong {{
        display: block;
        color: var(--nexmove-primary-dark);
        font-size: 1.05rem;
        font-weight: 900;
        margin-top: 2px;
        line-height: 1.2;
    }}

    .login-brand {{
        text-align: center;
        margin-left: 18px;
        margin-bottom: 20px;
    }}

    .login-brand img {{
        width: 250px;
        margin: 0 auto 8px auto;
        display: block;
    }}

    .login-brand-title {{
        font-size: 1.65rem;
        font-weight: 900;
        color: var(--nexmove-text);
        letter-spacing: -0.02em;
        margin-top: 8px;
    }}

    .login-brand-subtitle {{
        font-size: 0.98rem;
        font-weight: 600;
        color: var(--nexmove-muted);
        margin-top: 6px;
        line-height: 1.5;
    }}

    .sidebar-brand {{
        text-align: center;
        margin-left: 12px;
        margin-right: 12px;
        margin-bottom: 12px;
    }}

    .sidebar-brand img {{
        width: 180px;
        display: block;
        margin: 0 auto 10px auto;
    }}

    .sidebar-signed {{
        display: inline-block;
        background: var(--nexmove-soft);
        color: var(--nexmove-primary-dark);
        border: 1px solid var(--nexmove-border);
        border-radius: 999px;
        padding: 6px 12px;
        font-size: 0.8rem;
        font-weight: 800;
        margin-left: 6px;
    }}

    .account-row-wrapper {{
        padding-top: 4px;
        padding-bottom: 4px;
    }}

    .account-action-spacer {{
        height: 1px;
        margin: 0;
        padding: 0;
    }}

    .permission-row {{
        margin-top: 4px;
        margin-bottom: 8px;
    }}

    .small-muted {{
        color: var(--nexmove-muted);
        font-size: 0.92rem;
    }}
</style>
""",
    unsafe_allow_html=True,
)

# =========================================================
# UI HELPERS
# =========================================================


def page_header(title: str, subtitle: str, icon: str = "") -> None:
    if icon:
        st.title(f"{icon} {title}")
    else:
        st.title(title)
    st.caption(subtitle)
    st.divider()


def show_logo_sidebar(user_id: str) -> None:
    with st.sidebar:
        logo_base64 = get_logo_base64()

        if logo_base64:
            st.markdown(
                f"""
<div class="sidebar-brand">
    <img src="data:image/png;base64,{logo_base64}" alt="NEXMOVE logo">
    <div class="sidebar-signed">Signed in as {user_id}</div>
</div>
""",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"""
<div class="sidebar-brand">
    <h2 style="margin:0;color:var(--nexmove-primary-dark);">NEXMOVE</h2>
    <div class="sidebar-signed">Signed in as {user_id}</div>
</div>
""",
                unsafe_allow_html=True,
            )


# =========================================================
# LOGIN PAGE
# =========================================================


def render_login_page() -> None:
    st.write("")
    col_left, col_center, col_right = st.columns([1, 1.15, 1])

    with col_center:
        st.write("")
        logo_base64 = get_logo_base64()

        if logo_base64:
            st.markdown(
                f"""
<div class="login-brand">
    <img src="data:image/png;base64,{logo_base64}" alt="NEXMOVE logo">
    <div class="login-brand-title">Enterprise Email Engine</div>
    <div class="login-brand-subtitle">
        Sign in to manage sender accounts and send emails with a clean workflow.
    </div>
</div>
""",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                """
<div class="login-brand">
    <div class="login-brand-title">NEXMOVE</div>
    <div class="login-brand-subtitle">
        Sign in to manage sender accounts and send emails with a clean workflow.
    </div>
</div>
""",
                unsafe_allow_html=True,
            )

        with st.form("login_form", clear_on_submit=False):
            user_id = st.text_input("User ID", placeholder="Enter your user ID")
            password = st.text_input("Password", type="password", placeholder="Enter password")
            remember = st.checkbox("Remember me on this device")
            login_btn = st.form_submit_button("Login", width="stretch")

        if login_btn:
            user_id = user_id.strip()

            if user_id in EMPLOYEES_DB and EMPLOYEES_DB[user_id] == password:
                st.session_state.logged_in = True
                st.session_state.current_user = user_id
                ensure_user_initialized(user_id)

                if remember:
                    save_session(user_id)
                else:
                    clear_session()

                save_user_data()
                st.success("Login successful.")
                time.sleep(0.5)
                st.rerun()
            else:
                st.error("Invalid User ID or Password.")


# =========================================================
# SIDEBAR
# =========================================================


def render_sidebar(user_id: str) -> None:
    show_logo_sidebar(user_id)

    if st.sidebar.button("  Dashboard", width="stretch"):
        st.session_state.page = "dashboard"

    if st.sidebar.button("  Email Accounts", width="stretch"):
        st.session_state.page = "emails"

    if st.sidebar.button("  Compose Email", width="stretch"):
        st.session_state.page = "campaign"

    st.sidebar.divider()

    if st.sidebar.button("  Logout", width="stretch"):
        clear_session()
        st.session_state.logged_in = False
        st.session_state.current_user = None
        st.session_state.page = "dashboard"
        st.rerun()


# =========================================================
# DASHBOARD PAGE
# =========================================================


def render_dashboard(user_id: str) -> None:
    accounts = get_user_accounts(user_id)
    campaigns = get_user_campaigns(user_id)
    logs = get_user_logs(user_id)

    total_accounts = len(accounts)
    total_sent = get_total_sent_for_user(user_id)
    total_campaigns = len(campaigns)
    total_failed = sum(1 for log in logs if log.get("status") == "Failed")
    total_success = sum(1 for log in logs if log.get("status") == "Success")
    total_logged = total_success + total_failed
    success_rate = round((total_success / total_logged) * 100, 1) if total_logged else 0

    page_header(
        "Dashboard",
        "A clean overview of sender accounts, composed emails, and recent activity.",
    )

    metric_1, metric_2, metric_3, metric_4 = st.columns(4)
    metric_1.metric("📨 Sender Accounts", total_accounts)
    metric_2.metric("📤 Total Emails Sent", total_sent)
    metric_3.metric("✍️ Emails Composed", total_sent)
    metric_4.metric("✅ Overall Success Rate", f"{success_rate}%")

    st.write("")

    left, right = st.columns([1.2, 0.8])

    with left:
        with st.container(border=True):
            st.subheader("📊 Sender Usage")
            if accounts:
                usage_rows = []
                for account in accounts:
                    email = account.get("email", "")
                    sent = int(st.session_state.email_counters.get(email, 0))
                    remaining = max(DAILY_EMAIL_LIMIT - sent, 0)
                    usage_percent = round((sent / DAILY_EMAIL_LIMIT) * 100, 1)

                    usage_rows.append(
                        {
                            "Sender Email": email,
                            "Sent": sent,
                            "Remaining": remaining,
                            "Usage %": usage_percent,
                        }
                    )

                usage_df = pd.DataFrame(usage_rows)
                st.dataframe(usage_df, width="stretch", hide_index=True)

                for row in usage_rows:
                    st.caption(f"{row['Sender Email']} — {row['Usage %']}% used")
                    st.progress(min(row["Usage %"] / 100, 1.0))
            else:
                st.info("No sender accounts added yet.")

    with right:
        with st.container(border=True):
            st.subheader("🧭 Latest Email Summary")
            if campaigns:
                latest = campaigns[-1]
                st.write(f"**Email Batch ID:** {latest.get('campaign_id', '-')}")
                st.write(f"**Sender:** {latest.get('sender', '-')}")
                st.write(f"**Subject:** {latest.get('subject', '-')}")
                st.write(f"**Valid Leads:** {latest.get('valid_leads', 0)}")
                st.write(f"**Sent:** {latest.get('sent', 0)}")
                st.write(f"**Failed:** {latest.get('failed', 0)}")
                st.write(f"**Date:** {latest.get('created_at', '-')}")
            else:
                st.info("No emails have been sent yet.")

    st.write("")

    bottom_left, bottom_right = st.columns([1.2, 0.8])

    with bottom_left:
        with st.container(border=True):
            st.subheader("🗂️ Recent Email Sends")
            if campaigns:
                recent_campaigns = pd.DataFrame(campaigns[-8:]).iloc[::-1]
                display_columns = [
                    "created_at",
                    "campaign_id",
                    "sender",
                    "subject",
                    "valid_leads",
                    "sent",
                    "failed",
                    "status",
                ]
                existing_columns = [col for col in display_columns if col in recent_campaigns.columns]
                display_df = recent_campaigns[existing_columns].rename(
                    columns={
                        "created_at": "Date",
                        "campaign_id": "Email Batch ID",
                        "sender": "Sender",
                        "subject": "Subject",
                        "valid_leads": "Valid Emails",
                        "sent": "Sent",
                        "failed": "Failed",
                        "status": "Status",
                    }
                )
                st.dataframe(
                    display_df,
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.info("No email send records available yet.")

    with bottom_right:
        with st.container(border=True):
            st.subheader("📌 Recent Recipient Activity")
            if logs:
                recent_logs = pd.DataFrame(logs[-8:]).iloc[::-1]
                display_columns = ["timestamp", "recipient", "status", "sender"]
                existing_columns = [col for col in display_columns if col in recent_logs.columns]
                st.dataframe(recent_logs[existing_columns], width="stretch", hide_index=True)
            else:
                st.info("No recipient logs available yet.")


# =========================================================
# EMAIL ACCOUNTS PAGE
# =========================================================


def render_email_accounts(user_id: str) -> None:
    page_header(
        "Email Accounts",
        "Add and manage Gmail sender accounts. Use Gmail App Passwords, not normal Gmail passwords.",
        "📨",
    )

    accounts = get_user_accounts(user_id)

    with st.form("add_email_form", clear_on_submit=True):
        col1, col2 = st.columns(2)

        with col1:
            new_email = st.text_input("Gmail Address", placeholder="example@gmail.com")

        with col2:
            new_pass = st.text_input(
                "Gmail App Password",
                type="password",
                placeholder="Enter app password",
            )

        btn_left, btn_right = st.columns([0.32, 0.68])
        with btn_left:
            add_btn = st.form_submit_button("Add Email Account", width="stretch")

    if add_btn:
        email_key = new_email.strip().lower()

        if not email_key or not new_pass:
            st.error("Please enter both Gmail address and app password.")
        elif not is_valid_email(email_key):
            st.error("Invalid email format.")
        elif any(acc.get("email") == email_key for acc in accounts):
            st.warning("This email account already exists.")
        else:
            accounts.append(
                {
                    "email": email_key,
                    "app_password": encode_secret(new_pass.strip()),
                    "added_at": now_label(),
                }
            )
            st.session_state.user_emails_db[user_id] = accounts
            st.session_state.email_counters.setdefault(email_key, 0)
            save_user_data()

            st.success("Email account added successfully.")
            time.sleep(0.5)
            st.rerun()

    st.subheader("Saved Sender Accounts")

    if not accounts:
        st.info("No sender emails added yet.")
        return

    for index, account in enumerate(accounts):
        email = account.get("email", "")
        sent = int(st.session_state.email_counters.get(email, 0))
        remaining = max(DAILY_EMAIL_LIMIT - sent, 0)

        with st.container(border=True):
            st.markdown('<div class="account-row-wrapper">', unsafe_allow_html=True)
            col_email, col_sent, col_remaining, col_remove = st.columns([3, 0.85, 0.85, 0.85], vertical_alignment="center")

            with col_email:
                st.write(f"**{email}**")
                st.caption(f"Added: {account.get('added_at', 'Old record')}")

            with col_sent:
                st.markdown(
                    f'<div class="compact-stat"><span>Sent</span><strong>{sent}</strong></div>',
                    unsafe_allow_html=True,
                )

            with col_remaining:
                st.markdown(
                    f'<div class="compact-stat"><span>Remaining</span><strong>{remaining}</strong></div>',
                    unsafe_allow_html=True,
                )

            with col_remove:
                if st.button("Remove", key=f"remove_{index}", width="stretch"):
                    st.session_state.user_emails_db[user_id].pop(index)
                    st.session_state.email_counters.pop(email, None)
                    save_user_data()
                    st.success("Email account removed.")
                    time.sleep(0.5)
                    st.rerun()

            st.markdown("</div>", unsafe_allow_html=True)


# =========================================================
# COMPOSE EMAIL PAGE
# =========================================================


def render_compose_email(user_id: str) -> None:
    page_header(
        "Compose Email",
        "Upload a CSV/XLSX file exported from Google Sheets and send emails with a 10-second gap.",
        "✍️",
    )

    accounts = get_user_accounts(user_id)

    if not accounts:
        st.warning("Please add a sender email first.")
        if st.button("Go to Email Accounts"):
            st.session_state.page = "emails"
            st.rerun()
        return

    email_options = [acc.get("email", "") for acc in accounts if acc.get("email")]

    with st.form("campaign_form", clear_on_submit=False):
        sender = st.selectbox("Select Sender Email", email_options)
        subject = st.text_input("Subject", placeholder="Enter email subject")
        body = st.text_area("Email Body", height=220, placeholder="Write a professional plain-text email...")
        uploaded_file = st.file_uploader("Upload Leads File", type=["csv", "xlsx"])

        st.markdown('<div class="permission-row">', unsafe_allow_html=True)
        permission_confirmed = st.checkbox(
            "I confirm these recipients are allowed to receive this email. This helps reduce spam complaints."
        )
        st.markdown("</div>", unsafe_allow_html=True)

        run_campaign = st.form_submit_button("Send Emails", width="stretch")

    if not run_campaign:
        return

    if uploaded_file is None:
        st.error("Please upload a leads file.")
        return

    if not subject.strip():
        st.error("Email subject is required.")
        return

    if not body.strip():
        st.error("Email body is required.")
        return

    if not permission_confirmed:
        st.error("Please confirm that recipients are allowed to receive this email.")
        return

    sender_account = next((acc for acc in accounts if acc.get("email") == sender), None)

    if sender_account is None:
        st.error("Selected sender account was not found.")
        return

    try:
        leads_df, valid_before_dedup, removed_count = read_leads_file(uploaded_file)
    except Exception as error:
        st.error(str(error))
        return

    if leads_df.empty:
        st.error("No valid recipient email IDs found in the uploaded file.")
        return

    already_sent = int(st.session_state.email_counters.get(sender, 0))
    remaining_limit = max(DAILY_EMAIL_LIMIT - already_sent, 0)

    if remaining_limit <= 0:
        st.error("This sender has reached the daily email limit.")
        return

    if len(leads_df) > remaining_limit:
        st.warning(
            f"Your file has {len(leads_df)} valid unique emails, but this sender has only "
            f"{remaining_limit} sends remaining today. Only first {remaining_limit} emails will be sent."
        )
        leads_df = leads_df.head(remaining_limit)

    try:
        sender_pass = decode_secret(sender_account["app_password"])
    except Exception:
        st.error("Saved app password is invalid. Please remove and add this sender account again.")
        return

    campaign_id = short_campaign_id()
    success: List[str] = []
    failed: List[str] = []

    st.info(
        f"Email sending started. Valid unique emails: {len(leads_df)} | "
        f"Invalid/duplicate removed: {removed_count} | Delay: {SEND_DELAY_SECONDS}s"
    )

    progress = st.progress(0)
    status_text = st.empty()
    server = None

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
        server.starttls()
        server.login(sender, sender_pass)

        total_rows = len(leads_df)

        for index, row in leads_df.iterrows():
            recipient = str(row["email"]).strip().lower()
            status_text.info(f"Sending {index + 1}/{total_rows}: {recipient}")

            try:
                msg = MIMEMultipart()
                msg["From"] = sender
                msg["To"] = recipient
                msg["Subject"] = subject.strip()
                msg["Reply-To"] = sender
                msg.attach(MIMEText(build_email_body(body, sender), "plain"))

                server.sendmail(sender, recipient, msg.as_string())
                success.append(recipient)

                st.session_state.email_counters.setdefault(sender, 0)
                st.session_state.email_counters[sender] += 1

                add_send_log(
                    user_id,
                    {
                        "campaign_id": campaign_id,
                        "timestamp": now_label(),
                        "sender": sender,
                        "recipient": recipient,
                        "subject": subject.strip(),
                        "status": "Success",
                        "error": "",
                    },
                )

            except Exception as send_error:
                failed.append(recipient)

                add_send_log(
                    user_id,
                    {
                        "campaign_id": campaign_id,
                        "timestamp": now_label(),
                        "sender": sender,
                        "recipient": recipient,
                        "subject": subject.strip(),
                        "status": "Failed",
                        "error": str(send_error),
                    },
                )

            progress.progress((index + 1) / total_rows)

            # Required 10-second gap between each email.
            if index < total_rows - 1:
                time.sleep(SEND_DELAY_SECONDS)

    except smtplib.SMTPAuthenticationError:
        st.error("Incorrect Gmail address or Gmail App Password.")
        return

    except Exception as error:
        st.error(f"SMTP Error: {error}")
        return

    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass

    campaign_record = {
        "campaign_id": campaign_id,
        "created_at": now_label(),
        "sender": sender,
        "subject": subject.strip(),
        "valid_leads": int(len(leads_df)),
        "sent": int(len(success)),
        "failed": int(len(failed)),
        "removed_invalid_or_duplicate": int(removed_count),
        "status": "Completed" if success else "Failed",
    }

    add_campaign(user_id, campaign_record)
    save_logs_after_campaign()

    st.session_state.last_success = success
    st.session_state.last_failed = failed

    status_text.empty()

    if success:
        st.success(f"Email sending completed. Sent: {len(success)} | Failed: {len(failed)}")

    if failed:
        st.warning(f"Failed to send to {len(failed)} recipient(s).")
        with st.expander("View Failed Emails"):
            st.dataframe(pd.DataFrame({"Failed Emails": failed}), width="stretch", hide_index=True)


# =========================================================
# MAIN ROUTER
# =========================================================

if not st.session_state.logged_in:
    render_login_page()
else:
    current_user = st.session_state.current_user

    if not current_user:
        clear_session()
        st.session_state.logged_in = False
        st.session_state.current_user = None
        st.rerun()

    ensure_user_initialized(current_user)
    render_sidebar(current_user)

    if st.session_state.page == "dashboard":
        render_dashboard(current_user)
    elif st.session_state.page == "emails":
        render_email_accounts(current_user)
    elif st.session_state.page == "campaign":
        render_compose_email(current_user)
    else:
        st.session_state.page = "dashboard"
        st.rerun()