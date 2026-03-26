"""
Contribution Optimizer — tax-efficient RRSP / TFSA / FHSA split for Ontario residents.
Includes a full tax refund / balance-owing estimator with pay stub inputs.
Based on 2025 federal + Ontario tax rates (approximate; verify with CRA).
"""
import streamlit as st
import plotly.graph_objects as go
from datetime import date

from utils.sheets import get_contributions, get_withdrawals, get_settings
from utils.calculations import (
    tfsa_remaining_room,
    fhsa_remaining_room,
    rrsp_remaining_room,
)
from utils.constants import FHSA_ANNUAL_LIMIT

# ─── 2025 Tax constants ────────────────────────────────────────────────────────

# Federal brackets + BPA
FED_BPA      = 16_129.0
FED_BRACKETS = [
    (57_375,       0.15),
    (114_750,      0.205),
    (158_519,      0.26),
    (220_000,      0.29),
    (float("inf"), 0.33),
]

# Ontario brackets + BPA
ON_BPA       = 11_865.0
ON_BRACKETS  = [
    (51_446,       0.0505),
    (102_894,      0.0915),
    (150_000,      0.1116),
    (220_000,      0.1216),
    (float("inf"), 0.1316),
]
ON_SURTAX_T1 = 5_315.0
ON_SURTAX_T2 = 6_802.0

# Payroll maximums (2025 estimates — actual amounts come from pay stubs)
CPP_YMPE          = 73_200.0    # Year's Maximum Pensionable Earnings
CPP_YMPE2         = 81_900.0    # CPP2 upper ceiling (estimated 2025)
CPP_BASIC_EXEMPT  = 3_500.0
CPP1_RATE         = 0.0595
CPP2_RATE         = 0.04
EI_MAX_INSURABLE  = 65_700.0    # estimated 2025 max insurable earnings
EI_RATE           = 0.0166

# Non-refundable credit amounts (2025)
FED_EMPLOYMENT_AMT  = 1_433.0   # Canada Employment Amount
FED_DISABILITY_AMT  = 9_428.0   # federal disability amount
FED_MED_THRESHOLD   = 2_759.0   # lesser of this or 3% net income
FED_AGE_AMOUNT      = 8_396.0   # age amount for 65+
FED_AGE_CLAW_START  = 42_335.0  # age amount clawback starts here
FED_PENSION_AMT     = 2_000.0   # eligible pension income amount

ON_DISABILITY_AMT   = 9_428.0   # Ontario disability amount
ON_AGE_AMOUNT       = 5_632.0   # Ontario age amount for 65+
ON_AGE_CLAW_START   = 40_495.0
ON_PENSION_AMT      = 1_592.0   # Ontario pension income amount

# Optimizer threshold
RRSP_THRESHOLD   = 0.31
FHSA_YEAR_MAX    = FHSA_ANNUAL_LIMIT * 2
PAY_FREQUENCIES  = {
    "Bi-weekly (26×/yr)":    26,
    "Semi-monthly (24×/yr)": 24,
    "Weekly (52×/yr)":       52,
    "Monthly (12×/yr)":      12,
}


# ─── Core tax helpers ─────────────────────────────────────────────────────────

def _bracket_tax(income: float, brackets: list) -> float:
    tax, prev = 0.0, 0.0
    for upper, rate in brackets:
        if income <= prev:
            break
        tax += (min(income, upper) - prev) * rate
        prev = upper
    return tax


def _marginal_rate(income: float, brackets: list) -> float:
    for upper, rate in brackets:
        if income <= upper:
            return rate
    return brackets[-1][1]


def _ontario_surtax(on_net: float) -> float:
    s = 0.0
    if on_net > ON_SURTAX_T1:
        s += (min(on_net, ON_SURTAX_T2) - ON_SURTAX_T1) * 0.20
    if on_net > ON_SURTAX_T2:
        s += (on_net - ON_SURTAX_T2) * 0.56
    return s


def calc_tax(gross: float, rrsp_ded: float = 0.0, fhsa_ded: float = 0.0):
    """Simplified tax for the optimizer (marginal rate + quick comparison)."""
    taxable  = max(0.0, gross - rrsp_ded - fhsa_ded)
    fed_tax  = max(0.0, _bracket_tax(taxable, FED_BRACKETS) - FED_BPA * 0.15)
    on_net   = max(0.0, _bracket_tax(taxable, ON_BRACKETS) - ON_BPA * 0.0505)
    on_tax   = on_net + _ontario_surtax(on_net)
    fed_m    = _marginal_rate(taxable, FED_BRACKETS)
    on_m     = _marginal_rate(taxable, ON_BRACKETS)
    if on_net > ON_SURTAX_T2:
        on_m *= 1.56
    elif on_net > ON_SURTAX_T1:
        on_m *= 1.20
    return fed_tax, on_tax, fed_tax + on_tax, round(fed_m + on_m, 4)


