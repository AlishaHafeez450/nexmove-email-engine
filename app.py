# =========================================================
# NEXMOVE ENTERPRISE EMAIL ENGINE - RBAC VERSION
# Admin + Employee dashboards, persistent JSON storage
# =========================================================
from __future__ import annotations

from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import base64
import hashlib
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

APP_DIR = Path(__file__).resolve().parent
SESSION_FILE = APP_DIR / "session.json"
DATA_FILE = APP_DIR / "user_data.json"
USERS_FILE = APP_DIR / "users.json"
LOGO_FILE = APP_DIR / "logonew.png"

st.set_page_config(page_title="NEXMOVE", layout="wide", initial_sidebar_state="expanded")

EMAIL_REGEX = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
DAILY_EMAIL_LIMIT = 500
SEND_DELAY_SECONDS = 10
ADMIN_ROLE = "admin"
EMPLOYEE_ROLE = "employee"
EMAIL_COLUMN_CANDIDATES = [
    "email", "email id", "email_id", "email address", "email_address", "gmail",
    "gmail address", "recipient", "recipient email", "to",
]

COLOR_BG = "#F4FFFC"
COLOR_CARD = "#FFFFFF"
COLOR_PRIMARY = "#21C7B7"
COLOR_PRIMARY_DARK = "#0EA5A3"
COLOR_PRIMARY_SOFT = "#E8FFFB"
COLOR_TEXT = "#0F172A"
COLOR_MUTED = "#64748B"
COLOR_BORDER = "#BFEFE8"

# ------------------------- JSON + SECURITY -------------------------
def now_label() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_label() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def short_campaign_id() -> str:
    return f"CMP-{uuid.uuid4().hex[:8].upper()}"


def load_json_file(file_path: Path, default_value: Any) -> Any:
    if not file_path.exists():
        return default_value
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return default_value


def save_json_file(file_path: Path, data: Any) -> None:
    file_path.write_text(json.dumps(data, indent=4), encoding="utf-8")


