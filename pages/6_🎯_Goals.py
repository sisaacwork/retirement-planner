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

# OAS 2026 quarterly-indexed rates
OAS_FULL_MONTHLY_65_74  = 727.67   # age 65–74 (full pension, 40 qualifying years)
OAS_FULL_MONTHLY_75PLUS = 800.44   # age 75+  (automatic 10% top-up since Jul 2022)
OAS_DEFERRAL_BONUS      = 0.006    # 0.6 % per month deferred past 65, max age 70


def calc_oas_monthly(residency_years: float, start_age: int) -> float:
    """
    Estimate monthly OAS based on Canadian residency and chosen start age.

    Proration rule: full pension requires 40 years of residency after age 18.
    Deferral bonus:  0.6 % per month deferred past 65 (max 36 % at age 70).
    The 75+ top-up is applied automatically once the person turns 75.
    We return the age-65-to-74 amount here; the 75+ bump is handled in the sim.
    """
    proration       = min(residency_years / 40.0, 1.0)
    deferral_months = max(0, (min(start_age, 70) - 65) * 12)
    deferral_factor = 1.0 + OAS_DEFERRAL_BONUS * deferral_months
    return OAS_FULL_MONTHLY_65_74 * proration * deferral_factor

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

# ─── Section 3: Government Benefits ──────────────────────────────────────────

st.subheader("🏛️ Government Benefits (OAS / CPP / Social Security)")
st.caption(
    "When these benefits begin, the monthly income is added to your portfolio each month — "
    "effectively reducing how much you need to draw down from savings. "
    "Enter your **own estimates** from Service Canada and SSA.gov, as actual amounts depend "
    "on your full contribution history."
)

with st.expander("ℹ️ Notes on US Social Security & the Canada-US Totalization Agreement", expanded=False):
    st.markdown("""
**Totalization Agreement (Canada–US)**
Canada and the US have a Social Security Totalization Agreement. If you've worked in both countries,
periods of contribution can be *combined* to meet minimum eligibility requirements for each country's
benefit — but the payout from each country is still calculated only from that country's own contributions.
This means you can receive **both CPP and US Social Security** simultaneously.

**Windfall Elimination Provision (WEP) — no longer applies**
The WEP historically reduced US Social Security benefits for people who also received a pension
from non-SS-covered work (like CPP). The **Social Security Fairness Act**, signed into law in
January 2025, **eliminated WEP**, so your full SS benefit is payable alongside CPP.

**Getting your estimates**
- CPP: Log in to [My Service Canada Account](https://www.canada.ca/en/employment-social-development/services/my-account.html) → "Canada Pension Plan" → Statement of Contributions
- US SS: Visit [ssa.gov/myaccount](https://www.ssa.gov/myaccount/) to see your Social Security Statement
""")

# USD → CAD conversion for Social Security
usd_cad = st.number_input(
    "USD → CAD exchange rate",
    min_value=1.0, max_value=2.0, value=1.38, step=0.01, format="%.2f",
    help="Used to convert US Social Security amounts to CA$. Adjust to reflect your assumption.",
)

st.markdown("---")

gov_cols = st.columns(2)
gov_benefits = {}   # {person: [{"start_year", "monthly_cad", "label", "age_75_bump"}, ...]}