def calc_tax_full(
    gross: float,
    rrsp_ded: float      = 0.0,
    fhsa_ded: float      = 0.0,
    union_dues: float    = 0.0,
    childcare: float     = 0.0,
    moving: float        = 0.0,
    cpp_paid: float      = 0.0,
    ei_paid: float       = 0.0,
    charitable: float    = 0.0,
    medical: float       = 0.0,
    pension_income: float = 0.0,
    disability: bool     = False,
    age_65_plus: bool    = False,
    first_time_buyer: bool = False,
) -> dict:
    """
    Full Ontario + Federal tax calculation with common deductions and credits.
    Returns a detailed breakdown dict.
    """
    # ── Step 1: Net income (deductions from income) ───────────────────────────
    income_deds = rrsp_ded + fhsa_ded + union_dues + childcare + moving
    net_income  = max(0.0, gross - income_deds)

    # ── Step 2: Federal tax ───────────────────────────────────────────────────
    fed_gross_tax  = _bracket_tax(net_income, FED_BRACKETS)

    # Non-refundable credits (× 15%)
    fed_bpa_cr   = FED_BPA * 0.15
    fed_cpp_cr   = cpp_paid * 0.15
    fed_ei_cr    = ei_paid * 0.15
    fed_emp_cr   = min(gross, FED_EMPLOYMENT_AMT) * 0.15

    age_amt_fed  = 0.0
    if age_65_plus:
        raw = max(0.0, FED_AGE_AMOUNT - max(0.0, net_income - FED_AGE_CLAW_START) * 0.15)
        age_amt_fed = raw * 0.15

    pension_cr_fed = min(pension_income, FED_PENSION_AMT) * 0.15

    fed_dis_cr   = FED_DISABILITY_AMT * 0.15 if disability else 0.0

    if charitable > 0:
        fed_char_cr = min(charitable, 200) * 0.15 + max(0, charitable - 200) * 0.29
    else:
        fed_char_cr = 0.0

    med_floor    = min(net_income * 0.03, FED_MED_THRESHOLD)
    fed_med_cr   = max(0.0, medical - med_floor) * 0.15

    fthb_cr      = 1_500.0 if first_time_buyer else 0.0   # $10,000 × 15%

    fed_credits  = (fed_bpa_cr + fed_cpp_cr + fed_ei_cr + fed_emp_cr +
                    age_amt_fed + pension_cr_fed + fed_dis_cr +
                    fed_char_cr + fed_med_cr + fthb_cr)
    fed_tax      = max(0.0, fed_gross_tax - fed_credits)

    # ── Step 3: Ontario tax ───────────────────────────────────────────────────
    on_gross_tax  = _bracket_tax(net_income, ON_BRACKETS)

    on_bpa_cr    = ON_BPA * 0.0505
    on_cpp_cr    = cpp_paid * 0.0505
    on_ei_cr     = ei_paid * 0.0505

    age_amt_on   = 0.0
    if age_65_plus:
        raw = max(0.0, ON_AGE_AMOUNT - max(0.0, net_income - ON_AGE_CLAW_START) * 0.15)
        age_amt_on = raw * 0.0505

    pension_cr_on  = min(pension_income, ON_PENSION_AMT) * 0.0505
    on_dis_cr    = ON_DISABILITY_AMT * 0.0505 if disability else 0.0

    if charitable > 0:
        on_char_cr = min(charitable, 200) * 0.0505 + max(0, charitable - 200) * 0.1116
    else:
        on_char_cr = 0.0

    on_med_cr    = max(0.0, medical - med_floor) * 0.0505

    on_credits   = (on_bpa_cr + on_cpp_cr + on_ei_cr + age_amt_on +
                    pension_cr_on + on_dis_cr + on_char_cr + on_med_cr)
    on_net       = max(0.0, on_gross_tax - on_credits)
    on_surtax    = _ontario_surtax(on_net)
    on_tax       = on_net + on_surtax

    return dict(
        gross=gross, net_income=net_income, income_deds=income_deds,
        rrsp_ded=rrsp_ded, fhsa_ded=fhsa_ded, union_dues=union_dues,
        childcare=childcare, moving=moving,
        # Federal
        fed_gross_tax=fed_gross_tax,
        fed_bpa_cr=fed_bpa_cr, fed_cpp_cr=fed_cpp_cr, fed_ei_cr=fed_ei_cr,
        fed_emp_cr=fed_emp_cr, age_amt_fed=age_amt_fed, pension_cr_fed=pension_cr_fed,
        fed_dis_cr=fed_dis_cr, fed_char_cr=fed_char_cr, fed_med_cr=fed_med_cr,
        fthb_cr=fthb_cr, fed_credits=fed_credits, fed_tax=fed_tax,
        # Ontario
        on_gross_tax=on_gross_tax, on_credits=on_credits,
        on_bpa_cr=on_bpa_cr, on_cpp_cr=on_cpp_cr, on_ei_cr=on_ei_cr,
        age_amt_on=age_amt_on, pension_cr_on=pension_cr_on,
        on_dis_cr=on_dis_cr, on_char_cr=on_char_cr, on_med_cr=on_med_cr,
        on_surtax=on_surtax, on_tax=on_tax,
        total_tax=fed_tax + on_tax,
    )


# ─── CPP / EI estimators (auto-defaults for pay stub inputs) ─────────────────

def estimate_cpp_annual(gross: float) -> float:
    cpp1 = max(0.0, min(gross, CPP_YMPE) - CPP_BASIC_EXEMPT) * CPP1_RATE
    cpp2 = max(0.0, min(gross, CPP_YMPE2) - CPP_YMPE) * CPP2_RATE
    return cpp1 + cpp2


def estimate_ei_annual(gross: float) -> float:
    return min(gross, EI_MAX_INSURABLE) * EI_RATE


# ─── Paycheque helpers ────────────────────────────────────────────────────────

def paychecks_remaining_this_year(pay_periods: int) -> int:
    today      = date.today()
    year_start = date(today.year, 1, 1)
    year_end   = date(today.year, 12, 31)
    days_total   = (year_end - year_start).days + 1
    days_elapsed = (today - year_start).days
    completed    = round(days_elapsed / days_total * pay_periods)
    return max(0, pay_periods - completed)


def paychecks_completed_this_year(pay_periods: int) -> int:
    return pay_periods - paychecks_remaining_this_year(pay_periods)


# ─── Optimizer ────────────────────────────────────────────────────────────────

