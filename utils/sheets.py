"""
Google Sheets connector.
All reads and writes to the backing spreadsheet go through this module.
"""

import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
from datetime import date
import uuid

from utils.constants import (
    SHEET_CONTRIBUTIONS, SHEET_RETURNS, SHEET_SNAPSHOTS, SHEET_SETTINGS,
    CONTRIBUTIONS_COLS, RETURNS_COLS, SNAPSHOTS_COLS, SETTINGS_COLS,
    DEFAULT_SETTINGS,
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# ─── Connection ───────────────────────────────────────────────────────────────

@st.cache_resource
def get_client():
    """Authenticate and return a gspread client (cached for the session)."""
    # Streamlit Cloud stores the private key with literal \n text instead of
    # real newlines — this converts them back so Google's library can read the key.
    info = dict(st.secrets["gcp_service_account"])
    info["private_key"] = info["private_key"].replace("\\n", "\n")
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


@st.cache_resource
def get_spreadsheet():
    """Open the spreadsheet by name (cached for the session)."""
    client = get_client()
    name = st.secrets["sheets"]["spreadsheet_name"]
    try:
        return client.open(name)
    except gspread.SpreadsheetNotFound:
        st.error(
            f"❌ Spreadsheet **'{name}'** not found. "
            "Please create it in Google Sheets and share it with your service account email. "
            "Check the README for setup instructions."
        )
        st.stop()


def get_or_create_worksheet(spreadsheet, title: str, headers: list[str]):
    """Return a worksheet, creating it with headers if it doesn't exist."""
    try:
        ws = spreadsheet.worksheet(title)
        # If empty, write headers
        if ws.row_count == 0 or not ws.get_all_values():
            ws.append_row(headers)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(headers))
        ws.append_row(headers)
    return ws


def init_sheets():
    """Ensure all required worksheets exist; write default settings if needed."""
    ss = get_spreadsheet()
    get_or_create_worksheet(ss, SHEET_CONTRIBUTIONS, CONTRIBUTIONS_COLS)
    get_or_create_worksheet(ss, SHEET_RETURNS,       RETURNS_COLS)
    get_or_create_worksheet(ss, SHEET_SNAPSHOTS,     SNAPSHOTS_COLS)
    ws_settings = get_or_create_worksheet(ss, SHEET_SETTINGS, SETTINGS_COLS)

    # Write defaults for any missing setting keys
    existing = _read_df(ws_settings)
    if existing.empty:
        existing_keys = set()
    else:
        existing_keys = set(existing["key"].tolist())

    for key, value in DEFAULT_SETTINGS.items():
        if key not in existing_keys:
            ws_settings.append_row([key, value])


# ─── Generic helpers ─────────────────────────────────────────────────────────

def _read_df(ws) -> pd.DataFrame:
    """Read a worksheet into a DataFrame."""
    data = ws.get_all_records()
    if not data:
        return pd.DataFrame()
    return pd.DataFrame(data)


# ─── Contributions ────────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def get_contributions() -> pd.DataFrame:
    ss = get_spreadsheet()
    ws = ss.worksheet(SHEET_CONTRIBUTIONS)
    df = _read_df(ws)
    if df.empty:
        return df
    df["date"]   = pd.to_datetime(df["date"])
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
    return df.sort_values("date").reset_index(drop=True)


def add_contribution(contribution_date: date, amount: float, account: str,
                     person: str, notes: str = ""):
    ss = get_spreadsheet()
    ws = ss.worksheet(SHEET_CONTRIBUTIONS)
    row_id = str(uuid.uuid4())[:8]
    ws.append_row([row_id, str(contribution_date), amount, account, person, notes])
    st.cache_data.clear()


def delete_contribution(row_id: str):
    ss = get_spreadsheet()
    ws = ss.worksheet(SHEET_CONTRIBUTIONS)
    cell = ws.find(row_id)
    if cell:
        ws.delete_rows(cell.row)
    st.cache_data.clear()


# ─── Returns ─────────────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def get_returns() -> pd.DataFrame:
    ss = get_spreadsheet()
    ws = ss.worksheet(SHEET_RETURNS)
    df = _read_df(ws)
    if df.empty:
        return df
    df["date"]   = pd.to_datetime(df["date"])
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
    return df.sort_values("date").reset_index(drop=True)


def add_return(return_date: date, amount: float, account: str,
               person: str, notes: str = ""):
    ss = get_spreadsheet()
    ws = ss.worksheet(SHEET_RETURNS)
    row_id = str(uuid.uuid4())[:8]
    ws.append_row([row_id, str(return_date), amount, account, person, notes])
    st.cache_data.clear()


def delete_return(row_id: str):
    ss = get_spreadsheet()
    ws = ss.worksheet(SHEET_RETURNS)
    cell = ws.find(row_id)
    if cell:
        ws.delete_rows(cell.row)
    st.cache_data.clear()


# ─── Balance Snapshots ────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def get_snapshots() -> pd.DataFrame:
    ss = get_spreadsheet()
    ws = ss.worksheet(SHEET_SNAPSHOTS)
    df = _read_df(ws)
    if df.empty:
        return df
    df["date"]    = pd.to_datetime(df["date"])
    df["balance"] = pd.to_numeric(df["balance"], errors="coerce").fillna(0)
    return df.sort_values("date").reset_index(drop=True)


def add_snapshot(snapshot_date: date, account: str, person: str,
                 balance: float, source: str = "", notes: str = ""):
    ss = get_spreadsheet()
    ws = ss.worksheet(SHEET_SNAPSHOTS)
    row_id = str(uuid.uuid4())[:8]
    ws.append_row([row_id, str(snapshot_date), account, person, balance, source, notes])
    st.cache_data.clear()


def delete_snapshot(row_id: str):
    ss = get_spreadsheet()
    ws = ss.worksheet(SHEET_SNAPSHOTS)
    cell = ws.find(row_id)
    if cell:
        ws.delete_rows(cell.row)
    st.cache_data.clear()


# ─── Settings ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def get_settings() -> dict:
    ss = get_spreadsheet()
    ws = ss.worksheet(SHEET_SETTINGS)
    df = _read_df(ws)
    if df.empty:
        return {}
    return dict(zip(df["key"], df["value"]))


def update_setting(key: str, value):
    ss = get_spreadsheet()
    ws = ss.worksheet(SHEET_SETTINGS)
    try:
        cell = ws.find(key)
        ws.update_cell(cell.row, 2, str(value))
    except gspread.CellNotFound:
        ws.append_row([key, str(value)])
    st.cache_data.clear()


def update_settings(updates: dict):
    """Batch-update multiple settings at once."""
    for key, value in updates.items():
        update_setting(key, value)
