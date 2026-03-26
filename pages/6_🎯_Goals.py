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
_today_year = date.today().year

# OAS 2026 quarterly-indexed rates
OAS_FULL_MONTHLY_65_74  = 727.67   # age 65–74 (full pension, 40 qualifying years)
OAS_FULL_MONTHLY_75PLUS = 800.44   # age 75+  (automatic 10% top-up since Jul 2022)
OAS_DEFERRAL_BONUS      = 0.006    # 0.6 % per month deferred past 65, max age 70

# CPP estimation constants (2026)
YMPE_2026           = 73_200    # Year's Maximum Pensionable Earnings
MAX_CPP_MONTHLY     = 1_364.60  # Maximum CPP at age 65 (2026)

# US Social Security estimation constants (2026)
SS_FRA              = 67        # Full Retirement Age for those born after 1960
SS_MAX_MONTHLY_USD  = 4_018     # 2026 maximum SS benefit at FRA
SS_BEND_1           = 1_226     # First AIME bend point (2026)
SS_BEND_2           = 7_391     # Second AIME bend point (2026)


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


def estimate_cpp_monthly(birth_year: int, cpp_start_age: int,
                         canada_resident_since: int, salary_cad: float) -> float:
    """
    Rough CPP estimate based on approximate contributing years and salary.

    CPP formula: (years_contributing / 39) × (earnings / YMPE) × max_cpp
    Adjusted for early (−0.6%/mo before 65) or late (+0.7%/mo after 65) claiming.
    A default earnings factor of 65% of YMPE is used when no salary is entered.
    """
    current_year    = date.today().year
    resident_from   = max(canada_resident_since, birth_year + 18)
    years_in_canada = max(0, current_year - resident_from)
    years_to_cpp    = max(0, cpp_start_age - (current_year - birth_year))
    total_cpp_years = min(years_in_canada + years_to_cpp, 39)

    earnings_factor = min(salary_cad / YMPE_2026, 1.0) if salary_cad > 0 else 0.65
    base            = (total_cpp_years / 39) * earnings_factor * MAX_CPP_MONTHLY

    if cpp_start_age < 65:
        base *= max(0.0, 1.0 - (65 - cpp_start_age) * 12 * 0.006)
    elif cpp_start_age > 65:
        base *= 1.0 + (cpp_start_age - 65) * 12 * 0.007

    return round(max(0.0, base))


def estimate_ss_monthly_usd(birth_year: int, ss_start_age: int,
                             canada_resident_since: int, salary_cad: float,
                             usd_cad_rate: float) -> float:
    """
    Rough US Social Security estimate using the PIA bend-point formula.

    US work years are estimated as the period from age 22 to when the person
    moved to Canada (canada_resident_since). Salary in CAD is converted to USD
    via usd_cad_rate and used as a proxy for average US earnings.
    SS uses 35 highest-earning years — non-contributing years dilute AIME.
    Adjusted for claiming age relative to FRA (67 for born after 1960).
    """
    us_work_start = birth_year + 22
    us_work_end   = max(us_work_start, min(canada_resident_since, birth_year + ss_start_age))
    us_work_years = min(max(0, us_work_end - us_work_start), 35)

    if us_work_years == 0:
        return 0.0

    # Fall back to 65% of YMPE (roughly median income) if no salary entered,
    # matching the same assumption used by the CPP estimator.
    effective_salary_cad = salary_cad if salary_cad > 0 else YMPE_2026 * 0.65
    salary_usd = effective_salary_cad / max(usd_cad_rate, 0.01)
    # SS averages over 35 years; gaps count as $0
    aime = salary_usd * us_work_years / 35 / 12

    # PIA bend-point formula
    if aime <= SS_BEND_1:
        pia = aime * 0.90
    elif aime <= SS_BEND_2:
        pia = SS_BEND_1 * 0.90 + (aime - SS_BEND_1) * 0.32
    else:
        pia = SS_BEND_1 * 0.90 + (SS_BEND_2 - SS_BEND_1) * 0.32 + (aime - SS_BEND_2) * 0.15

    # Adjust for claiming age vs FRA
    if ss_start_age < SS_FRA:
        months_early = (SS_FRA - ss_start_age) * 12
        if months_early <= 36:
            pia *= 1.0 - months_early * 5 / 900
        else:
            pia *= 1.0 - 36 * 5 / 900 - (months_early - 36) * 5 / 1200
    elif ss_start_age > SS_FRA:
        months_late = min((ss_start_age - SS_FRA) * 12, (70 - SS_FRA) * 12)
        pia *= 1.0 + months_late * 2 / 300

    return round(max(0.0, min(pia, SS_MAX_MONTHLY_USD)))

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

