"""
Goals & Projections — milestone progress and detailed forward projections.

Users can customise their monthly contribution mix per account/person,
set expected salaries for RRSP room growth, and see exactly when
contribution caps will be hit and when each milestone will be reached.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date

from utils.sheets import get_contributions, get_returns, get_snapshots, get_withdrawals, get_settings
from utils.calculations import (
    current_balance_by_account, total_balance,
    tfsa_cumulative_room, tfsa_remaining_room,
    fhsa_cumulative_room, fhsa_remaining_room,
    rrsp_remaining_room,
    avg_monthly_contribution,
)
from utils.constants import (
    ACCOUNT_TYPES, PEOPLE,
    TFSA_ANNUAL_LIMITS, FHSA_ANNUAL_LIMIT, FHSA_LIFETIME_LIMIT,
)

st.set_page_config(page_title="Goals", page_icon="🎯", layout="wide")
st.title("🎯 Goals & Projections")
st.caption(
    "Model your path to each milestone. Customise your contribution mix, "
    "set expected salaries for RRSP room, and see when caps will be hit."
)
st.divider()

# ─── Constants ────────────────────────────────────────────────────────────────

RRSP_ANNUAL_MAX = 32490   # 2026 CRA RRSP deduction limit
TFSA_FUTURE_LIMIT = 7000  # Assumed annual limit for years beyond known schedule

# ─── Load data ────────────────────────────────────────────────────────────────

contributions = get_contributions()
returns       = get_returns()
snapshots     = get_snapshots()
withdrawals   = get_withdrawals()
settings      = get_settings()

def s(key, default="0"):
    return settings.get(key, default)

balance_df = current_balance_by_account(contributions, returns, snapshots, withdrawals)
portfolio  = total_balance(balance_df)

# ─── Pull current room from settings ──────────────────────────────────────────

try:
    isaac_birth          = int(s("isaac_birth_year", "1995"))
    katherine_birth      = int(s("katherine_birth_year", "1995"))
    isaac_tfsa_eligible  = int(s("tfsa_eligible_year_isaac", "2025"))
    kath_tfsa_eligible   = int(s("tfsa_eligible_year_katherine", "2026"))
    isaac_fhsa_open      = int(s("fhsa_open_year_isaac", "2025"))
    kath_fhsa_open       = int(s("fhsa_open_year_katherine", "2026"))
    rrsp_room_isaac      = float(s("rrsp_room_isaac", "0") or 0)
    rrsp_room_kath       = float(s("rrsp_room_katherine", "0") or 0)
    tfsa_prior_isaac     = float(s("tfsa_prior_contributions_isaac", "0") or 0)
    tfsa_prior_kath      = float(s("tfsa_prior_contributions_katherine", "0") or 0)
    tfsa_prior_w_isaac   = float(s("tfsa_prior_withdrawals_isaac", "0") or 0)
    tfsa_prior_w_kath    = float(s("tfsa_prior_withdrawals_katherine", "0") or 0)
    fhsa_prior_isaac     = float(s("fhsa_prior_contributions_isaac", "0") or 0)
    fhsa_prior_kath      = float(s("fhsa_prior_contributions_katherine", "0") or 0)
    m1 = float(s("milestone_1", "250000") or 250000)
    m2 = float(s("milestone_2", "500000") or 500000)
    m3 = float(s("milestone_3", "1000000") or 1000000)
except (ValueError, TypeError):
    st.error("⚠️ Some settings are missing — visit ⚙️ Settings to configure them.")
    st.stop()

milestones = [m1, m2, m3]

# Current remaining room per person/account
current_room = {
    ("Isaac",     "TFSA"): tfsa_remaining_room(
        isaac_birth, contributions,
        prior_contributions=tfsa_prior_isaac, prior_withdrawals=tfsa_prior_w_isaac,
        person="Isaac", eligible_from_year=isaac_tfsa_eligible, withdrawals_df=withdrawals),
    ("Katherine", "TFSA"): tfsa_remaining_room(
        katherine_birth, contributions,
        prior_contributions=tfsa_prior_kath, prior_withdrawals=tfsa_prior_w_kath,
        person="Katherine", eligible_from_year=kath_tfsa_eligible, withdrawals_df=withdrawals),
    ("Isaac",     "FHSA"): fhsa_remaining_room(isaac_fhsa_open, contributions, fhsa_prior_isaac, person="Isaac"),
    ("Katherine", "FHSA"): fhsa_remaining_room(kath_fhsa_open,  contributions, fhsa_prior_kath,  person="Katherine"),
    ("Isaac",     "RRSP"): rrsp_remaining_room(rrsp_room_isaac, contributions, person="Isaac"),
    ("Katherine", "RRSP"): rrsp_remaining_room(rrsp_room_kath,  contributions, person="Katherine"),
    ("Isaac",     "NRSP"): float("inf"),   # No limit
    ("Katherine", "NRSP"): float("inf"),
}

# FHSA lifetime already used (for tracking cap during projection)
fhsa_lifetime_used = {
    "Isaac":     fhsa_prior_isaac + (float(contributions[(contributions["account"] == "FHSA") & (contributions["person"] == "Isaac")]["amount"].sum()) if not contributions.empty else 0),
    "Katherine": fhsa_prior_kath  + (float(contributions[(contributions["account"] == "FHSA") & (contributions["person"] == "Katherine")]["amount"].sum()) if not contributions.empty else 0),
}

# Current balances per (person, account)
current_balances = {}
if not balance_df.empty:
    for _, row in balance_df.iterrows():
        current_balances[(row["person"], row["account"])] = float(row["balance"])

# ─── Section 1: Projection assumptions ───────────────────────────────────────

st.subheader("📐 Projection Assumptions")

col_r, col_i, col_k = st.columns(3)
with col_r:
    expected_return = st.slider(
        "Expected Annual Return (%)",
        min_value=0.0, max_value=15.0, value=6.0, step=0.5,
        help="Long-term stock market average is roughly 7–8% nominal. "
             "A more conservative estimate for a balanced portfolio is 5–6%.",
    )
with col_i:
    isaac_salary = st.number_input(
        "Isaac's Annual Salary (CA$)",
        min_value=0.0, step=1000.0, format="%.0f", value=0.0,
        help="Used to estimate future RRSP contribution room. "
             f"New RRSP room = 18% of salary, up to ${RRSP_ANNUAL_MAX:,}/year.",
    )
with col_k:
    katherine_salary = st.number_input(
        "Katherine's Annual Salary (CA$)",
        min_value=0.0, step=1000.0, format="%.0f", value=0.0,
        help="Used to estimate future RRSP contribution room.",
    )

salaries = {
    "Isaac":     isaac_salary,
    "Katherine": katherine_salary,
}

st.divider()

# ─── Section 2: Monthly contribution mix ──────────────────────────────────────

st.subheader("💸 Monthly Contribution Mix")
st.caption(
    "Set how much goes into each account each month. "
    "The projection will automatically stop contributions once room is exhausted "
    "and redirect nothing — adjust your NRSP amount to capture any overflow."
)

# Build an editable table: rows = accounts, columns = Isaac / Katherine
default_monthly = avg_monthly_contribution(contributions, lookback_months=3) / 2  # rough per-person split

contrib_template = pd.DataFrame({
    "Account":    ACCOUNT_TYPES,
    "Isaac ($/mo)":     [max(round(default_monthly / len(ACCOUNT_TYPES)), 0)] * len(ACCOUNT_TYPES),
    "Katherine ($/mo)": [max(round(default_monthly / len(ACCOUNT_TYPES)), 0)] * len(ACCOUNT_TYPES),
})

edited_mix = st.data_editor(
    contrib_template,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Account":          st.column_config.TextColumn("Account", disabled=True),
        "Isaac ($/mo)":     st.column_config.NumberColumn("Isaac ($/mo)",     min_value=0, step=50, format="$%d"),
        "Katherine ($/mo)": st.column_config.NumberColumn("Katherine ($/mo)", min_value=0, step=50, format="$%d"),
    },
)

# Parse contribution mix
monthly_contributions = {}
for _, row in edited_mix.iterrows():
    acct = row["Account"]
    monthly_contributions[("Isaac",     acct)] = float(row["Isaac ($/mo)"])
    monthly_contributions[("Katherine", acct)] = float(row["Katherine ($/mo)"])

total_monthly = sum(monthly_contributions.values())
st.caption(
    f"Total monthly across all accounts: **${total_monthly:,.0f}** "
    f"(${total_monthly * 12:,.0f}/year)"
)

st.divider()

# ─── Section 3: Current room snapshot ────────────────────────────────────────

st.subheader("🏦 Current Contribution Room")
st.caption("Remaining room as of today, based on your logged contributions and settings.")

room_cols = st.columns(4)
for i, acct in enumerate(["TFSA", "FHSA", "RRSP", "NRSP"]):
    with room_cols[i]:
        st.markdown(f"**{acct}**")
        for person in PEOPLE:
            key = (person, acct)
            room_val = current_room.get(key, 0)
            if room_val == float("inf"):
                st.caption(f"{person}: No limit")
            else:
                st.caption(f"{person}: **${room_val:,.0f}**")
        if acct == "FHSA":
            st.caption(f"Lifetime limit: ${FHSA_LIFETIME_LIMIT:,}")
        if acct == "RRSP" and any(s > 0 for s in salaries.values()):
            for person, sal in salaries.items():
                if sal > 0:
                    new_room = min(sal * 0.18, RRSP_ANNUAL_MAX)
                    st.caption(f"{person} new room/yr: ${new_room:,.0f}")

st.divider()

# ─── Section 4: Projection ────────────────────────────────────────────────────

st.subheader("📈 Year-by-Year Projection")

if total_monthly == 0 and expected_return == 0:
    st.info("Set a monthly contribution or return rate above to generate the projection.")
    st.stop()

# ── Simulation ────────────────────────────────────────────────────────────────

def run_projection(current_balances, current_room, monthly_contributions,
                   fhsa_lifetime_used, salaries, annual_return_rate, years=40):
    monthly_rate = annual_return_rate / 12
    balances     = dict(current_balances)
    room         = {k: v for k, v in current_room.items()}
    fhsa_life    = dict(fhsa_lifetime_used)
    today_year   = date.today().year

    # Ensure all contribution keys exist in balances
    for key in monthly_contributions:
        if key not in balances:
            balances[key] = 0.0

    results = []
    milestone_hits = {}   # milestone value → year first hit

    for yr_offset in range(1, years + 1):
        cal_year    = today_year + yr_offset
        yr_contribs = {k: 0.0 for k in monthly_contributions}
        yr_capped   = []   # accounts where room ran out this year

        # ── January 1: refresh annual room ────────────────────────────────────
        # TFSA: new annual limit
        for person in PEOPLE:
            key = (person, "TFSA")
            annual_tfsa = TFSA_ANNUAL_LIMITS.get(cal_year, TFSA_FUTURE_LIMIT)
            room[key] = room.get(key, 0) + annual_tfsa

        # FHSA: $8k/year until lifetime cap
        for person in PEOPLE:
            key = (person, "FHSA")
            used = fhsa_life.get(person, 0)
            if used < FHSA_LIFETIME_LIMIT:
                new_fhsa = min(FHSA_ANNUAL_LIMIT, FHSA_LIFETIME_LIMIT - used)
                room[key] = room.get(key, 0) + new_fhsa

        # RRSP: 18% of prior year salary, capped at annual max
        for person, salary in salaries.items():
            if salary > 0:
                key = (person, "RRSP")
                new_rrsp = min(salary * 0.18, RRSP_ANNUAL_MAX)
                room[key] = room.get(key, 0) + new_rrsp

        # ── Run 12 months ──────────────────────────────────────────────────────
        for _ in range(12):
            for key, monthly_amt in monthly_contributions.items():
                if monthly_amt <= 0:
                    continue
                person, acct = key
                room_left = room.get(key, float("inf"))

                if room_left == float("inf"):
                    actual = monthly_amt
                else:
                    actual = min(monthly_amt, max(room_left, 0))
                    room[key] = max(room_left - actual, 0)
                    if acct == "FHSA":
                        fhsa_life[person] = fhsa_life.get(person, 0) + actual

                balances[key] = balances.get(key, 0.0) + actual
                yr_contribs[key] = yr_contribs.get(key, 0.0) + actual

            # Apply monthly return to all account balances
            for k in balances:
                balances[k] = balances[k] * (1 + monthly_rate)

        # ── Year-end stats ─────────────────────────────────────────────────────
        total_bal    = sum(balances.values())
        total_contrib = sum(yr_contribs.values())

        # Track room-exhausted accounts
        for person in PEOPLE:
            for acct in ["TFSA", "FHSA", "RRSP"]:
                key = (person, acct)
                if room.get(key, 1) <= 0 and monthly_contributions.get(key, 0) > 0:
                    label = f"{person} {acct}"
                    if label not in yr_capped:
                        yr_capped.append(label)

        # Track milestone hits
        for ms in milestones:
            if ms not in milestone_hits and total_bal >= ms:
                milestone_hits[ms] = cal_year

        results.append({
            "year":        cal_year,
            "total":       total_bal,
            "contributed": total_contrib,
            "capped":      yr_capped,
            "by_account":  {k: balances[k] for k in balances},
        })

        # Stop projecting once well past all milestones and after at least 10 years
        if milestones and total_bal > max(milestones) * 1.2 and yr_offset >= 10:
            break

    return results, milestone_hits

projection, milestone_hits = run_projection(
    current_balances      = current_balances,
    current_room          = current_room,
    monthly_contributions = monthly_contributions,
    fhsa_lifetime_used    = fhsa_lifetime_used,
    salaries              = salaries,
    annual_return_rate    = expected_return / 100,
)

# ── Milestone summary ─────────────────────────────────────────────────────────

ms_cols = st.columns(len(milestones))
for col, ms in zip(ms_cols, milestones):
    with col:
        pct = min(portfolio / ms * 100, 100)
        st.markdown(f"### ${ms:,.0f}")
        st.progress(pct / 100)
        if portfolio >= ms:
            st.success("✅ Already reached!")
        elif ms in milestone_hits:
            hit_year = milestone_hits[ms]
            years_away = hit_year - date.today().year
            st.metric("Projected Year", str(hit_year))
            st.caption(f"~{years_away} year{'s' if years_away != 1 else ''} away · {pct:.1f}% there")
        else:
            st.caption(f"{pct:.1f}% there — extend projection or increase contributions")

st.divider()

# ── Chart ─────────────────────────────────────────────────────────────────────

chart_df = pd.DataFrame([{"Year": r["year"], "Projected Balance": r["total"]} for r in projection])

fig = px.area(
    chart_df, x="Year", y="Projected Balance",
    color_discrete_sequence=["#2196F3"],
    labels={"Year": "Year", "Projected Balance": "Balance (CA$)"},
)

# Add milestone lines
for ms in milestones:
    fig.add_hline(
        y=ms, line_dash="dash", line_color="orange",
        annotation_text=f"${ms:,.0f}",
        annotation_position="bottom right",
    )

fig.update_layout(
    yaxis_tickprefix="$", yaxis_tickformat=",.0f",
    margin=dict(l=0, r=0, t=20, b=0),
)
st.plotly_chart(fig, use_container_width=True)

# ── Year-by-year table ────────────────────────────────────────────────────────

table_rows = []
for r in projection:
    row = {
        "Year":                str(r["year"]),
        "Projected Balance":   f"${r['total']:,.0f}",
        "Contributed (Year)":  f"${r['contributed']:,.0f}",
    }
    # Flag years where any account hits its cap
    flags = []
    for ms in milestones:
        if milestone_hits.get(ms) == r["year"]:
            flags.append(f"🎯 Hit ${ms:,.0f}")
    for label in r["capped"]:
        flags.append(f"🏁 {label} room full")
    row["Notes"] = " · ".join(flags) if flags else ""
    table_rows.append(row)

st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

st.divider()

# ── Per-account balance breakdown ─────────────────────────────────────────────

st.subheader("💼 End-of-Projection Balance by Account")
if projection:
    final = projection[-1]["by_account"]
    final_total = sum(final.values())
    acct_rows = []
    for person in PEOPLE:
        for acct in ACCOUNT_TYPES:
            bal = final.get((person, acct), 0.0)
            if bal > 0:
                acct_rows.append({
                    "Person":  person,
                    "Account": acct,
                    "Balance": f"${bal:,.0f}",
                    "% of Total": f"{bal/final_total*100:.1f}%" if final_total > 0 else "—",
                })
    if acct_rows:
        st.dataframe(pd.DataFrame(acct_rows), use_container_width=True, hide_index=True)

st.divider()

# ── Current milestone progress ────────────────────────────────────────────────

st.subheader("📊 Current Balance by Account")
if not balance_df.empty:
    disp = balance_df[balance_df["balance"] > 0].copy()
    disp = disp.sort_values("balance", ascending=False)
    for _, row in disp.iterrows():
        pct = row["balance"] / portfolio * 100 if portfolio > 0 else 0
        st.caption(f"{row['person']} — {row['account']}: **${row['balance']:,.2f}** ({pct:.1f}%)")
else:
    st.caption("No balance data yet.")
