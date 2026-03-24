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
    withdrawals: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    For each (account, person) pair, estimate the current balance as:
        latest_snapshot + contributions_since_snapshot + returns_since_snapshot
                        - withdrawals_since_snapshot

    If no snapshot exists, balance = sum(contributions) + sum(returns) - sum(withdrawals).

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

        # Subtract withdrawals after (or all if no snapshot)
        withdrawal_sum = 0.0
        if withdrawals is not None and not withdrawals.empty:
            wmask = (withdrawals["account"] == account) & (withdrawals["person"] == person)
            acct_withdrawals = withdrawals[wmask]
            if not acct_withdrawals.empty:
                if snap_date is not None:
                    acct_withdrawals = acct_withdrawals[acct_withdrawals["date"] > snap_date]
                withdrawal_sum = float(acct_withdrawals["amount"].sum())

        balance = snap_balance + contrib_sum + return_sum - withdrawal_sum
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
    withdrawals: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Build a daily portfolio value series across all accounts.

    Each (account, person) pair is tracked independently, then summed by date.
    Within each pair:
      - Contributions, returns, and withdrawals are running deltas.
      - Balance snapshots are absolute anchors (they override the delta-based
        running total on that date, since they reflect the actual account value).

    This hybrid approach preserves the full contribution history going back to
    day one, while using real balance snapshots for accuracy wherever they exist.

    Returns a DataFrame with columns: date, balance.
    """
    # Collect all (account, person) pairs seen in any dataset
    pairs: set = set()
    for df in [contributions, returns, snapshots]:
        if not df.empty and "account" in df.columns and "person" in df.columns:
            for _, row in df[["account", "person"]].drop_duplicates().iterrows():
                pairs.add((row["account"], row["person"]))
    if withdrawals is not None and not withdrawals.empty:
        for _, row in withdrawals[["account", "person"]].drop_duplicates().iterrows():
            pairs.add((row["account"], row["person"]))

    if not pairs:
        return pd.DataFrame(columns=["date", "balance"])

    # Determine overall date range
    all_dates: list = []
    for df in [contributions, returns, snapshots]:
        if not df.empty and "date" in df.columns:
            all_dates.extend(df["date"].tolist())
    if withdrawals is not None and not withdrawals.empty:
        all_dates.extend(withdrawals["date"].tolist())
    if not all_dates:
        return pd.DataFrame(columns=["date", "balance"])

    date_range = pd.date_range(
        start=min(all_dates),
        end=max(pd.Timestamp.today(), max(all_dates)),
        freq="D",
    )

    total_series = pd.Series(0.0, index=date_range)

    for account, person in pairs:
        # Build a list of (timestamp, delta, is_snapshot, snap_value)
        # Snapshots sort after deltas on the same day so the anchor wins.
        events: list = []

        if not contributions.empty:
            m = (contributions["account"] == account) & (contributions["person"] == person)
            for _, row in contributions[m].iterrows():
                events.append((pd.Timestamp(row["date"]), float(row["amount"]), False, 0.0))

        if not returns.empty:
            m = (returns["account"] == account) & (returns["person"] == person)
            for _, row in returns[m].iterrows():
                events.append((pd.Timestamp(row["date"]), float(row["amount"]), False, 0.0))

        if withdrawals is not None and not withdrawals.empty:
            m = (withdrawals["account"] == account) & (withdrawals["person"] == person)
            for _, row in withdrawals[m].iterrows():
                events.append((pd.Timestamp(row["date"]), -float(row["amount"]), False, 0.0))

        if not snapshots.empty:
            m = (snapshots["account"] == account) & (snapshots["person"] == person)
            for _, row in snapshots[m].iterrows():
                events.append((pd.Timestamp(row["date"]), 0.0, True, float(row["balance"])))

        if not events:
            continue

        # Sort: by date first, snapshots after deltas on the same day
        events.sort(key=lambda x: (x[0], x[2]))

        # Walk events and build a {date: balance} mapping for this account
        running = 0.0
        acct_points: dict = {}
        for ts, delta, is_snap, snap_val in events:
            day = ts.normalize()
            if is_snap:
                running = snap_val        # anchor to actual balance
            else:
                running += delta
            acct_points[day] = max(running, 0.0)

        # Reindex to full date range: forward-fill (hold last known balance),
        # leave dates before first event as 0.
        acct_series = pd.Series(acct_points).reindex(date_range)
        acct_series = acct_series.ffill().fillna(0.0)

        total_series = total_series + acct_series

    result = total_series.reset_index()
    result.columns = ["date", "balance"]
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
    withdrawals: Optional[pd.DataFrame] = None,
) -> list[tuple[date, float]]:
    """
    Build the cashflow list for XIRR (Money-Weighted Rate of Return).

    Sign convention from the investor's perspective:
        - Contributions are OUTFLOWS (negative) — money leaving your pocket.
        - Withdrawals are INFLOWS  (positive) — money coming back to you.
        - Current portfolio balance is a final INFLOW (positive) at today.

    Without including withdrawals, the MWRR is badly distorted — a $9,000
    withdrawal looks like a $9,000 loss, dragging the rate way negative.
    """
    cashflows: list[tuple[date, float]] = []

    if not contributions.empty:
        for _, row in contributions.iterrows():
            cashflows.append((row["date"].date(), -abs(float(row["amount"]))))

    # Withdrawals are positive cashflows — money returned to the investor
    if withdrawals is not None and not withdrawals.empty:
        for _, row in withdrawals.iterrows():
            cashflows.append((row["date"].date(), +abs(float(row["amount"]))))

    # Current balance as final inflow at today
    balance_df = current_balance_by_account(contributions, returns, snapshots, withdrawals)
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


# ─── Return derivation from balances ─────────────────────────────────────────

def calculate_return_from_balance(
    new_balance: float,
    new_date: date,
    account: str,
    person: str,
    contributions: pd.DataFrame,
    snapshots: pd.DataFrame,
    withdrawals: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Given a newly recorded balance, derive the implied market return since the
    last known balance snapshot for that account/person.

    return = new_balance − prev_balance − contributions_between + withdrawals_between

    Withdrawals are added back because taking money out reduces the balance
    without being a market loss — we want to isolate the pure investment return.

    Returns a dict with keys:
        return_amount       – the derived CA$ return (None if no prior snapshot)
        prev_balance        – the previous snapshot balance (None if no prior)
        prev_date           – date of the previous snapshot (None if no prior)
        contrib_between     – sum of contributions between the two dates
        withdrawal_between  – sum of withdrawals between the two dates
    """
    prev_balance: Optional[float] = None
    prev_date: Optional[date]     = None

    if not snapshots.empty:
        mask = (
            (snapshots["account"] == account) &
            (snapshots["person"]  == person)  &
            (snapshots["date"].dt.date < new_date)
        )
        prior = snapshots[mask]
        if not prior.empty:
            latest       = prior.loc[prior["date"].idxmax()]
            prev_balance = float(latest["balance"])
            prev_date    = latest["date"].date()

    if prev_balance is None:
        return {"return_amount": None, "prev_balance": None,
                "prev_date": None, "contrib_between": 0.0, "withdrawal_between": 0.0}

    contrib_between = 0.0
    if not contributions.empty:
        cmask = (
            (contributions["account"] == account) &
            (contributions["person"]  == person)  &
            (contributions["date"].dt.date > prev_date) &
            (contributions["date"].dt.date <= new_date)
        )
        contrib_between = float(contributions[cmask]["amount"].sum())

    withdrawal_between = 0.0
    if withdrawals is not None and not withdrawals.empty:
        wmask = (
            (withdrawals["account"] == account) &
            (withdrawals["person"]  == person)  &
            (withdrawals["date"].dt.date > prev_date) &
            (withdrawals["date"].dt.date <= new_date)
        )
        withdrawal_between = float(withdrawals[wmask]["amount"].sum())

    return {
        "return_amount":      new_balance - prev_balance - contrib_between + withdrawal_between,
        "prev_balance":       prev_balance,
        "prev_date":          prev_date,
        "contrib_between":    contrib_between,
        "withdrawal_between": withdrawal_between,
    }