for col, person, birth_yr in zip(gov_cols,
                                  ["Isaac", "Katherine"],
                                  [isaac_birth, katherine_birth]):
    with col:
        st.markdown(f"**👤 {person}** (born {birth_yr})")

        # ── OAS ──
        st.markdown("🇨🇦 **OAS (Old Age Security)**")
        oas_residency = st.number_input(
            f"Canadian residency years at OAS start age",
            min_value=0, max_value=40, value=10,
            key=f"oas_res_{person}",
            help="Years you'll have lived in Canada after turning 18, by the time OAS starts. "
                 "Full pension requires 40 years; partial = (years / 40) × full rate.",
        )
        oas_age = st.selectbox(
            "OAS start age",
            options=[65, 66, 67, 68, 69, 70],
            index=0,
            key=f"oas_age_{person}",
            help="You can defer OAS past 65 for a 0.6%/month bonus (max +36% at 70).",
        )
        oas_start_year = birth_yr + oas_age
        oas_monthly    = calc_oas_monthly(oas_residency, oas_age)
        oas_75_start   = birth_yr + 75  # year the automatic 75+ top-up kicks in
        oas_75_bump    = (OAS_FULL_MONTHLY_75PLUS - OAS_FULL_MONTHLY_65_74) * min(oas_residency / 40.0, 1.0)
        st.caption(
            f"Estimated OAS: **${oas_monthly:,.0f}/mo** starting {oas_start_year}"
            + (f" · +${oas_75_bump:,.0f}/mo top-up at 75 ({oas_75_start})" if oas_residency > 0 else "")
        )

        # ── CPP ──
        st.markdown("🇨🇦 **CPP (Canada Pension Plan)**")
        cpp_monthly = st.number_input(
            "Expected CPP (CA$/mo)",
            min_value=0.0, step=50.0, format="%.0f", value=0.0,
            key=f"cpp_mo_{person}",
            help="Get your personalised estimate at My Service Canada Account → CPP Statement of Contributions.",
        )
        cpp_age = st.selectbox(
            "CPP start age",
            options=list(range(60, 71)),
            index=5,   # default 65
            key=f"cpp_age_{person}",
            help="Taking CPP before 65 reduces it 0.6%/mo; after 65 increases it 0.7%/mo (max age 70).",
        )
        cpp_start_year = birth_yr + cpp_age
        if cpp_monthly > 0:
            st.caption(f"CPP: **${cpp_monthly:,.0f}/mo** starting {cpp_start_year}")

        # ── US Social Security ──
        st.markdown("🇺🇸 **US Social Security**")
        ss_monthly_usd = st.number_input(
            "Expected SS benefit (USD/mo)",
            min_value=0.0, step=50.0, format="%.0f", value=0.0,
            key=f"ss_mo_{person}",
            help="Find your estimate at ssa.gov/myaccount. WEP was eliminated in Jan 2025, so "
                 "your full SS benefit is payable alongside CPP.",
        )
        ss_age = st.selectbox(
            "SS start age",
            options=list(range(62, 71)),
            index=3,   # default 65
            key=f"ss_age_{person}",
            help="Full Retirement Age (FRA) is 67 for those born after 1960. "
                 "Taking SS at 62 reduces the benefit; at 70 it's maximised.",
        )
        ss_start_year  = birth_yr + ss_age
        ss_monthly_cad = ss_monthly_usd * usd_cad
        if ss_monthly_usd > 0:
            st.caption(
                f"SS: **${ss_monthly_usd:,.0f} USD/mo** (≈ ${ss_monthly_cad:,.0f} CA$) "
                f"starting {ss_start_year}"
            )

        # Build the benefits list for this person
        benefits_list = []
        if oas_monthly > 0:
            benefits_list.append({
                "start_year": oas_start_year,
                "monthly_cad": oas_monthly,
                "label": "OAS",
                "age_75_year": oas_75_start,
                "age_75_bump_cad": oas_75_bump,
            })
        if cpp_monthly > 0:
            benefits_list.append({
                "start_year": cpp_start_year,
                "monthly_cad": cpp_monthly,
                "label": "CPP",
                "age_75_year": None,
                "age_75_bump_cad": 0.0,
            })
        if ss_monthly_cad > 0:
            benefits_list.append({
                "start_year": ss_start_year,
                "monthly_cad": ss_monthly_cad,
                "label": "US SS",
                "age_75_year": None,
                "age_75_bump_cad": 0.0,
            })
        gov_benefits[person] = benefits_list

st.divider()

