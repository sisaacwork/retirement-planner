"""
Financial calculations: balances, rate of return (XIRR), projections,
and Canadian contribution room.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from utils.constants import TFSA_ANNUAL_LIMITS, FHSA_ANNUAL_LIMIT, FHSA_LIFETIME_LIMIT


# ─── Balance calculation ──────────────────────────────────────────────────────

def current_balance_by_account(
    contributions: pd.DataFrame,
    returns: pd.DataFrame,
    snapshots: pd.DataFrame,
) -> pd.DataFrame:
    """
    For each (account, person) pair, estimate the current balance as:
        latest_snapshot + contributions_since_snapshot + returns_since_snapshot

    If no snapshot exists, balance = sum(contributions) + sum(returns).

    Returns a DataFrame with columns: account, person, balance, last_snapshot_date.
    """
    results = []
    account_persons = set()

    for df in [contributions, returns, snapshots]:
        if not df.empty and "account" in df.columns and "person" in df.columns:
            for _, row in df[["account", "person"]].drop_duplicates().iterrows():
                account_persons.add((row["account"], row["person"]))

    for account, person in account_persons:
        snap_date = None
        snap_balance = 0.0

        # Find the most recent snapshot for this account/person
        if not snapshots.empty:
            mask = (snapshots["account"] == account) & (snapshots["person"] == person)
            acct_snaps = snapshots[mask]
            if not acct_snaps.empty:
                latest = acct_snaps.loc[acct_snaps["date"].idxmax()]
                snap_date = latest["date"]
                snap_balance = float(latest["balance"])

        # Sum contributions after (or all if no snapshot)
        contrib_sum = 0.0
        if not contributions.empty:
            mask = (contributions["account"] == account) & (contributions["person"] == person)
            acct_contribs = contributions[mask]
            if not acct_contribs.empty:
                if snap_date is not None:
                    acct_contribs = acct_contribs[acct_contribs["date"] > snap_date]
                contrib_sum = float(acct_contribs["amount"].sum())

        # Sum returns after (or all if no snapshot)
        return_sum = 0.0
        if not returns.empty:
            mask = (returns["account"] == account) & (returns["person"] == person)
            acct_returns = returns[mask]
            if not acct_returns.empty:
                if snap_date is not None:
                    acct_returns = acct_returns[acct_returns["date"] > snap_date]
                return_sum = float(acct_returns["amount"].sum())

        balance = snap_balance + contrib_sum + return_sum
        results.append({
            "account":            account,
            "person":             person,
            "balance":            balance,
            "last_snapshot_date": snap_date,
        })

    if not results:
        return pd.DataFrame(columns=["account", "person", "balance", "last_snapshot_date"])

    return pd.DataFrame(results)


def total_balance(balance_df: pd.DataFrame) -> float:
    """Sum all account balances."""
    if balance_df.empty:
        return 0.0
    return float(balance_df["balance"].sum())


def portfolio_over_time(
    contributions: pd.DataFrame,
    returns: pd.DataFrame,
    snapshots: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build a daily cumulative portfolio value series by combining all events
    (contributions, returns, and balance snapshots as anchors).

    Returns a DataFrame with columns: date, balance.
    """
    events = []

    if not contributions.empty:
        for _, row in contributions.iterrows():
            events.append({"date": row["date"], "delta": float(row["amount"])})

    if not returns.empty:
        for _, row in returns.iterrows():
            events.append({"date": row["date"], "delta": float(row["amount"])})

    if not snapshots.empty:
        for _, row in snapshots.iterrows():
            events.append({"date": row["date"], "delta": None, "snap": float(row["balance"]),
                           "account": row["account"], "person": row["person"]})

    if not events:
        return pd.DataFrame(columns=["date", "balance"])

    # Sort by date
    events_df = pd.DataFrame(events).sort_values("date")

    # Build cumulative balance
    running = 0.0
    rows = []
    for _, ev in events_df.iterrows():
        if pd.isna(ev.get("snap", float("nan"))):
            running += ev["delta"]
        else:
            # A balance snapshot anchors the total (it replaces the running sum
            # for that account — simple approximation: treat snapshot as absolute)
            running = ev["snap"]
        rows.append({"date": ev["date"], "balance": running})

    result = pd.DataFrame(rows).sort_values("date")
    # Resample to daily, forward-fill
    result = result.set_index("date").resample("D").last().ffill().reset_index()
    return result


# ─── Rate of Return (XIRR) ────────────────────────────────────────────────────

def xirr(cashflows: list[tuple[date, float]]) -> Optional[float]:
    """
    Money-weighted rate of return (XIRR).

    cashflows: list of (date, amount) tuples.
        - Contributions are NEGATIVE (money leaving your pocket).
        - The final portfolio value is POSITIVE (money coming back to you).

    Returns the annualised rate as a decimal (e.g. 0.08 = 8 %), or None.
    """
    if len(cashflows) < 2:
        return None

    dates   = [cf[0] for cf in cashflows]
    amounts = [cf[1] for cf in cashflows]
    t0      = min(dates)
    days    = [(d - t0).days for d in dates]

    def npv(rate):
        return sum(a / (1 + rate) ** (d / 365.25) for a, d in zip(amounts, days))

    # Quick check: need at least one sign change
    pos = any(a > 0 for a in amounts)
    neg = any(a < 0 for a in amounts)
    if not (pos and neg):
        return None

    try:
        rate = brentq(npv, -0.9999, 100.0, maxiter=500)
        return rate
    except Exception:
        return None