def derive_returns_from_balance_series(
    entries: list[dict],
    account: str,
    person: str,
    contributions: pd.DataFrame,
    existing_snapshots: pd.DataFrame,
    withdrawals: Optional[pd.DataFrame] = None,
) -> list[dict]:
    """
    Given a time-ordered list of {date, balance} dicts, calculate the implied
    return for each entry relative to the immediately preceding balance.

    The first entry is compared against the most recent existing snapshot in the
    database (if any); otherwise its return_amount is None (treated as the
    opening balance with no return).

    Returns a list of dicts with keys:
        date, balance, return_amount, contrib_between, withdrawal_between, prev_balance, prev_date
    """
    sorted_entries    = sorted(entries, key=lambda x: x["date"])
    running_snapshots = existing_snapshots.copy() if not existing_snapshots.empty else pd.DataFrame()

    results = []
    for entry in sorted_entries:
        entry_date    = entry["date"] if isinstance(entry["date"], date) else date.fromisoformat(str(entry["date"]))
        entry_balance = float(entry["balance"])

        info = calculate_return_from_balance(
            new_balance   = entry_balance,
            new_date      = entry_date,
            account       = account,
            person        = person,
            contributions = contributions,
            snapshots     = running_snapshots,
            withdrawals   = withdrawals,
        )

        results.append({
            "date":               entry_date,
            "balance":            entry_balance,
            "return_amount":      info["return_amount"],
            "prev_balance":       info["prev_balance"],
            "prev_date":          info["prev_date"],
            "contrib_between":    info["contrib_between"],
            "withdrawal_between": info["withdrawal_between"],
        })

        new_row = pd.DataFrame([{
            "id": "temp", "date": pd.Timestamp(entry_date),
            "account": account, "person": person,
            "balance": entry_balance, "source": "", "notes": "",
        }])
        running_snapshots = pd.concat([running_snapshots, new_row], ignore_index=True) \
            if not running_snapshots.empty else new_row

    return results


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
    prior_withdrawals: float = 0.0,
    person: Optional[str] = None,
    eligible_from_year: Optional[int] = None,
    withdrawals_df: Optional[pd.DataFrame] = None,
) -> float:
    """
    Remaining TFSA room = cumulative_room
                         - prior_contributions (before app)
                         - in_app_contributions
                         + prior_withdrawals (before app, from prior calendar years)
                         + in_app_withdrawals_from_prior_years

    CRA rule: TFSA withdrawals are added back to your room on January 1st of
    the FOLLOWING year — not immediately. So only withdrawals made before the
    current calendar year are counted.
    """
    total_room = tfsa_cumulative_room(birth_year, eligible_from_year=eligible_from_year)

    in_app_contributions = 0.0
    if not contributions.empty:
        mask = contributions["account"] == "TFSA"
        if person:
            mask &= contributions["person"] == person
        in_app_contributions = float(contributions[mask]["amount"].sum())

    # Only withdrawals from PRIOR calendar years restore room
    in_app_prior_withdrawals = 0.0
    current_year = date.today().year
    if withdrawals_df is not None and not withdrawals_df.empty:
        wmask = withdrawals_df["account"] == "TFSA"
        if person:
            wmask &= withdrawals_df["person"] == person
        wmask &= withdrawals_df["date"].dt.year < current_year
        in_app_prior_withdrawals = float(withdrawals_df[wmask]["amount"].sum())

    return (total_room
            - prior_contributions
            - in_app_contributions
            + prior_withdrawals
            + in_app_prior_withdrawals)


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