for col, person, birth_yr, eligible_yr, salary in zip(
    gov_cols,
    ["Isaac", "Katherine"],
    [isaac_birth,         katherine_birth],
    [isaac_tfsa_eligible, kath_tfsa_eligible],
    [isaac_salary,        katherine_salary],
):
    with col:
        st.markdown(f"**👤 {person}** (born {birth_yr})")

        # ── OAS ──
        st.markdown("🇨🇦 **OAS (Old Age Security)**")
        # Auto-estimate residency: years already in Canada + years until OAS age
        _current_age       = _today_year - birth_yr
        _years_in_canada   = max(0, _today_year - max(eligible_yr, birth_yr + 18))
        oas_age = st.selectbox(
            "OAS start age",
            options=[65, 66, 67, 68, 69, 70],
            index=0,
            key=f"oas_age_{person}",
            help="You can defer OAS past 65 for a 0.6%/month bonus (max +36% at 70).",
        )
        _oas_years_to_go   = max(0, oas_age - _current_age)
        _oas_est_residency = min(int(_years_in_canada + _oas_years_to_go), 40)
        oas_residency = st.number_input(
            "Canadian residency years at OAS start age",
            min_value=0, max_value=40, value=_oas_est_residency,
            key=f"oas_res_{person}",
            help="Years you'll have lived in Canada after turning 18, by the time OAS starts. "
                 "Full pension requires 40 years; partial = (years / 40) × full rate. "
                 f"Auto-estimated from your TFSA eligibility year ({eligible_yr}).",
        )
        oas_start_year = birth_yr + oas_age
        oas_monthly    = calc_oas_monthly(oas_residency, oas_age)
        oas_75_start   = birth_yr + 75
        oas_75_bump    = (OAS_FULL_MONTHLY_75PLUS - OAS_FULL_MONTHLY_65_74) * min(oas_residency / 40.0, 1.0)
        st.caption(
            f"Estimated OAS: **${oas_monthly:,.0f}/mo** starting {oas_start_year}"
            + (f" · +${oas_75_bump:,.0f}/mo top-up at 75 ({oas_75_start})" if oas_residency > 0 else "")
        )

        # ── CPP ──
        st.markdown("🇨🇦 **CPP (Canada Pension Plan)**")
        cpp_age = st.selectbox(
            "CPP start age",
            options=list(range(60, 71)),
            index=5,   # default 65
            key=f"cpp_age_{person}",
            help="Taking CPP before 65 reduces it 0.6%/mo; after 65 increases it 0.7%/mo (max age 70).",
        )
        _cpp_est = estimate_cpp_monthly(birth_yr, cpp_age, eligible_yr, salary)
        cpp_monthly = st.number_input(
            "Expected CPP (CA$/mo)",
            min_value=0.0, step=50.0, format="%.0f", value=float(_cpp_est),
            key=f"cpp_mo_{person}",
            help="Auto-estimated from your residency history and salary. "
                 "For a precise figure, check My Service Canada Account → CPP Statement of Contributions.",
        )
        cpp_start_year = birth_yr + cpp_age
        st.caption(
            f"CPP: **${cpp_monthly:,.0f}/mo** starting {cpp_start_year}"
            + (f" (est. based on ~{int(_years_in_canada + max(0, cpp_age - _current_age))} CPP years)" if _cpp_est > 0 else "")
        )

        # ── US Social Security ──
        st.markdown("🇺🇸 **US Social Security**")
        ss_age = st.selectbox(
            "SS start age",
            options=list(range(62, 71)),
            index=3,   # default 65
            key=f"ss_age_{person}",
            help="Full Retirement Age (FRA) is 67 for those born after 1960. "
                 "Taking SS at 62 reduces the benefit; at 70 it's maximised.",
        )
        _ss_est_usd = estimate_ss_monthly_usd(birth_yr, ss_age, eligible_yr, salary, usd_cad)
        _us_work_yrs = max(0, min(eligible_yr, birth_yr + ss_age) - (birth_yr + 22))
        ss_monthly_usd = st.number_input(
            "Expected SS benefit (USD/mo)",
            min_value=0.0, step=50.0, format="%.0f", value=float(_ss_est_usd),
            key=f"ss_mo_{person}",
            help="Auto-estimated from approximate US work history (before moving to Canada) and salary. "
                 "For a precise figure, visit ssa.gov/myaccount. "
                 "WEP was eliminated in Jan 2025 — your full SS is payable alongside CPP.",
        )
        ss_start_year  = birth_yr + ss_age
        ss_monthly_cad = ss_monthly_usd * usd_cad
        st.caption(
            f"SS: **${ss_monthly_usd:,.0f} USD/mo** (≈ ${ss_monthly_cad:,.0f} CA$) starting {ss_start_year}"
            + (f" (est. ~{_us_work_yrs} US work years)" if _ss_est_usd > 0 else " · set to $0 — no US work history detected")
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

# ─── Section 5: Retirement Income ────────────────────────────────────────────

st.subheader("🏖️ Retirement Income Estimate")
st.caption(
    "Set a target retirement year and the app will show projected income once you stop contributing, "
    "how that income is split between portfolio withdrawals and government benefits, "
    "and how long your portfolio lasts."
)

ret_c1, ret_c2 = st.columns(2)
with ret_c1:
    retirement_year = st.number_input(
        "Target retirement year",
        min_value=_today_year + 1,
        max_value=_today_year + 60,
        value=_today_year + 30,
        step=1,
        key="retirement_year",
        help="The year you plan to stop contributing to your accounts. "
             "Contributions stop; the portfolio continues growing and is drawn down.",
    )
with ret_c2:
    withdrawal_rate_pct = st.slider(
        "Annual safe withdrawal rate (%)",
        min_value=2.0, max_value=7.0, value=4.0, step=0.1,
        key="withdrawal_rate",
        help="The '4% rule' says you can withdraw 4% of your portfolio's value at retirement "
             "each year with a high chance of not outliving your money over 30 years. "
             "A lower rate is more conservative.",
    )

st.divider()

# ─── Section 6: Projection ────────────────────────────────────────────────────

st.subheader("📈 Year-by-Year Projection")

if total_monthly == 0 and expected_return == 0:
    st.info("Set a monthly contribution or return rate above to generate the projection.")
    st.stop()

# ── Simulation ────────────────────────────────────────────────────────────────

def run_projection(current_balances, current_room, monthly_contributions,
                   fhsa_lifetime_used, salaries, annual_return_rate,
                   gov_benefits=None, years=40,
                   retirement_year=None, annual_withdrawal_rate=0.0):
    """
    Month-by-month portfolio projection with optional retirement phase.

    Accumulation phase (before retirement_year):
      - Contributions flow in, room is tracked, RRSP/TFSA/FHSA caps enforced.

    Decumulation phase (from retirement_year onward):
      - Contributions stop.
      - A fixed monthly withdrawal is taken from the portfolio, distributed
        proportionally across all account buckets.
      - The withdrawal amount is locked in at the start of retirement as:
            retirement_balance × annual_withdrawal_rate / 12
      - Government benefit income continues to be added each month.
      - The portfolio balance can reach $0 (depleted_year is recorded).

    Returns: (results, milestone_hits, retirement_info)
      retirement_info = {
          "balance":         portfolio value at the start of retirement,
          "monthly_withdrawal": fixed CA$ drawn from portfolio each month,
          "depleted_year":   year the portfolio hits $0, or None,
      }
    """
    monthly_rate        = annual_return_rate / 12
    balances            = dict(current_balances)
    room                = {k: v for k, v in current_room.items()}
    fhsa_life           = dict(fhsa_lifetime_used)
    today_year          = date.today().year
    gov_benefits        = gov_benefits or {}

    for key in monthly_contributions:
        if key not in balances:
            balances[key] = 0.0

    results             = []
    milestone_hits      = {}
    benefit_starts      = {}

    retired             = False
    monthly_withdrawal  = 0.0
    retirement_balance  = 0.0
    depleted_year       = None

    for yr_offset in range(1, years + 1):
        cal_year    = today_year + yr_offset
        yr_contribs = {k: 0.0 for k in monthly_contributions}
        yr_capped   = []
        yr_benefits = []

        # ── Transition to retirement at the start of the year ─────────────────
        if retirement_year and not retired and cal_year >= retirement_year:
            retired            = True
            retirement_balance = sum(balances.values())   # portfolio only (no Benefits bucket)
            monthly_withdrawal = retirement_balance * annual_withdrawal_rate / 12
            # monthly_withdrawal is the target total income from the portfolio.
            # Government benefits will offset this — so the actual portfolio draw
            # each month is max(0, monthly_withdrawal - monthly_benefit_income).

        # ── January 1: refresh annual room (accumulation only) ────────────────
        if not retired:
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
        yr_benefit_income = 0.0   # total benefit income received this year

        for _ in range(12):
            # Calculate benefit income this month (same formula both phases)
            monthly_benefit_income = 0.0
            for person, benefits in gov_benefits.items():
                for ben in benefits:
                    if cal_year < ben["start_year"]:
                        continue
                    mo_ben = ben["monthly_cad"]
                    if ben.get("age_75_year") and cal_year >= ben["age_75_year"]:
                        mo_ben += ben.get("age_75_bump_cad", 0.0)
                    monthly_benefit_income += mo_ben
            yr_benefit_income += monthly_benefit_income

            if not retired:
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
            else:
                # Benefit income offsets the withdrawal — you spend it, not invest it.
                # Only the shortfall (if any) is drawn from the portfolio.
                net_draw      = max(0.0, monthly_withdrawal - monthly_benefit_income)
                savings_total = sum(balances.values())
                if savings_total > 0 and net_draw > 0:
                    actual_draw = min(net_draw, savings_total)
                    for k in list(balances.keys()):
                        share = balances[k] / savings_total
                        balances[k] = max(0.0, balances[k] - actual_draw * share)

            # Apply monthly return to portfolio balances only
            for k in balances:
                balances[k] = balances[k] * (1 + monthly_rate)

        # ── Year-end stats ─────────────────────────────────────────────────────
        savings_total_yr = sum(balances.values())   # no Benefits bucket to exclude
        total_contrib    = sum(yr_contribs.values())

        # Record portfolio depletion
        if retired and depleted_year is None and savings_total_yr <= 0:
            depleted_year = cal_year

        # Track room-exhausted accounts (accumulation only)
        if not retired:
            for person in PEOPLE:
                for acct in ["TFSA", "FHSA", "RRSP"]:
                    key = (person, acct)
                    if room.get(key, 1) <= 0 and monthly_contributions.get(key, 0) > 0:
                        label = f"{person} {acct}"
                        if label not in yr_capped:
                            yr_capped.append(label)

        # Track first year each benefit starts
        for person, benefits in gov_benefits.items():
            for ben in benefits:
                tag = f"{person} {ben['label']}"
                if tag not in benefit_starts and cal_year >= ben["start_year"]:
                    benefit_starts[tag] = cal_year
                    yr_benefits.append(tag)
                age75_tag = f"{person} OAS 75+ top-up"
                if (ben.get("age_75_year") and age75_tag not in benefit_starts
                        and cal_year >= ben["age_75_year"]):
                    benefit_starts[age75_tag] = cal_year
                    yr_benefits.append(age75_tag)

        # Track milestone hits (accumulation only)
        if not retired:
            for ms in milestones:
                if ms not in milestone_hits and total_bal >= ms:
                    milestone_hits[ms] = cal_year

        # Monthly income in retirement = what the portfolio covers + govt benefits.
        # During accumulation this is 0 (no withdrawals being made).
        yr_monthly_income = (monthly_withdrawal + yr_benefit_income / 12) if retired else 0.0

        results.append({
            "year":              cal_year,
            "total":             savings_total_yr,   # portfolio savings only
            "savings":           savings_total_yr,
            "contributed":       total_contrib,
            "capped":            yr_capped,
            "benefits":          yr_benefits,
            "retired":           retired,
            "monthly_income":    yr_monthly_income,  # portfolio draw + govt benefits/mo
            "monthly_benefit":   yr_benefit_income / 12,
            "by_account":        {k: balances[k] for k in balances},
        })

        # Stop once well past all milestones (accumulation) OR 35 years post-retirement
        if not retired and milestones and total_bal > max(milestones) * 1.2 and yr_offset >= 10:
            if retirement_year is None:
                break
        if retired and yr_offset >= (retirement_year - today_year + 35 if retirement_year else years):
            break

    retirement_info = {
        "balance":            retirement_balance,
        "monthly_withdrawal": monthly_withdrawal,
        "depleted_year":      depleted_year,
    }
    return results, milestone_hits, retirement_info

# Run at least to 35 years past retirement so we can show portfolio longevity
_proj_years = max(40, int(retirement_year) - _today_year + 35)

projection, milestone_hits, retirement_info = run_projection(
    current_balances      = current_balances,
    current_room          = current_room,
    monthly_contributions = monthly_contributions,
    fhsa_lifetime_used    = fhsa_lifetime_used,
    salaries              = salaries,
    annual_return_rate    = expected_return / 100,
    gov_benefits          = gov_benefits,
    years                 = _proj_years,
    retirement_year       = int(retirement_year),
    annual_withdrawal_rate= withdrawal_rate_pct / 100,
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

chart_rows = []
for r in projection:
    chart_rows.append({
        "Year":    r["year"],
        "Balance": r["savings"],   # portfolio only — benefits no longer inflate this
        "Phase":   "Retirement" if r["retired"] else "Accumulation",
    })
chart_df = pd.DataFrame(chart_rows)

fig = px.area(
    chart_df, x="Year", y="Balance", color="Phase",
    color_discrete_map={"Accumulation": "#2196F3", "Retirement": "#FF7043"},
    labels={"Year": "Year", "Balance": "Balance (CA$)", "Phase": ""},
)

# Retirement year vertical line
if any(r["retired"] for r in projection):
    fig.add_vline(
        x=int(retirement_year), line_dash="dot", line_color="#FF7043",
        annotation_text=f"Retire {int(retirement_year)}",
        annotation_position="top right",
    )

# Milestone lines
for ms in milestones:
    fig.add_hline(
        y=ms, line_dash="dash", line_color="orange",
        annotation_text=f"${ms:,.0f}",
        annotation_position="bottom right",
    )

# Depletion marker
if retirement_info["depleted_year"]:
    fig.add_vline(
        x=retirement_info["depleted_year"], line_dash="dash", line_color="red",
        annotation_text=f"Portfolio depleted {retirement_info['depleted_year']}",
        annotation_position="top left",
    )

fig.update_layout(
    yaxis_tickprefix="$", yaxis_tickformat=",.0f",
    margin=dict(l=0, r=0, t=20, b=0),
)
st.plotly_chart(fig, use_container_width=True)

# ── Year-by-year table ────────────────────────────────────────────────────────

table_rows = []
_ret_yr_int = int(retirement_year)
for r in projection:
    row = {
        "Year":              str(r["year"]),
        "Phase":             "🏖️ Retirement" if r["retired"] else "📈 Accumulation",
        "Portfolio Balance": f"${r['savings']:,.0f}",
        "Est. Monthly Income": f"${r['monthly_income']:,.0f}" if r["retired"] else "—",
        "  Govt Benefits/mo":  f"${r['monthly_benefit']:,.0f}" if r["retired"] else "—",
        "Contributed":       f"${r['contributed']:,.0f}" if not r["retired"] else "—",
    }
    flags = []
    if r["year"] == _ret_yr_int:
        flags.append(f"🏖️ Retirement begins · ${retirement_info['monthly_withdrawal']:,.0f}/mo portfolio draw")
    for ms in milestones:
        if milestone_hits.get(ms) == r["year"]:
            flags.append(f"🎯 Hit ${ms:,.0f}")
    for label in r["capped"]:
        flags.append(f"🏁 {label} room full")
    for label in r.get("benefits", []):
        flags.append(f"🏛️ {label} begins")
    if retirement_info["depleted_year"] == r["year"]:
        flags.append("⚠️ Portfolio depleted — govt benefits continue")
    row["Notes"] = " · ".join(flags) if flags else ""
    table_rows.append(row)

st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

st.divider()

# ── Retirement income breakdown ────────────────────────────────────────────────

st.subheader("🏖️ Retirement Income Breakdown")

_ret_balance   = retirement_info["balance"]
_monthly_draw  = retirement_info["monthly_withdrawal"]
_depleted      = retirement_info["depleted_year"]

if _ret_balance > 0:
    # Benefits active AT retirement (start_year <= retirement_year)
    ben_at_retirement   = sum(
        b["monthly_cad"]
        for p, blist in gov_benefits.items() for b in blist
        if b["start_year"] <= _ret_yr_int
    )
    # Benefits active once ALL streams have started
    ben_all_active      = sum(
        b["monthly_cad"]
        for p, blist in gov_benefits.items() for b in blist
    )
    _last_ben_year = max(
        (b["start_year"] for p, blist in gov_benefits.items() for b in blist),
        default=_ret_yr_int,
    )

    ri_c1, ri_c2, ri_c3 = st.columns(3)
    with ri_c1:
        st.metric(
            "Portfolio at Retirement",
            f"${_ret_balance:,.0f}",
            help=f"Projected balance at the start of {_ret_yr_int}.",
        )
        st.metric(
            "Monthly Portfolio Withdrawal",
            f"${_monthly_draw:,.0f}/mo",
            help=f"{withdrawal_rate_pct:.1f}% of ${_ret_balance:,.0f} ÷ 12.",
        )
    with ri_c2:
        st.metric(
            f"Income at Retirement ({_ret_yr_int})",
            f"${_monthly_draw + ben_at_retirement:,.0f}/mo",
            help="Portfolio withdrawal + any benefits already in payment at retirement.",
        )
        st.caption(
            f"Portfolio: **${_monthly_draw:,.0f}**/mo  "
            f"+ Benefits: **${ben_at_retirement:,.0f}**/mo"
        )
    with ri_c3:
        st.metric(
            f"Income Once All Benefits Active ({_last_ben_year})",
            f"${_monthly_draw + ben_all_active:,.0f}/mo",
            help="Portfolio withdrawal + all OAS, CPP, and SS streams.",
        )
        st.caption(
            f"Portfolio: **${_monthly_draw:,.0f}**/mo  "
            f"+ Benefits: **${ben_all_active:,.0f}**/mo"
        )

    st.divider()

    if _depleted:
        yrs_in_retirement = _depleted - _ret_yr_int
        st.warning(
            f"⚠️ **Portfolio depleted in {_depleted}** — {yrs_in_retirement} years into retirement. "
            f"After depletion, income drops to government benefits only "
            f"(**${ben_all_active:,.0f}/mo** once all are active). "
            f"Consider a lower withdrawal rate or later retirement date.",
            icon=None,
        )
    else:
        oldest_person_age_at_end = max(
            _ret_yr_int + 35 - isaac_birth,
            _ret_yr_int + 35 - katherine_birth,
        )
        st.success(
            f"✅ **Portfolio sustains 35+ years of retirement** at the {withdrawal_rate_pct:.1f}% "
            f"withdrawal rate — taking you to roughly age {oldest_person_age_at_end}. "
            f"Government benefits provide an additional income floor throughout."
        )
else:
    st.info("Projection did not reach the retirement year — adjust contributions or return rate.")

st.divider()

# ── Government benefit income summary ─────────────────────────────────────────

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
    all_accts   = ACCOUNT_TYPES
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