# ─── Section 4: Current room snapshot ────────────────────────────────────────

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
                   fhsa_lifetime_used, salaries, annual_return_rate,
                   gov_benefits=None, years=40):
    """
    Month-by-month portfolio projection.

    gov_benefits: dict of {person: [{"start_year", "monthly_cad", "label",
                                      "age_75_year", "age_75_bump_cad"}, ...]}
    Government benefit income is added to a virtual "(person, Benefits)" bucket
    each month once the benefit's start_year is reached — effectively modelling
    the income as supplementing (or replacing) portfolio withdrawals.
    The 75+ OAS top-up is applied automatically once age_75_year is hit.
    """
    monthly_rate   = annual_return_rate / 12
    balances       = dict(current_balances)
    room           = {k: v for k, v in current_room.items()}
    fhsa_life      = dict(fhsa_lifetime_used)
    today_year     = date.today().year
    gov_benefits   = gov_benefits or {}

    # Ensure all contribution keys exist in balances
    for key in monthly_contributions:
        if key not in balances:
            balances[key] = 0.0

    results        = []
    milestone_hits = {}   # milestone value → year first hit
    benefit_starts = {}   # label → year benefit first appeared in notes

    for yr_offset in range(1, years + 1):
        cal_year    = today_year + yr_offset
        yr_contribs = {k: 0.0 for k in monthly_contributions}
        yr_capped   = []
        yr_benefits = []   # benefit labels that begin this year

        # ── January 1: refresh annual room ────────────────────────────────────
        for person in PEOPLE:
            key = (person, "TFSA")
            annual_tfsa = TFSA_ANNUAL_LIMITS.get(cal_year, TFSA_FUTURE_LIMIT)
            room[key] = room.get(key, 0) + annual_tfsa

        for person in PEOPLE:
            key = (person, "FHSA")
            used = fhsa_life.get(person, 0)
            if used < FHSA_LIFETIME_LIMIT:
                new_fhsa = min(FHSA_ANNUAL_LIMIT, FHSA_LIFETIME_LIMIT - used)
                room[key] = room.get(key, 0) + new_fhsa

        for person, salary in salaries.items():
            if salary > 0:
                key = (person, "RRSP")
                new_rrsp = min(salary * 0.18, RRSP_ANNUAL_MAX)
                room[key] = room.get(key, 0) + new_rrsp

        # ── Run 12 months ──────────────────────────────────────────────────────
        for _ in range(12):
            # Regular contributions
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

            # Government benefit income
            for person, benefits in gov_benefits.items():
                for ben in benefits:
                    if cal_year < ben["start_year"]:
                        continue
                    monthly_cad = ben["monthly_cad"]
                    # Apply OAS 75+ top-up if applicable
                    if ben.get("age_75_year") and cal_year >= ben["age_75_year"]:
                        monthly_cad += ben.get("age_75_bump_cad", 0.0)
                    ben_key = (person, "Benefits")
                    balances[ben_key] = balances.get(ben_key, 0.0) + monthly_cad

            # Apply monthly return to all account balances
            for k in balances:
                balances[k] = balances[k] * (1 + monthly_rate)

        # ── Year-end stats ─────────────────────────────────────────────────────
        total_bal     = sum(balances.values())
        total_contrib = sum(yr_contribs.values())

        # Track room-exhausted accounts
        for person in PEOPLE:
            for acct in ["TFSA", "FHSA", "RRSP"]:
                key = (person, acct)
                if room.get(key, 1) <= 0 and monthly_contributions.get(key, 0) > 0:
                    label = f"{person} {acct}"
                    if label not in yr_capped:
                        yr_capped.append(label)

        # Track first year each benefit starts (for table notes)
        for person, benefits in gov_benefits.items():
            for ben in benefits:
                tag = f"{person} {ben['label']}"
                if tag not in benefit_starts and cal_year >= ben["start_year"]:
                    benefit_starts[tag] = cal_year
                    yr_benefits.append(tag)
                # OAS 75+ top-up flag
                age75_tag = f"{person} OAS 75+ top-up"
                if (ben.get("age_75_year") and age75_tag not in benefit_starts
                        and cal_year >= ben["age_75_year"]):
                    benefit_starts[age75_tag] = cal_year
                    yr_benefits.append(age75_tag)

        # Track milestone hits
        for ms in milestones:
            if ms not in milestone_hits and total_bal >= ms:
                milestone_hits[ms] = cal_year

        results.append({
            "year":        cal_year,
            "total":       total_bal,
            "contributed": total_contrib,
            "capped":      yr_capped,
            "benefits":    yr_benefits,
            "by_account":  {k: balances[k] for k in balances},
        })

        # Stop once well past all milestones (min 10 years)
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
    gov_benefits          = gov_benefits,
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
    flags = []
    for ms in milestones:
        if milestone_hits.get(ms) == r["year"]:
            flags.append(f"🎯 Hit ${ms:,.0f}")
    for label in r["capped"]:
        flags.append(f"🏁 {label} room full")
    for label in r.get("benefits", []):
        flags.append(f"🏛️ {label} begins")
    row["Notes"] = " · ".join(flags) if flags else ""
    table_rows.append(row)

st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

st.divider()

# ── Retirement income summary ──────────────────────────────────────────────────

any_benefits = any(len(v) > 0 for v in gov_benefits.values())
if any_benefits:
    st.subheader("🏛️ Projected Government Benefit Income")
    st.caption(
        "Monthly income from OAS, CPP, and US Social Security once all benefits are in payment. "
        "At age 75, OAS automatically increases by ~10%. Amounts are in today's dollars (not inflation-adjusted)."
    )

    inc_rows = []
    for person, benefits in gov_benefits.items():
        for ben in benefits:
            age_at_start = ben["start_year"] - ([isaac_birth, katherine_birth][PEOPLE.index(person)])
            row = {
                "Person":       person,
                "Benefit":      ben["label"],
                "Starts":       str(ben["start_year"]),
                "Age at Start": str(age_at_start),
                "Monthly (CA$)": f"${ben['monthly_cad']:,.0f}",
            }
            if ben.get("age_75_year") and ben.get("age_75_bump_cad", 0) > 0:
                row["Notes"] = f"+${ben['age_75_bump_cad']:,.0f}/mo top-up at age 75 ({ben['age_75_year']})"
            else:
                row["Notes"] = ""
            inc_rows.append(row)

    if inc_rows:
        st.dataframe(pd.DataFrame(inc_rows), use_container_width=True, hide_index=True)

    # Total combined monthly income per person once all benefits are in payment
    st.markdown("**Combined monthly income once all benefits are active:**")
    for person, benefits in gov_benefits.items():
        if benefits:
            total_mo = sum(b["monthly_cad"] for b in benefits)
            latest   = max(b["start_year"] for b in benefits)
            st.caption(f"{person}: **${total_mo:,.0f}/mo CA$** (all benefits active from {latest})")

st.divider()

# ── Per-account balance breakdown ─────────────────────────────────────────────

st.subheader("💼 End-of-Projection Balance by Account")
if projection:
    final       = projection[-1]["by_account"]
    final_total = sum(final.values())
    acct_rows   = []
    all_accts   = ACCOUNT_TYPES + ["Benefits"]
    for person in PEOPLE:
        for acct in all_accts:
            bal = final.get((person, acct), 0.0)
            if bal > 0:
                acct_rows.append({
                    "Person":     person,
                    "Account":    acct,
                    "Balance":    f"${bal:,.0f}",
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