def optimize_contributions(
    gross: float,
    rrsp_lumpsum_room: float,
    tfsa_room: float,
    fhsa_room: float,
    fhsa_is_open: bool,
    budget: float,
    annual_payroll_rrsp: float = 0.0,
):
    _, _, baseline_tax, marginal = calc_tax(gross)
    rec       = {"FHSA": 0.0, "RRSP": 0.0, "TFSA": 0.0}
    remaining = budget

    if fhsa_is_open and fhsa_room > 0 and remaining > 0:
        amt         = min(fhsa_room, FHSA_YEAR_MAX, remaining)
        rec["FHSA"] = amt
        remaining  -= amt

    if remaining > 0:
        if marginal >= RRSP_THRESHOLD:
            rrsp_amt    = min(rrsp_lumpsum_room, remaining)
            rec["RRSP"] = rrsp_amt
            remaining  -= rrsp_amt
            if remaining > 0:
                rec["TFSA"] = min(tfsa_room, remaining)
        else:
            tfsa_amt    = min(tfsa_room, remaining)
            rec["TFSA"] = tfsa_amt
            remaining  -= tfsa_amt
            if remaining > 0:
                rec["RRSP"] = min(rrsp_lumpsum_room, remaining)

    total_rrsp_ded = rec["RRSP"] + annual_payroll_rrsp
    _, _, new_tax, _ = calc_tax(gross, rrsp_ded=total_rrsp_ded, fhsa_ded=rec["FHSA"])
    savings = baseline_tax - new_tax
    return rec, baseline_tax, new_tax, savings, marginal


# ─── Page setup ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="Contribution Optimizer", page_icon="💡", layout="wide")
st.title("💡 Contribution Optimizer & Tax Estimator")
st.caption(
    "Plan your RRSP / TFSA / FHSA split and estimate your tax refund or balance owing "
    "for the year — based on **Ontario 2025** federal + provincial rates. "
    "All figures are estimates; confirm with CRA My Account and a tax professional."
)
st.divider()

# ─── Load data ────────────────────────────────────────────────────────────────

contributions = get_contributions()
withdrawals   = get_withdrawals()
settings      = get_settings()

def s(key, default="0"):
    return settings.get(key, default)

try:
    isaac_birth_year        = int(s("isaac_birth_year",              "1995"))
    katherine_birth_year    = int(s("katherine_birth_year",          "1995"))
    isaac_tfsa_eligible     = int(s("tfsa_eligible_year_isaac",      "2025"))
    katherine_tfsa_eligible = int(s("tfsa_eligible_year_katherine",  "2026"))
    isaac_fhsa_open         = int(s("fhsa_open_year_isaac",          "2025"))
    katherine_fhsa_open     = int(s("fhsa_open_year_katherine",      "2026"))
    rrsp_room_isaac         = float(s("rrsp_room_isaac",             "0"))
    rrsp_room_katherine     = float(s("rrsp_room_katherine",         "0"))
    tfsa_prior_isaac        = float(s("tfsa_prior_contributions_isaac",     "0"))
    tfsa_prior_katherine    = float(s("tfsa_prior_contributions_katherine", "0"))
    tfsa_prior_w_isaac      = float(s("tfsa_prior_withdrawals_isaac",       "0"))
    tfsa_prior_w_katherine  = float(s("tfsa_prior_withdrawals_katherine",   "0"))
    fhsa_prior_isaac        = float(s("fhsa_prior_contributions_isaac",     "0"))
    fhsa_prior_katherine    = float(s("fhsa_prior_contributions_katherine", "0"))
except (ValueError, TypeError):
    st.error("⚠️ Some settings are missing or invalid. Please visit ⚙️ Settings to configure them.")
    st.stop()

current_year = date.today().year

# ─── Pre-compute remaining room ───────────────────────────────────────────────

people = {
    "Isaac": dict(
        birth_year=isaac_birth_year, tfsa_eligible=isaac_tfsa_eligible,
        fhsa_open=isaac_fhsa_open, rrsp_noa=rrsp_room_isaac,
        tfsa_prior=tfsa_prior_isaac, tfsa_prior_w=tfsa_prior_w_isaac,
        fhsa_prior=fhsa_prior_isaac,
    ),
    "Katherine": dict(
        birth_year=katherine_birth_year, tfsa_eligible=katherine_tfsa_eligible,
        fhsa_open=katherine_fhsa_open, rrsp_noa=rrsp_room_katherine,
        tfsa_prior=tfsa_prior_katherine, tfsa_prior_w=tfsa_prior_w_katherine,
        fhsa_prior=fhsa_prior_katherine,
    ),
}

for name, d in people.items():
    d["tfsa_remaining"] = tfsa_remaining_room(
        d["birth_year"], contributions,
        prior_contributions=d["tfsa_prior"],
        prior_withdrawals=d["tfsa_prior_w"],
        person=name,
        eligible_from_year=d["tfsa_eligible"],
        withdrawals_df=withdrawals,
    )
    d["fhsa_remaining"] = fhsa_remaining_room(
        d["fhsa_open"], contributions, d["fhsa_prior"], person=name
    )
    d["rrsp_remaining"] = rrsp_remaining_room(d["rrsp_noa"], contributions, person=name)
    d["fhsa_is_open"]   = d["fhsa_open"] <= current_year
    d["age"]            = current_year - d["birth_year"]

    rrsp_used_ytd = 0.0
    if not contributions.empty:
        mask = (
            (contributions["account"] == "RRSP") &
            (contributions["person"]  == name) &
            (contributions["date"].dt.year == current_year)
        )
        rrsp_used_ytd = float(contributions[mask]["amount"].sum())
    d["rrsp_used_ytd"] = rrsp_used_ytd


# ─── Per-person panels ────────────────────────────────────────────────────────