def build_xirr_cashflows(
    contributions: pd.DataFrame,
    returns: pd.DataFrame,
    snapshots: pd.DataFrame,
) -> list[tuple[date, float]]:
    """
    Build the cashflow list for XIRR:
        - Each contribution is an outflow (negative).
        - The estimated current balance is a single inflow at today (positive).
    """
    cashflows: list[tuple[date, float]] = []

    if not contributions.empty:
        for _, row in contributions.iterrows():
            cashflows.append((row["date"].date(), -abs(float(row["amount"]))))

    # Current balance as inflow at today
    balance_df = current_balance_by_account(contributions, returns, snapshots)
    total = total_balance(balance_df)
    if total > 0:
        cashflows.append((date.today(), total))

    return cashflows


# ─── Projections ─────────────────────────────────────────────────────────────

def months_to_milestone(
    current_balance: float,
    monthly_contribution: float,
    annual_return_rate: float,
    milestone: float,
) -> Optional[float]:
    """
    Solve for the number of months to reach `milestone` given:
        FV = PV*(1+r)^n + PMT*((1+r)^n - 1)/r
    Returns None if milestone is already reached or unreachable with no return.
    """
    if current_balance >= milestone:
        return 0.0

    r = annual_return_rate / 12  # monthly rate

    if r == 0:
        if monthly_contribution <= 0:
            return None
        n = (milestone - current_balance) / monthly_contribution
        return max(n, 0)

    # Solve FV = PV*(1+r)^n + PMT*((1+r)^n - 1)/r  for n
    # Rearranging: (1+r)^n = (FV + PMT/r) / (PV + PMT/r)
    ratio_num = milestone + monthly_contribution / r
    ratio_den = current_balance + monthly_contribution / r

    if ratio_den <= 0 or ratio_num / ratio_den <= 0:
        return None

    try:
        n = math.log(ratio_num / ratio_den) / math.log(1 + r)
        return max(n, 0)
    except Exception:
        return None


def avg_monthly_contribution(contributions: pd.DataFrame, lookback_months: int = 12) -> float:
    """Average monthly net contributions over the last `lookback_months` months."""
    if contributions.empty:
        return 0.0
    cutoff = pd.Timestamp.today() - pd.DateOffset(months=lookback_months)
    recent = contributions[contributions["date"] >= cutoff]
    if recent.empty:
        return 0.0
    total = float(recent["amount"].sum())
    return total / lookback_months


# ─── Canadian Contribution Room ───────────────────────────────────────────────

def tfsa_cumulative_room(
    birth_year: int,
    as_of_year: Optional[int] = None,
    eligible_from_year: Optional[int] = None,
) -> float:
    """
    Total TFSA room accumulated through `as_of_year`.

    Eligibility starts at the LATER of:
      - the year the person turned 18, OR 2009 (standard rule), AND
      - `eligible_from_year` if provided (used for non-residents who became
        eligible after turning 18, e.g. new Canadian residents).
    """
    if as_of_year is None:
        as_of_year = date.today().year

    age_based = max(birth_year + 18, 2009)
    if eligible_from_year is not None:
        eligible_from = max(age_based, eligible_from_year)
    else:
        eligible_from = age_based

    total = 0.0
    for year, limit in TFSA_ANNUAL_LIMITS.items():
        if eligible_from <= year <= as_of_year:
            total += limit
    return total


def tfsa_remaining_room(
    birth_year: int,
    contributions: pd.DataFrame,
    prior_contributions: float = 0.0,
    withdrawals: float = 0.0,
    person: Optional[str] = None,
    eligible_from_year: Optional[int] = None,
) -> float:
    """
    Remaining TFSA room = cumulative_room - prior_contributions - in_app_contributions + withdrawals
    """
    total_room = tfsa_cumulative_room(birth_year, eligible_from_year=eligible_from_year)
    in_app = 0.0
    if not contributions.empty:
        mask = contributions["account"] == "TFSA"
        if person:
            mask &= contributions["person"] == person
        in_app = float(contributions[mask]["amount"].sum())
    return total_room - prior_contributions - in_app + withdrawals


def fhsa_cumulative_room(open_year: int, as_of_year: Optional[int] = None) -> float:
    """
    FHSA room accumulated from open year through as_of_year (annual limit, capped at lifetime).
    Unused room carries forward 1 year only — simplified here as full cumulative room.
    """
    if as_of_year is None:
        as_of_year = date.today().year

    start = max(open_year, FHSA_LIFETIME_LIMIT // FHSA_ANNUAL_LIMIT)  # safety
    start = max(open_year, 2023)  # FHSA launched 2023
    years = max(0, as_of_year - start + 1)
    return min(years * FHSA_ANNUAL_LIMIT, FHSA_LIFETIME_LIMIT)


def fhsa_remaining_room(
    open_year: int,
    contributions: pd.DataFrame,
    prior_contributions: float = 0.0,
    person: Optional[str] = None,
) -> float:
    """Remaining FHSA room."""
    total_room = fhsa_cumulative_room(open_year)
    in_app = 0.0
    if not contributions.empty:
        mask = contributions["account"] == "FHSA"
        if person:
            mask &= contributions["person"] == person
        in_app = float(contributions[mask]["amount"].sum())
    used = prior_contributions + in_app
    return max(0.0, total_room - used)


def rrsp_remaining_room(
    noa_room: float,
    contributions: pd.DataFrame,
    person: Optional[str] = None,
) -> float:
    """Remaining RRSP room = NOA room - contributions made in app."""
    in_app = 0.0
    if not contributions.empty:
        mask = contributions["account"] == "RRSP"
        if person:
            mask &= contributions["person"] == person
        in_app = float(contributions[mask]["amount"].sum())
    return max(0.0, noa_room - in_app)