def password_hash(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def verify_password(user: Dict[str, Any], password: str) -> bool:
    if user.get("password_hash"):
        return user["password_hash"] == password_hash(password)
    # Backward compatibility for older/plain JSON records.
    return user.get("password") == password


def make_user(username: str, password: str, role: str, active: bool = True) -> Dict[str, Any]:
    return {
        "username": username.strip(),
        "password_hash": password_hash(password),
        "role": role.lower(),
        "active": bool(active),
        "created_date": now_label(),
        "updated_at": now_label(),
    }


def default_users() -> Dict[str, Any]:
    return {
        "admin123": make_user("admin123", "pass786", ADMIN_ROLE, True),
        "Emily": make_user("Emily", "secure123", EMPLOYEE_ROLE, True),
        "member2": make_user("member2", "welcome2026", EMPLOYEE_ROLE, True),
    }


def migrate_users(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict) or not raw:
        return default_users()
    migrated = {}
    for username, record in raw.items():
        if isinstance(record, str):
            migrated[username] = make_user(username, record, EMPLOYEE_ROLE, True)
            continue
        if not isinstance(record, dict):
            continue
        name = str(record.get("username") or username).strip()
        role = str(record.get("role") or EMPLOYEE_ROLE).lower()
        if role not in [ADMIN_ROLE, EMPLOYEE_ROLE]:
            role = EMPLOYEE_ROLE
        migrated[name] = {
            "username": name,
            "password_hash": record.get("password_hash") or password_hash(str(record.get("password", ""))),
            "role": role,
            "active": bool(record.get("active", True)),
            "created_date": record.get("created_date") or record.get("created_at") or now_label(),
            "updated_at": record.get("updated_at") or now_label(),
        }
    if not any(u.get("role") == ADMIN_ROLE for u in migrated.values()):
        migrated["admin123"] = make_user("admin123", "pass786", ADMIN_ROLE, True)
    return migrated


def load_users() -> Dict[str, Any]:
    users = migrate_users(load_json_file(USERS_FILE, default_users()))
    save_json_file(USERS_FILE, users)
    return users


def save_users() -> None:
    save_json_file(USERS_FILE, st.session_state.users)


def default_user_data() -> Dict[str, Any]:
    return {
        "user_emails_db": {},
        "email_counters": {},
        "campaigns": {},
        "send_logs": {},
        "activity_logs": [],
        "employee_statistics": {},
    }


def migrate_user_data(data: Any) -> Dict[str, Any]:
    base = default_user_data()
    if isinstance(data, dict):
        for key in base:
            base[key] = data.get(key, base[key]) or base[key]
    # Build global activity from legacy send_logs if needed.
    if not base["activity_logs"] and isinstance(base.get("send_logs"), dict):
        for username, logs in base["send_logs"].items():
            for log in logs or []:
                base["activity_logs"].append({
                    "employee_username": username,
                    "campaign_id": log.get("campaign_id", ""),
                    "sender_email": log.get("sender") or log.get("sender_email", ""),
                    "recipient_email": log.get("recipient") or log.get("recipient_email", ""),
                    "subject": log.get("subject", ""),
                    "status": log.get("status", ""),
                    "timestamp": log.get("timestamp", ""),
                    "error": log.get("error", ""),
                })
    return base


def load_user_data() -> Dict[str, Any]:
    data = migrate_user_data(load_json_file(DATA_FILE, default_user_data()))
    save_json_file(DATA_FILE, data)
    return data


def save_user_data() -> None:
    save_json_file(DATA_FILE, {
        "user_emails_db": st.session_state.user_emails_db,
        "email_counters": st.session_state.email_counters,
        "campaigns": st.session_state.campaigns,
        "send_logs": st.session_state.send_logs,
        "activity_logs": st.session_state.activity_logs,
        "employee_statistics": build_employee_statistics(),
    })


def load_saved_session() -> Optional[Dict[str, Any]]:
    return load_json_file(SESSION_FILE, None)


def save_session(username: str) -> None:
    save_json_file(SESSION_FILE, {"logged_in": True, "current_user": username, "saved_at": now_label()})


def clear_session() -> None:
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()

# ------------------------- APP HELPERS -------------------------
def is_valid_email(email: str) -> bool:
    return isinstance(email, str) and bool(re.match(EMAIL_REGEX, email.strip()))


def encode_secret(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("utf-8")


def decode_secret(value: str) -> str:
    return base64.b64decode(value.encode("utf-8")).decode("utf-8")


def validate_gmail_credentials(email: str, app_password: str) -> Tuple[bool, str]:
    """
    Validate Gmail SMTP login before saving the sender account.
    Gmail requires an App Password when 2-Step Verification is enabled.
    """
    server = None
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(email, app_password)
        return True, "Gmail account verified successfully."
    except smtplib.SMTPAuthenticationError:
        return False, "Incorrect Gmail address or Gmail App Password. Please use a valid Gmail App Password."
    except smtplib.SMTPConnectError:
        return False, "Could not connect to Gmail SMTP. Please check your internet connection."
    except smtplib.SMTPServerDisconnected:
        return False, "Gmail SMTP disconnected. Please try again."
    except Exception as error:
        return False, f"Could not verify Gmail account: {error}"
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass


def current_username() -> str:
    return st.session_state.get("current_user") or ""


def current_user_record() -> Dict[str, Any]:
    return st.session_state.users.get(current_username(), {})


def current_role() -> str:
    return current_user_record().get("role", EMPLOYEE_ROLE)


def is_admin() -> bool:
    return current_role() == ADMIN_ROLE


def ensure_user_initialized(username: str) -> None:
    st.session_state.user_emails_db.setdefault(username, [])
    st.session_state.campaigns.setdefault(username, [])
    st.session_state.send_logs.setdefault(username, [])


def guard_page(allowed_roles: List[str]) -> bool:
    user = current_user_record()
    if not user or not user.get("active", False):
        st.error("Your account has been deactivated. Please contact the administrator.")
        clear_session()
        st.stop()
    if user.get("role") not in allowed_roles:
        st.error("You are not authorized to access this page.")
        st.session_state.page = "emails"
        st.stop()
    return True


def get_user_accounts(username: str) -> List[Dict[str, Any]]:
    ensure_user_initialized(username)
    return st.session_state.user_emails_db.get(username, [])


def get_user_campaigns(username: str) -> List[Dict[str, Any]]:
    ensure_user_initialized(username)
    return st.session_state.campaigns.get(username, [])


def get_user_logs(username: str) -> List[Dict[str, Any]]:
    ensure_user_initialized(username)
    return st.session_state.send_logs.get(username, [])


def get_total_sent_for_user(username: str) -> int:
    return sum(1 for log in st.session_state.activity_logs if log.get("employee_username") == username and log.get("status") == "Success")


def user_log_counts(username: str) -> Tuple[int, int, int]:
    logs = [l for l in st.session_state.activity_logs if l.get("employee_username") == username]
    success = sum(1 for l in logs if l.get("status") == "Success")
    failed = sum(1 for l in logs if l.get("status") == "Failed")
    return len(logs), success, failed


def employees_only(include_admin: bool = False) -> Dict[str, Dict[str, Any]]:
    return {u: r for u, r in st.session_state.users.items() if include_admin or r.get("role") == EMPLOYEE_ROLE}


def build_employee_statistics() -> Dict[str, Any]:
    stats = {}
    for username in st.session_state.users:
        total, success, failed = user_log_counts(username)
        stats[username] = {"total_emails": total, "successful_emails": success, "failed_emails": failed, "updated_at": now_label()}
    return stats


def detect_email_column(df: pd.DataFrame) -> Optional[str]:
    normalized_map = {str(col).strip().lower(): col for col in df.columns}
    for candidate in EMAIL_COLUMN_CANDIDATES:
        if candidate in normalized_map:
            return normalized_map[candidate]
    return next((original for normalized, original in normalized_map.items() if "email" in normalized), None)


def read_leads_file(uploaded_file) -> Tuple[pd.DataFrame, int, int]:
    df = pd.read_csv(uploaded_file) if uploaded_file.name.lower().endswith(".csv") else pd.read_excel(uploaded_file)
    if df.empty:
        raise ValueError("Uploaded file is empty.")
    email_column = detect_email_column(df)
    if not email_column:
        raise ValueError("File must contain an email column. Accepted names: email, email id, email address, recipient email.")
    original_count = len(df)
    cleaned_df = pd.DataFrame({"email": df[email_column].astype(str).str.strip().str.lower()})
    cleaned_df = cleaned_df[cleaned_df["email"].apply(is_valid_email)].drop_duplicates(subset=["email"]).reset_index(drop=True)
    removed = original_count - len(cleaned_df)
    return cleaned_df, len(cleaned_df), removed


def add_send_log(username: str, log: Dict[str, Any]) -> None:
    ensure_user_initialized(username)
    st.session_state.send_logs[username].append(log)
    st.session_state.activity_logs.append({
        "employee_username": username,
        "campaign_id": log.get("campaign_id", ""),
        "sender_email": log.get("sender", ""),
        "recipient_email": log.get("recipient", ""),
        "subject": log.get("subject", ""),
        "status": log.get("status", ""),
        "timestamp": log.get("timestamp", now_label()),
        "error": log.get("error", ""),
    })


def build_email_body(body: str, sender_email: str) -> str:
    return body.strip() + "\n\n---\n" + f"Sent by {sender_email}\nIf you do not want to receive future emails, please reply with unsubscribe."


def get_logo_base64() -> str:
    try:
        return base64.b64encode(LOGO_FILE.read_bytes()).decode("utf-8") if LOGO_FILE.exists() else ""
    except Exception:
        return ""

# ------------------------- SESSION INIT -------------------------
if "users" not in st.session_state:
    st.session_state.users = load_users()
saved_data = load_user_data()
for key in default_user_data():
    if key not in st.session_state:
        st.session_state[key] = saved_data.get(key, default_user_data()[key])
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
        user = st.session_state.users.get(saved_user)
        if user and user.get("active"):
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

# ------------------------- CSS -------------------------
st.markdown(f"""
<style>
.stApp {{ background: {COLOR_BG}; }}
header[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 1.25rem; padding-left: 2rem; padding-right: 2rem; padding-bottom: 2rem; max-width: 1420px; }}
section[data-testid="stSidebar"] {{ background: #ffffff !important; border-right: 1px solid {COLOR_BORDER}; box-shadow: 8px 0 24px rgba(33,199,183,.08); }}
section[data-testid="stSidebar"] .stButton > button {{ width: 100%; height: 48px; border-radius: 14px; border: 1px solid {COLOR_BORDER} !important; background: #fff !important; color: {COLOR_TEXT} !important; text-align: left; padding-left: 16px; font-weight: 800; box-shadow: 0 6px 16px rgba(15,23,42,.04); }}
section[data-testid="stSidebar"] .stButton > button:hover {{ background: {COLOR_PRIMARY} !important; color:#fff!important; border-color:{COLOR_PRIMARY}!important; transform:translateY(-1px); }}
h1,h2,h3 {{ color:{COLOR_TEXT}!important; letter-spacing:-.025em; }}
p,span,label {{ color:{COLOR_TEXT}; }}
div[data-testid="stMetric"] {{ background:#fff; border:1px solid {COLOR_BORDER}; border-top:5px solid {COLOR_PRIMARY}; padding:20px; border-radius:22px; box-shadow:0 12px 28px rgba(33,199,183,.10); }}
div[data-testid="stMetricLabel"] {{ color:{COLOR_MUTED}!important; font-weight:800; }}
div[data-testid="stMetricValue"] {{ color:{COLOR_PRIMARY_DARK}!important; font-weight:900; }}
div[data-testid="stForm"], div[data-testid="stVerticalBlockBorderWrapper"] {{ background:#fff; border:1px solid {COLOR_BORDER}; border-radius:24px; padding:22px; box-shadow:0 14px 34px rgba(33,199,183,.10); }}
.stTextInput div[data-baseweb="input"], .stTextArea textarea, .stSelectbox div[data-baseweb="select"] {{ border-radius:14px!important; border-color:{COLOR_BORDER}!important; background:#fff!important; }}
.stButton > button, .stFormSubmitButton > button {{ border-radius:14px!important; border:1px solid {COLOR_PRIMARY}!important; background:{COLOR_PRIMARY}!important; color:#fff!important; font-weight:900!important; height:46px; box-shadow:0 8px 18px rgba(33,199,183,.22); }}
.stButton > button:hover, .stFormSubmitButton > button:hover {{ background:{COLOR_PRIMARY_DARK}!important; border-color:{COLOR_PRIMARY_DARK}!important; color:#fff!important; transform:translateY(-1px); }}
div[data-testid="stDataFrame"] {{ border:1px solid {COLOR_BORDER}; border-radius:18px; overflow:hidden; box-shadow:0 10px 24px rgba(33,199,183,.07); }}
.login-brand,.sidebar-brand {{ text-align:center; margin-bottom:18px; }}
.login-brand img {{ width:250px; margin:0 auto 8px auto; display:block; }}
.sidebar-brand img {{ width:180px; display:block; margin:0 auto 10px auto; }}
.login-brand-title {{ font-size:1.65rem; font-weight:900; color:{COLOR_TEXT}; }}
.login-brand-subtitle {{ font-size:.98rem; font-weight:600; color:{COLOR_MUTED}; line-height:1.5; }}
.sidebar-signed {{ display:inline-block; background:{COLOR_PRIMARY_SOFT}; color:{COLOR_PRIMARY_DARK}; border:1px solid {COLOR_BORDER}; border-radius:999px; padding:6px 12px; font-size:.8rem; font-weight:800; }}
.compact-stat {{ background:#fff; border:1px solid {COLOR_BORDER}; border-left:4px solid {COLOR_PRIMARY}; border-radius:14px; padding:8px 12px; min-height:52px; box-shadow:0 6px 14px rgba(33,199,183,.07); }}
.compact-stat span {{ display:block; color:{COLOR_MUTED}; font-size:.78rem; font-weight:800; }}
.compact-stat strong {{ display:block; color:{COLOR_PRIMARY_DARK}; font-size:1.05rem; font-weight:900; margin-top:2px; }}
</style>
""", unsafe_allow_html=True)

# ------------------------- UI HELPERS -------------------------
def page_header(title: str, subtitle: str, icon: str = "") -> None:
    st.title(f"{icon} {title}" if icon else title)
    st.caption(subtitle)
    st.divider()


def show_logo_sidebar(username: str) -> None:
    role = current_role().title()
    logo = get_logo_base64()
    with st.sidebar:
        if logo:
            st.markdown(f'<div class="sidebar-brand"><img src="data:image/png;base64,{logo}"><div class="sidebar-signed">{username} • {role}</div></div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="sidebar-brand"><h2>NEXMOVE</h2><div class="sidebar-signed">{username} • {role}</div></div>', unsafe_allow_html=True)

# ------------------------- LOGIN -------------------------
def render_login_page() -> None:
    st.write("")
    _, col, _ = st.columns([1, 1.15, 1])
    with col:
        logo = get_logo_base64()
        if logo:
            st.markdown(f'<div class="login-brand"><img src="data:image/png;base64,{logo}"><div class="login-brand-title">Enterprise Email Engine</div><div class="login-brand-subtitle">Role-based email management for admins and employees.</div></div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="login-brand"><div class="login-brand-title">NEXMOVE</div><div class="login-brand-subtitle">Role-based email management for admins and employees.</div></div>', unsafe_allow_html=True)
        with st.form("login_form", clear_on_submit=False):
            username = st.text_input("Username", placeholder="Enter username")
            password = st.text_input("Password", type="password", placeholder="Enter password")
            remember = st.checkbox("Remember me on this device")
            login_btn = st.form_submit_button("Login", width="stretch")
        if login_btn:
            username = username.strip()
            user = st.session_state.users.get(username)
            if not user or not verify_password(user, password):
                st.error("Invalid username or password.")
                return
            if not user.get("active", False):
                st.error("Your account has been deactivated. Please contact the administrator.")
                return
            st.session_state.logged_in = True
            st.session_state.current_user = username
            ensure_user_initialized(username)
            save_session(username) if remember else clear_session()
            st.success("Login successful.")
            time.sleep(.4)
            st.rerun()

# ------------------------- SIDEBAR -------------------------
def nav_button(label: str, page: str) -> None:
    if st.sidebar.button(label, width="stretch"):
        st.session_state.page = page
        st.rerun()


def render_sidebar(username: str) -> None:
    show_logo_sidebar(username)
    if is_admin():
        nav_button("  Dashboard", "dashboard")
        nav_button("  Email Accounts", "emails")
        nav_button("  Compose Email", "campaign")
        nav_button("  Employee Management", "employees")
        nav_button("  Reports / Analytics", "reports")
    else:
        nav_button("  Email Accounts", "emails")
        nav_button("  Compose Email", "campaign")
    st.sidebar.divider()
    if st.sidebar.button("  Logout", width="stretch"):
        clear_session()
        st.session_state.logged_in = False
        st.session_state.current_user = None
        st.session_state.page = "dashboard"
        st.rerun()

# ------------------------- ADMIN DASHBOARD -------------------------
def render_dashboard(username: str) -> None:
    guard_page([ADMIN_ROLE])
    accounts_all = sum(len(get_user_accounts(u)) for u in st.session_state.users)
    campaigns_all = sum(len(get_user_campaigns(u)) for u in st.session_state.users)
    total_logs = len(st.session_state.activity_logs)
    success = sum(1 for l in st.session_state.activity_logs if l.get("status") == "Success")
    failed = sum(1 for l in st.session_state.activity_logs if l.get("status") == "Failed")
    success_rate = round((success / total_logs) * 100, 1) if total_logs else 0
    active_emp = sum(1 for u in employees_only().values() if u.get("active"))

    page_header("Admin Dashboard", "System-wide overview of employees, sender accounts, campaigns, and email activity.")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("👥 Active Employees", active_emp)
    c2.metric("📨 Sender Accounts", accounts_all)
    c3.metric("📤 Total Emails Sent", success)
    c4.metric("✅ Success Rate", f"{success_rate}%")
    left, right = st.columns([1.2, .8])
    with left:
        with st.container(border=True):
            st.subheader("Employee Performance Snapshot")
            rows = []
            for uname, user in employees_only().items():
                total, ok, bad = user_log_counts(uname)
                rows.append({"Employee": uname, "Status": "Active" if user.get("active") else "Inactive", "Emails Sent": total, "Success": ok, "Failed": bad})
            if rows:
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
            else:
                st.info("No employees yet.")
    with right:
        with st.container(border=True):
            st.subheader("System Performance")
            st.write(f"**Total campaigns:** {campaigns_all}")
            st.write(f"**Total activity logs:** {total_logs}")
            st.write(f"**Successful emails:** {success}")
            st.write(f"**Failed emails:** {failed}")
            st.write(f"**Last updated:** {now_label()}")

# ------------------------- EMAIL ACCOUNTS -------------------------
def render_email_accounts(username: str) -> None:
    guard_page([ADMIN_ROLE, EMPLOYEE_ROLE])
    page_header("Email Accounts", "Add and manage your own Gmail sender accounts. Use Gmail App Passwords, not normal Gmail passwords.", "📨")
    accounts = get_user_accounts(username)
    with st.form("add_email_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        new_email = col1.text_input("Gmail Address", placeholder="example@gmail.com")
        new_pass = col2.text_input("Gmail App Password", type="password", placeholder="Enter app password")
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
            with st.spinner("Verifying Gmail address and app password..."):
                is_verified, verify_message = validate_gmail_credentials(email_key, new_pass.strip())

            if not is_verified:
                st.error(verify_message)
                st.info("Tip: Gmail does not accept your normal Gmail password here. Create a Gmail App Password and paste that password.")
                return

            accounts.append({"email": email_key, "app_password": encode_secret(new_pass.strip()), "added_at": now_label(), "owner": username})
            st.session_state.user_emails_db[username] = accounts
            st.session_state.email_counters.setdefault(email_key, 0)
            save_user_data()
            st.success("Email account verified and added successfully.")
            st.rerun()
    st.subheader("Saved Sender Accounts")
    if not accounts:
        st.info("No sender emails added yet.")
        return
    for i, account in enumerate(accounts):
        email = account.get("email", "")
        sent = int(st.session_state.email_counters.get(email, 0))
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([3, .8, .8, .8], vertical_alignment="center")
            c1.write(f"**{email}**"); c1.caption(f"Added: {account.get('added_at', 'Old record')}")
            c2.markdown(f'<div class="compact-stat"><span>Sent</span><strong>{sent}</strong></div>', unsafe_allow_html=True)
            c3.markdown(f'<div class="compact-stat"><span>Remaining</span><strong>{max(DAILY_EMAIL_LIMIT-sent,0)}</strong></div>', unsafe_allow_html=True)
            if c4.button("Remove", key=f"remove_{username}_{i}", width="stretch"):
                st.session_state.user_emails_db[username].pop(i)
                st.session_state.email_counters.pop(email, None)
                save_user_data(); st.success("Email account removed."); st.rerun()

# ------------------------- COMPOSE -------------------------
def render_compose_email(username: str) -> None:
    guard_page([ADMIN_ROLE, EMPLOYEE_ROLE])
    page_header("Compose Email", "Upload a CSV/XLSX leads file and send emails with a 10-second gap.", "✍️")
    accounts = get_user_accounts(username)
    if not accounts:
        st.warning("Please add a sender email first.")
        if st.button("Go to Email Accounts"):
            st.session_state.page = "emails"; st.rerun()
        return
    email_options = [a.get("email", "") for a in accounts if a.get("email")]
    with st.form("campaign_form", clear_on_submit=False):
        sender = st.selectbox("Select Sender Email", email_options)
        subject = st.text_input("Subject", placeholder="Enter email subject")
        body = st.text_area("Email Body", height=220, placeholder="Write a professional plain-text email...")
        uploaded_file = st.file_uploader("Upload Leads File", type=["csv", "xlsx"])
        permission_confirmed = st.checkbox("I confirm these recipients are allowed to receive this email.")
        run_campaign = st.form_submit_button("Send Emails", width="stretch")
    if not run_campaign:
        return
    if uploaded_file is None or not subject.strip() or not body.strip() or not permission_confirmed:
        st.error("Upload leads, add subject/body, and confirm recipient permission."); return
    sender_account = next((a for a in accounts if a.get("email") == sender), None)
    if not sender_account:
        st.error("Selected sender account was not found."); return
    try:
        leads_df, _, removed_count = read_leads_file(uploaded_file)
    except Exception as e:
        st.error(str(e)); return
    if leads_df.empty:
        st.error("No valid recipient email IDs found."); return
    already_sent = int(st.session_state.email_counters.get(sender, 0))
    remaining_limit = max(DAILY_EMAIL_LIMIT - already_sent, 0)
    if remaining_limit <= 0:
        st.error("This sender has reached the daily email limit."); return
    if len(leads_df) > remaining_limit:
        st.warning(f"Only first {remaining_limit} emails will be sent due to daily limit.")
        leads_df = leads_df.head(remaining_limit)
    try:
        sender_pass = decode_secret(sender_account["app_password"])
    except Exception:
        st.error("Saved app password is invalid. Please remove and add this sender again."); return

    campaign_id = short_campaign_id(); success=[]; failed=[]; server=None
    st.info(f"Sending started. Valid unique emails: {len(leads_df)} | Invalid/duplicate removed: {removed_count} | Delay: {SEND_DELAY_SECONDS}s")
    progress = st.progress(0); status_text = st.empty()
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30); server.starttls(); server.login(sender, sender_pass)
        total_rows = len(leads_df)
        for index, row in leads_df.iterrows():
            recipient = str(row["email"]).strip().lower(); status_text.info(f"Sending {index+1}/{total_rows}: {recipient}")
            try:
                msg = MIMEMultipart(); msg["From"] = sender; msg["To"] = recipient; msg["Subject"] = subject.strip(); msg["Reply-To"] = sender
                msg.attach(MIMEText(build_email_body(body, sender), "plain")); server.sendmail(sender, recipient, msg.as_string())
                success.append(recipient); st.session_state.email_counters[sender] = st.session_state.email_counters.get(sender, 0) + 1
                add_send_log(username, {"campaign_id": campaign_id, "timestamp": now_label(), "sender": sender, "recipient": recipient, "subject": subject.strip(), "status": "Success", "error": ""})
            except Exception as send_error:
                failed.append(recipient)
                add_send_log(username, {"campaign_id": campaign_id, "timestamp": now_label(), "sender": sender, "recipient": recipient, "subject": subject.strip(), "status": "Failed", "error": str(send_error)})
            progress.progress((index + 1) / total_rows)
            if index < total_rows - 1:
                time.sleep(SEND_DELAY_SECONDS)
    except smtplib.SMTPAuthenticationError:
        st.error("Incorrect Gmail address or Gmail App Password."); return
    except Exception as error:
        st.error(f"SMTP Error: {error}"); return
    finally:
        if server:
            try: server.quit()
            except Exception: pass
    st.session_state.campaigns[username].append({"campaign_id": campaign_id, "created_at": now_label(), "employee_username": username, "sender": sender, "subject": subject.strip(), "valid_leads": int(len(leads_df)), "sent": len(success), "failed": len(failed), "removed_invalid_or_duplicate": int(removed_count), "status": "Completed" if success else "Failed"})
    save_user_data(); status_text.empty()
    st.success(f"Email sending completed. Sent: {len(success)} | Failed: {len(failed)}")
    if failed:
        with st.expander("View Failed Emails"):
            st.dataframe(pd.DataFrame({"Failed Emails": failed}), width="stretch", hide_index=True)

# ------------------------- EMPLOYEE MANAGEMENT -------------------------
def render_employee_management() -> None:
    guard_page([ADMIN_ROLE])
    page_header("Employee Management", "Create, update, activate, deactivate, and monitor employee accounts.", "👥")
    rows = []
    for uname, user in employees_only().items():
        total, _, _ = user_log_counts(uname)
        rows.append({"Username": uname, "Role": user.get("role"), "Status": "Active" if user.get("active") else "Inactive", "Created Date": user.get("created_date"), "Total Emails Sent": total})
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.info("No employees created yet.")

    tab_add, tab_update, tab_delete = st.tabs(["Add Employee", "Update / Activate / Deactivate", "Delete Employee"])
    with tab_add:
        with st.form("add_employee_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            username = c1.text_input("Username")
            password = c2.text_input("Password", type="password")
            active = c3.checkbox("Active", value=True)
            add = st.form_submit_button("Create Employee", width="stretch")
        if add:
            username = username.strip()
            if not username or not password:
                st.error("Username and password are required.")
            elif username in st.session_state.users:
                st.error("Username already exists.")
            else:
                st.session_state.users[username] = make_user(username, password, EMPLOYEE_ROLE, active)
                ensure_user_initialized(username); save_users(); save_user_data(); st.success("Employee created."); st.rerun()
    with tab_update:
        employees = list(employees_only().keys())
        if employees:
            selected = st.selectbox("Select Employee", employees)
            record = st.session_state.users[selected]
            with st.form("update_employee_form"):
                new_username = st.text_input("Username", value=selected)
                new_password = st.text_input("New Password (leave blank to keep current)", type="password")
                new_active = st.checkbox("Active", value=bool(record.get("active")))
                update = st.form_submit_button("Update Employee", width="stretch")
            if update:
                new_username = new_username.strip()
                if not new_username:
                    st.error("Username cannot be empty.")
                elif new_username != selected and new_username in st.session_state.users:
                    st.error("This username already exists.")
                else:
                    updated = dict(record); updated["username"] = new_username; updated["active"] = new_active; updated["updated_at"] = now_label()
                    if new_password: updated["password_hash"] = password_hash(new_password)
                    if new_username != selected:
                        st.session_state.users.pop(selected)
                        st.session_state.users[new_username] = updated
                        st.session_state.user_emails_db[new_username] = st.session_state.user_emails_db.pop(selected, [])
                        st.session_state.campaigns[new_username] = st.session_state.campaigns.pop(selected, [])
                        st.session_state.send_logs[new_username] = st.session_state.send_logs.pop(selected, [])
                        for log in st.session_state.activity_logs:
                            if log.get("employee_username") == selected: log["employee_username"] = new_username
                    else:
                        st.session_state.users[selected] = updated
                    save_users(); save_user_data(); st.success("Employee updated."); st.rerun()
        else:
            st.info("No employees available to update.")
    with tab_delete:
        employees = list(employees_only().keys())
        if employees:
            selected_delete = st.selectbox("Select Employee to Delete", employees, key="delete_employee_select")
            st.warning("Deleting removes the user login. Activity logs are preserved for reporting history.")
            if st.button("Delete Employee", width="stretch"):
                st.session_state.users.pop(selected_delete, None)
                save_users(); save_user_data(); st.success("Employee deleted."); st.rerun()

# ------------------------- REPORTS -------------------------
def render_reports() -> None:
    guard_page([ADMIN_ROLE])
    page_header("Reports / Analytics", "Employee summaries, performance tables, daily activity, and detail view.", "📊")
    emp = employees_only()
    total_emp = len(emp); active = sum(1 for u in emp.values() if u.get("active")); inactive = total_emp - active
    total_sent = sum(1 for l in st.session_state.activity_logs if l.get("status") == "Success")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Employees", total_emp); c2.metric("Active Employees", active); c3.metric("Inactive Employees", inactive); c4.metric("Total Emails Sent", total_sent)

    st.subheader("Employee Performance Table")
    performance = []
    for uname in emp:
        total, ok, bad = user_log_counts(uname)
        performance.append({"Employee Name": uname, "Emails Sent": total, "Successful Emails": ok, "Failed Emails": bad})
    if performance:
        st.dataframe(pd.DataFrame(performance), width="stretch", hide_index=True)
    else:
        st.info("No employee activity yet.")

    st.subheader("Daily Activity Report")
    daily = {}
    for log in st.session_state.activity_logs:
        date = str(log.get("timestamp", ""))[:10]
        uname = log.get("employee_username", "")
        if not date or not uname: continue
        daily[(date, uname)] = daily.get((date, uname), 0) + 1
    daily_rows = [{"Date": d, "Employee": u, "Emails Sent": n} for (d, u), n in sorted(daily.items(), reverse=True)]
    if daily_rows:
        st.dataframe(pd.DataFrame(daily_rows), width="stretch", hide_index=True)
    else:
        st.info("No daily activity recorded yet.")

    st.subheader("Employee Detail View")
    employee_names = list(emp.keys())
    if not employee_names:
        st.info("No employees available."); return
    selected = st.selectbox("Select Employee", employee_names)
    user = st.session_state.users[selected]
    total, ok, bad = user_log_counts(selected)
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Username", selected); d2.metric("Status", "Active" if user.get("active") else "Inactive"); d3.metric("Success Count", ok); d4.metric("Failure Count", bad)
    st.write(f"**Total Emails Sent:** {total}")
    recent = [l for l in st.session_state.activity_logs if l.get("employee_username") == selected][-20:][::-1]
    if recent:
        df = pd.DataFrame(recent).rename(columns={"timestamp": "Timestamp", "recipient_email": "Recipient", "subject": "Subject", "status": "Status"})
        st.dataframe(df[["Timestamp", "Recipient", "Subject", "Status"]], width="stretch", hide_index=True)
    else:
        st.info("No recent activity for this employee.")

# ------------------------- MAIN ROUTER -------------------------
if not st.session_state.logged_in:
    render_login_page()
else:
    username = current_username()
    user = st.session_state.users.get(username)
    if not user:
        clear_session(); st.session_state.logged_in = False; st.session_state.current_user = None; st.rerun()
    if not user.get("active", False):
        st.error("Your account has been deactivated. Please contact the administrator.")
        clear_session(); st.session_state.logged_in = False; st.session_state.current_user = None; st.stop()
    ensure_user_initialized(username)
    render_sidebar(username)
    page = st.session_state.page
    if page in ["dashboard", "employees", "reports"] and not is_admin():
        st.session_state.page = "emails"; st.rerun()
    if page == "dashboard": render_dashboard(username)
    elif page == "emails": render_email_accounts(username)
    elif page == "campaign": render_compose_email(username)
    elif page == "employees": render_employee_management()
    elif page == "reports": render_reports()
    else:
        st.session_state.page = "dashboard" if is_admin() else "emails"; st.rerun()