for name, d in people.items():
    st.subheader(f"👤 {name}")

    # ── Tabs: Optimizer | Refund Estimator ───────────────────────────────────
    tab_opt, tab_refund = st.tabs(["💡 Contribution Optimizer", "💸 Tax Refund Estimator"])

    # ════════════════════════════════════════════════════════════════════════
    # TAB 1: CONTRIBUTION OPTIMIZER
    # ════════════════════════════════════════════════════════════════════════
    with tab_opt:
        # ── Income input ──────────────────────────────────────────────────
        gross = st.number_input(
            "Expected gross employment income this year (CA$)",
            min_value=0.0, step=1_000.0, format="%.2f", value=70_000.0,
            key=f"gross_{name}",
            help=(
                "Total T4 employment income before any deductions. "
                "If your employer's RRSP match appears as a taxable benefit on your T4, include it here."
            ),
        )

        # ── Payroll RRSP ──────────────────────────────────────────────────
        with st.expander("💼 Payroll RRSP / Group RRSP", expanded=True):
            pc1, pc2, pc3 = st.columns(3)
            with pc1:
                employee_pct = st.number_input(
                    "Your contribution (% of gross)", min_value=0.0, max_value=25.0,
                    step=0.5, format="%.1f", value=0.0, key=f"emp_pct_{name}",
                    help="% of each paycheque deducted automatically for your group RRSP.",
                )
            with pc2:
                employer_pct = st.number_input(
                    "Employer match (% of gross)", min_value=0.0, max_value=25.0,
                    step=0.5, format="%.1f", value=0.0, key=f"er_pct_{name}",
                    help="Your employer's RRSP contribution as a % of your gross — free money that also reduces your taxable income.",
                )
            with pc3:
                pay_freq_label = st.selectbox(
                    "Pay frequency", list(PAY_FREQUENCIES.keys()), key=f"freq_{name}",
                )

            pay_periods  = PAY_FREQUENCIES[pay_freq_label]
            checks_done  = paychecks_completed_this_year(pay_periods)
            checks_left  = paychecks_remaining_this_year(pay_periods)

            annual_employee_payroll = gross * employee_pct / 100
            annual_employer_match   = gross * employer_pct / 100
            annual_payroll_rrsp     = annual_employee_payroll + annual_employer_match
            per_cheque_employee     = annual_employee_payroll / pay_periods if pay_periods else 0.0
            per_cheque_employer     = annual_employer_match   / pay_periods if pay_periods else 0.0
            per_cheque_total        = annual_payroll_rrsp     / pay_periods if pay_periods else 0.0
            payroll_remaining       = per_cheque_total * checks_left

            if annual_payroll_rrsp > 0:
                pr1, pr2, pr3, pr4 = st.columns(4)
                with pr1:
                    st.metric("Per cheque (you)",      f"${per_cheque_employee:,.2f}",
                              help=f"${annual_employee_payroll:,.2f}/year")
                with pr2:
                    st.metric("Per cheque (employer)", f"${per_cheque_employer:,.2f}",
                              help=f"${annual_employer_match:,.2f}/year")
                with pr3:
                    st.metric(f"Cheques left in {current_year}", str(checks_left),
                              help=f"{checks_done} of {pay_periods} completed.")
                with pr4:
                    st.metric("Payroll RRSP remaining", f"${payroll_remaining:,.2f}",
                              help=f"Full-year total: ${annual_payroll_rrsp:,.2f}")
                if employer_pct > 0:
                    st.success(
                        f"💰 Employer match adds **${annual_employer_match:,.2f}/year** "
                        f"(${per_cheque_employer:,.2f}/cheque) to your RRSP — and it reduces your taxable income."
                    )
            else:
                st.caption("Enter a contribution % above to see your payroll RRSP breakdown.")

            st.caption(
                "ℹ️ If payroll RRSP is already logged in the Contributions page, "
                "today's RRSP room already reflects it — enter only remaining-year payroll % to avoid double-counting."
            )

        # ── Full-year RRSP room ────────────────────────────────────────────
        st.markdown("#### RRSP Room — Full Year Picture")
        rrsp_after_payroll = max(0.0, d["rrsp_remaining"] - payroll_remaining)
        payroll_over_room  = max(0.0, payroll_remaining - d["rrsp_remaining"])

        ry1, ry2, ry3, ry4 = st.columns(4)
        with ry1:
            st.metric("NOA Room",          f"${d['rrsp_noa']:,.2f}")
        with ry2:
            st.metric("Used so far",       f"${d['rrsp_noa'] - d['rrsp_remaining']:,.2f}")
        with ry3:
            st.metric("Reserved: payroll", f"${payroll_remaining:,.2f}")
        with ry4:
            st.metric("Available: lump-sum", f"${rrsp_after_payroll:,.2f}")

        if payroll_over_room > 0:
            st.error(
                f"⚠️ Projected payroll RRSP exceeds remaining room by **${payroll_over_room:,.2f}**. "
                f"Consider reducing your payroll % or check your NOA room in ⚙️ Settings."
            )

        # ── Lump-sum budget ────────────────────────────────────────────────
        st.markdown("#### Lump-Sum Budget")
        max_lumpsum    = d["tfsa_remaining"] + min(d["fhsa_remaining"], FHSA_YEAR_MAX) + rrsp_after_payroll
        default_budget = min(5_000.0, max_lumpsum) if max_lumpsum > 0 else 0.0
        budget = st.number_input(
            "Additional lump-sum contributions this year (CA$) — beyond payroll RRSP",
            min_value=0.0, step=500.0, format="%.2f", value=default_budget,
            key=f"budget_{name}",
        )

        # ── Room summary ──────────────────────────────────────────────────
        rm1, rm2, rm3 = st.columns(3)
        with rm1:
            st.metric("TFSA Room Remaining",  f"${d['tfsa_remaining']:,.2f}")
        with rm2:
            if d["fhsa_is_open"]:
                st.metric("FHSA Room (this year)", f"${min(d['fhsa_remaining'], FHSA_YEAR_MAX):,.2f}",
                          help=f"Capped at ${FHSA_YEAR_MAX:,} max per year.")
            else:
                st.metric("FHSA Room (this year)", "Not open yet",
                          help=f"FHSA opens {d['fhsa_open']} per ⚙️ Settings.")
        with rm3:
            lbl = f"${rrsp_after_payroll:,.2f}" if d["rrsp_noa"] > 0 else "—"
            st.metric("RRSP Lump-Sum Available", lbl)

        # ── Optimize ──────────────────────────────────────────────────────
        rec = {"FHSA": 0.0, "RRSP": 0.0, "TFSA": 0.0}
        if budget > 0:
            rec, base_tax, new_tax, savings, marginal = optimize_contributions(
                gross,
                rrsp_lumpsum_room = rrsp_after_payroll,
                tfsa_room         = d["tfsa_remaining"],
                fhsa_room         = min(d["fhsa_remaining"], FHSA_YEAR_MAX),
                fhsa_is_open      = d["fhsa_is_open"],
                budget            = budget,
                annual_payroll_rrsp = annual_payroll_rrsp,
            )
            total_rec   = sum(rec.values())
            unallocated = max(0.0, budget - total_rec)

            st.markdown("#### Recommended Lump-Sum Split")

            def pct_label(amt):
                return f"{amt / total_rec * 100:.0f}% of lump-sum" if total_rec > 0 and amt > 0 else None

            r1, r2, r3 = st.columns(3)
            with r1:
                st.metric("🏠 FHSA", f"${rec['FHSA']:,.2f}", pct_label(rec["FHSA"]))
                if not d["fhsa_is_open"]:
                    st.caption("Open an FHSA to access this account.")
            with r2:
                st.metric("📋 RRSP (lump-sum)", f"${rec['RRSP']:,.2f}", pct_label(rec["RRSP"]))
            with r3:
                st.metric("🏦 TFSA", f"${rec['TFSA']:,.2f}", pct_label(rec["TFSA"]))

            if annual_payroll_rrsp > 0:
                st.info(
                    f"**Full-year RRSP:** payroll ${annual_payroll_rrsp:,.2f} + "
                    f"lump-sum ${rec['RRSP']:,.2f} = "
                    f"**${annual_payroll_rrsp + rec['RRSP']:,.2f} total RRSP deduction**"
                )
            if unallocated > 0.01:
                st.warning(
                    f"${unallocated:,.2f} of budget exceeds registered account room. "
                    "Consider a non-registered account for the remainder."
                )

            st.markdown("#### Tax Impact")
            t1, t2, t3, t4 = st.columns(4)
            with t1:
                st.metric("Combined Marginal Rate", f"{marginal * 100:.1f}%")
            with t2:
                st.metric("Tax (no deductions)", f"${base_tax:,.2f}")
            with t3:
                st.metric("Tax (with contributions)", f"${new_tax:,.2f}",
                          delta=f"-${savings:,.2f}", delta_color="inverse")
            with t4:
                st.metric("💰 Est. Tax Savings", f"${savings:,.2f}",
                          help="From RRSP + FHSA deductions — payroll + lump-sum combined.")

            if checks_left > 0 and total_rec > 0:
                st.caption(
                    f"💡 To spread lump-sum evenly: **${total_rec / checks_left:,.2f}/cheque** "
                    f"over {checks_left} remaining paycheques."
                )

            # Stacked bar
            if annual_payroll_rrsp > 0 or total_rec > 0:
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    name="Payroll (auto)", x=["FHSA", "RRSP", "TFSA"],
                    y=[0, annual_payroll_rrsp, 0], marker_color="#90CAF9",
                    text=[f"${v:,.0f}" if v > 0 else "" for v in [0, annual_payroll_rrsp, 0]],
                    textposition="inside",
                ))
                fig.add_trace(go.Bar(
                    name="Lump-sum (recommended)", x=["FHSA", "RRSP", "TFSA"],
                    y=[rec["FHSA"], rec["RRSP"], rec["TFSA"]],
                    marker_color=["#2196F3", "#FF9800", "#4CAF50"],
                    text=[f"${v:,.0f}" if v > 0 else "" for v in [rec["FHSA"], rec["RRSP"], rec["TFSA"]]],
                    textposition="outside",
                ))
                fig.update_layout(
                    barmode="stack", title="Full-year contribution plan",
                    yaxis=dict(title="Amount (CA$)", tickformat="$,.0f"),
                    height=300, margin=dict(l=20, r=20, t=40, b=20),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                )
                st.plotly_chart(fig, use_container_width=True)

            with st.expander("ℹ️ Why this recommendation?"):
                if employer_pct > 0:
                    st.markdown(
                        f"**Capture the employer match** — your employer adds "
                        f"**{employer_pct:.1f}% (${per_cheque_employer:,.2f}/cheque)** to your RRSP automatically. "
                        f"Always contribute enough yourself to receive the full match."
                    )
                if d["fhsa_is_open"] and rec["FHSA"] > 0:
                    st.markdown(
                        "**FHSA next** — deductible like RRSP *and* tax-free like TFSA. "
                        "Best-of-both-worlds if used for a qualifying home purchase."
                    )
                if marginal >= RRSP_THRESHOLD:
                    st.markdown(
                        f"**RRSP before TFSA** — combined marginal rate is **{marginal*100:.1f}%**. "
                        f"Each $1,000 of RRSP saves ~${marginal*1000:,.0f} in taxes today."
                    )
                else:
                    st.markdown(
                        f"**TFSA before RRSP** — combined marginal rate is **{marginal*100:.1f}%** "
                        f"(below ~31% crossover). TFSA's tax-free growth is generally the better "
                        f"long-term choice at this income level."
                    )
                st.caption(
                    "⚠️ Estimates use 2025 Ontario brackets, exclude CPP/EI credits and other "
                    "deductions. Confirm with a tax professional."
                )
        else:
            st.info("Enter a lump-sum budget above $0 to see contribution recommendations.")

    # ════════════════════════════════════════════════════════════════════════
    # TAB 2: TAX REFUND ESTIMATOR
    # ════════════════════════════════════════════════════════════════════════
    with tab_refund:
        st.caption(
            f"Estimate {name}'s {current_year} federal + Ontario tax, then compare against "
            f"what's being withheld to see your refund or balance owing."
        )

        # Pull gross and pay settings from the optimizer tab via session_state
        # (Streamlit widgets share state by key, so we re-read from keys)
        _gross_key   = f"gross_{name}"
        _freq_key    = f"freq_{name}"
        _gross_ref   = st.session_state.get(_gross_key, 70_000.0)
        _freq_ref    = st.session_state.get(_freq_key, "Bi-weekly (26×/yr)")
        _pp_ref      = PAY_FREQUENCIES.get(_freq_ref, 26)

        st.markdown("---")
        st.markdown("### 📄 Pay Stub Deductions")
        st.caption(
            f"Enter your **per-paycheque** amounts (annualized × {_pp_ref} = full-year total shown below)."
        )

        stub1, stub2, stub3 = st.columns(3)
        with stub1:
            tax_pp = st.number_input(
                "Income tax withheld ($/cheque)",
                min_value=0.0, step=10.0, format="%.2f", value=0.0,
                key=f"tax_pp_{name}",
                help="The 'Federal + Provincial income tax' line on your pay stub.",
            )
        with stub2:
            _cpp_default = round(estimate_cpp_annual(_gross_ref) / _pp_ref, 2)
            cpp_pp = st.number_input(
                "CPP deducted ($/cheque)",
                min_value=0.0, step=5.0, format="%.2f", value=_cpp_default,
                key=f"cpp_pp_{name}",
                help=f"Auto-estimated from your gross income. Override with your actual stub amount.",
            )
        with stub3:
            _ei_default = round(estimate_ei_annual(_gross_ref) / _pp_ref, 2)
            ei_pp = st.number_input(
                "EI premiums ($/cheque)",
                min_value=0.0, step=2.0, format="%.2f", value=_ei_default,
                key=f"ei_pp_{name}",
                help="Auto-estimated from your gross income. Override with your actual stub amount.",
            )

        tax_annual = tax_pp * _pp_ref
        cpp_annual = cpp_pp * _pp_ref
        ei_annual  = ei_pp  * _pp_ref

        st.caption(
            f"Annualized: income tax withheld **${tax_annual:,.2f}** · "
            f"CPP **${cpp_annual:,.2f}** · EI **${ei_annual:,.2f}**"
        )

        st.markdown("---")
        st.markdown("### ✂️ Deductions from Income")
        st.caption("These reduce your taxable income before the tax rates are applied.")

        # Pull RRSP / FHSA from optimizer recommendations (if computed)
        _rec_rrsp = rec.get("RRSP", 0.0) + (annual_payroll_rrsp if "annual_payroll_rrsp" in dir() else 0.0)
        # annual_payroll_rrsp is defined in the optimizer tab block above (same loop iteration)
        _rec_rrsp = rec.get("RRSP", 0.0) + annual_payroll_rrsp
        _rec_fhsa = rec.get("FHSA", 0.0)

        ded1, ded2 = st.columns(2)
        with ded1:
            rrsp_total_input = st.number_input(
                "Total RRSP contributions this year (CA$)",
                min_value=0.0, step=100.0, format="%.2f",
                value=round(_rec_rrsp, 2),
                key=f"rrsp_total_{name}",
                help="Payroll RRSP + any lump-sum. Pre-filled from optimizer above.",
            )
            fhsa_total_input = st.number_input(
                "Total FHSA contributions this year (CA$)",
                min_value=0.0, step=100.0, format="%.2f",
                value=round(_rec_fhsa, 2),
                key=f"fhsa_total_{name}",
                help="Pre-filled from optimizer above.",
            )
        with ded2:
            union_dues = st.number_input(
                "Union / professional dues (annual)",
                min_value=0.0, step=50.0, format="%.2f", value=0.0,
                key=f"union_{name}",
                help="Deductible union or professional association dues paid in the year.",
            )
            childcare = st.number_input(
                "Childcare expenses (annual)",
                min_value=0.0, step=100.0, format="%.2f", value=0.0,
                key=f"childcare_{name}",
                help="Eligible childcare costs. Generally claimed by the lower-income earner.",
            )
            moving = st.number_input(
                "Moving expenses (annual)",
                min_value=0.0, step=100.0, format="%.2f", value=0.0,
                key=f"moving_{name}",
                help="Eligible if you moved ≥40 km closer to a new job or school.",
            )

        st.markdown("---")
        st.markdown("### 🏷️ Additional Tax Credits")
        st.caption("Applied directly against your tax owing — more valuable than deductions at high incomes.")

        cr1, cr2 = st.columns(2)
        with cr1:
            charitable = st.number_input(
                "Charitable donations (annual)",
                min_value=0.0, step=50.0, format="%.2f", value=0.0,
                key=f"char_{name}",
                help="First $200: 15% federal + 5.05% Ontario credit. Above $200: 29% + 11.16%.",
            )
            medical = st.number_input(
                "Eligible medical expenses (annual)",
                min_value=0.0, step=100.0, format="%.2f", value=0.0,
                key=f"med_{name}",
                help=f"Only the amount above 3% of net income (or ${FED_MED_THRESHOLD:,}, whichever is less) qualifies.",
            )
            pension_income = st.number_input(
                "Eligible pension income (annual)",
                min_value=0.0, step=100.0, format="%.2f", value=0.0,
                key=f"pension_{name}",
                help=f"Qualifies for up to ${FED_PENSION_AMT:,} federal / ${ON_PENSION_AMT:,} Ontario pension income credit.",
            )
        with cr2:
            disability  = st.checkbox(
                "Disability tax credit (DTC)",
                key=f"dis_{name}",
                help=f"${FED_DISABILITY_AMT:,} federal amount (× 15%) + ${ON_DISABILITY_AMT:,} Ontario amount (× 5.05%). Must be CRA-approved.",
            )
            first_time_buyer = st.checkbox(
                "First-time home buyer credit",
                key=f"fthb_{name}",
                help="One-time $1,500 federal credit (15% × $10,000) for first-time home buyers.",
            )
            age_65_plus = d["age"] >= 65
            if age_65_plus:
                st.info(f"🎂 Age {d['age']} — age amount credit applied automatically.")

        # ── Calculate ─────────────────────────────────────────────────────
        bd = calc_tax_full(
            gross          = _gross_ref,
            rrsp_ded       = rrsp_total_input,
            fhsa_ded       = fhsa_total_input,
            union_dues     = union_dues,
            childcare      = childcare,
            moving         = moving,
            cpp_paid       = cpp_annual,
            ei_paid        = ei_annual,
            charitable     = charitable,
            medical        = medical,
            pension_income = pension_income,
            disability     = disability,
            age_65_plus    = age_65_plus,
            first_time_buyer = first_time_buyer,
        )

        total_withheld = tax_annual   # income tax withheld (not CPP/EI)
        refund_amount  = total_withheld - bd["total_tax"]

        st.markdown("---")
        st.markdown("### 📊 Estimated Result")

        res1, res2, res3 = st.columns(3)
        with res1:
            st.metric("Total Income Tax Withheld", f"${total_withheld:,.2f}",
                      help="Annualized income tax deducted from your pay stubs.")
        with res2:
            st.metric("Estimated Tax Owing",       f"${bd['total_tax']:,.2f}",
                      help="Federal + Ontario tax after all deductions and credits.")
        with res3:
            if refund_amount >= 0:
                st.metric("🟢 Estimated Refund",   f"${refund_amount:,.2f}",
                          help="CRA will refund this after you file.")
            else:
                st.metric("🔴 Estimated Balance Owing", f"${abs(refund_amount):,.2f}",
                          delta=f"-${abs(refund_amount):,.2f}", delta_color="inverse",
                          help="You'll owe this when you file. Consider increasing withholding via TD1.")

        # ── Detailed breakdown table ───────────────────────────────────────
        with st.expander("📋 Full tax breakdown", expanded=True):

            def _row(label, amount, note="", indent=False):
                prefix = "&nbsp;&nbsp;&nbsp;&nbsp;" if indent else ""
                sign   = "-" if amount < 0 else ("+" if amount > 0 else "")
                return {
                    "": f"{prefix}{label}",
                    "Amount": f"${abs(amount):>10,.2f}" if amount != 0 else "—",
                    "Note": note,
                }

            rows = []

            # Income & deductions
            rows.append({"": "**Employment Income**", "Amount": f"${_gross_ref:,.2f}", "Note": ""})
            if rrsp_total_input:
                rows.append({"": "  Less: RRSP contributions", "Amount": f"-${rrsp_total_input:,.2f}", "Note": "deductible"})
            if fhsa_total_input:
                rows.append({"": "  Less: FHSA contributions", "Amount": f"-${fhsa_total_input:,.2f}", "Note": "deductible"})
            if union_dues:
                rows.append({"": "  Less: Union/professional dues", "Amount": f"-${union_dues:,.2f}", "Note": "deductible"})
            if childcare:
                rows.append({"": "  Less: Childcare expenses", "Amount": f"-${childcare:,.2f}", "Note": "deductible"})
            if moving:
                rows.append({"": "  Less: Moving expenses", "Amount": f"-${moving:,.2f}", "Note": "deductible"})
            rows.append({"": "**Net Income**", "Amount": f"${bd['net_income']:,.2f}", "Note": ""})
            rows.append({"": "", "Amount": "", "Note": ""})

            # Federal
            rows.append({"": "**Federal Tax**", "Amount": "", "Note": ""})
            rows.append({"": "  Tax on net income", "Amount": f"${bd['fed_gross_tax']:,.2f}", "Note": ""})
            rows.append({"": "  Less: Basic personal amount", "Amount": f"-${bd['fed_bpa_cr']:,.2f}", "Note": f"${FED_BPA:,} × 15%"})
            if bd["fed_cpp_cr"]:
                rows.append({"": "  Less: CPP credit", "Amount": f"-${bd['fed_cpp_cr']:,.2f}", "Note": f"${cpp_annual:,.2f} × 15%"})
            if bd["fed_ei_cr"]:
                rows.append({"": "  Less: EI credit", "Amount": f"-${bd['fed_ei_cr']:,.2f}", "Note": f"${ei_annual:,.2f} × 15%"})
            if bd["fed_emp_cr"]:
                rows.append({"": "  Less: Canada Employment Amount", "Amount": f"-${bd['fed_emp_cr']:,.2f}", "Note": f"${FED_EMPLOYMENT_AMT:,} × 15%"})
            if bd["age_amt_fed"]:
                rows.append({"": "  Less: Age amount (65+)", "Amount": f"-${bd['age_amt_fed']:,.2f}", "Note": "income-tested"})
            if bd["pension_cr_fed"]:
                rows.append({"": "  Less: Pension income credit", "Amount": f"-${bd['pension_cr_fed']:,.2f}", "Note": ""})
            if bd["fed_dis_cr"]:
                rows.append({"": "  Less: Disability amount", "Amount": f"-${bd['fed_dis_cr']:,.2f}", "Note": ""})
            if bd["fed_char_cr"]:
                rows.append({"": "  Less: Charitable donations credit", "Amount": f"-${bd['fed_char_cr']:,.2f}", "Note": ""})
            if bd["fed_med_cr"]:
                rows.append({"": "  Less: Medical expenses credit", "Amount": f"-${bd['fed_med_cr']:,.2f}", "Note": f"above ${min(_gross_ref * 0.03, FED_MED_THRESHOLD):,.0f} floor"})
            if bd["fthb_cr"]:
                rows.append({"": "  Less: First-time home buyer", "Amount": f"-${bd['fthb_cr']:,.2f}", "Note": "$10,000 × 15%"})
            rows.append({"": "**Federal Tax Owing**", "Amount": f"${bd['fed_tax']:,.2f}", "Note": ""})
            rows.append({"": "", "Amount": "", "Note": ""})

            # Ontario
            rows.append({"": "**Ontario Tax**", "Amount": "", "Note": ""})
            rows.append({"": "  Tax on net income", "Amount": f"${bd['on_gross_tax']:,.2f}", "Note": ""})
            rows.append({"": "  Less: Basic personal amount", "Amount": f"-${bd['on_bpa_cr']:,.2f}", "Note": f"${ON_BPA:,} × 5.05%"})
            if bd["on_cpp_cr"]:
                rows.append({"": "  Less: CPP credit", "Amount": f"-${bd['on_cpp_cr']:,.2f}", "Note": ""})
            if bd["on_ei_cr"]:
                rows.append({"": "  Less: EI credit", "Amount": f"-${bd['on_ei_cr']:,.2f}", "Note": ""})
            if bd["age_amt_on"]:
                rows.append({"": "  Less: Age amount (65+)", "Amount": f"-${bd['age_amt_on']:,.2f}", "Note": "income-tested"})
            if bd["pension_cr_on"]:
                rows.append({"": "  Less: Pension income credit", "Amount": f"-${bd['pension_cr_on']:,.2f}", "Note": ""})
            if bd["on_dis_cr"]:
                rows.append({"": "  Less: Disability amount", "Amount": f"-${bd['on_dis_cr']:,.2f}", "Note": ""})
            if bd["on_char_cr"]:
                rows.append({"": "  Less: Charitable donations credit", "Amount": f"-${bd['on_char_cr']:,.2f}", "Note": ""})
            if bd["on_med_cr"]:
                rows.append({"": "  Less: Medical expenses credit", "Amount": f"-${bd['on_med_cr']:,.2f}", "Note": ""})
            if bd["on_surtax"]:
                rows.append({"": "  + Ontario surtax", "Amount": f"+${bd['on_surtax']:,.2f}", "Note": "20%/56% on high ON tax"})
            rows.append({"": "**Ontario Tax Owing**", "Amount": f"${bd['on_tax']:,.2f}", "Note": ""})
            rows.append({"": "", "Amount": "", "Note": ""})

            # Summary
            rows.append({"": "**Total Tax Owing**",    "Amount": f"${bd['total_tax']:,.2f}", "Note": "federal + Ontario"})
            rows.append({"": "Income Tax Withheld",     "Amount": f"${total_withheld:,.2f}", "Note": "from pay stubs (annualized)"})
            if refund_amount >= 0:
                rows.append({"": "**🟢 Estimated Refund**", "Amount": f"${refund_amount:,.2f}", "Note": "file your return to claim"})
            else:
                rows.append({"": "**🔴 Balance Owing**",    "Amount": f"${abs(refund_amount):,.2f}", "Note": "due April 30"})

            import pandas as pd
            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True,
            )

        # ── Waterfall chart ────────────────────────────────────────────────
        with st.expander("📊 Tax waterfall chart"):
            wf_labels  = ["Gross Income", "RRSP/FHSA/Other\nDeductions", "Net Income",
                          "Federal Tax", "Ontario Tax", "Non-Ref.\nCredits", "Tax Owing",
                          "Tax Withheld", "Refund / Owing"]
            wf_values  = [
                _gross_ref,
                -bd["income_deds"],
                bd["net_income"],
                -(bd["fed_gross_tax"]),
                -(bd["on_gross_tax"]),
                bd["fed_credits"] + bd["on_credits"],
                bd["total_tax"],
                total_withheld,
                refund_amount,
            ]
            wf_colors  = [
                "#4CAF50", "#F44336", "#4CAF50",
                "#F44336", "#F44336", "#4CAF50",
                "#FF9800",
                "#2196F3",
                "#4CAF50" if refund_amount >= 0 else "#F44336",
            ]
            wf_fig = go.Figure(go.Bar(
                x=wf_labels, y=[abs(v) for v in wf_values],
                marker_color=wf_colors,
                text=[f"${abs(v):,.0f}" for v in wf_values],
                textposition="outside",
            ))
            wf_fig.update_layout(
                title="Tax calculation waterfall",
                yaxis=dict(title="Amount (CA$)", tickformat="$,.0f"),
                height=380, margin=dict(l=20, r=20, t=40, b=20),
            )
            st.plotly_chart(wf_fig, use_container_width=True)

        st.caption(
            "⚠️ Approximate 2025 Ontario rates. Excludes: CPP/EI overpayment refunds, "
            "Ontario Trillium Benefit (OEPTC/OSTC), home accessibility credit, "
            "tuition carryforward, spousal / family credits, and other less-common credits. "
            "Always verify with CRA My Account before filing."
        )

    st.divider()
