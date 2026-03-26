"""
Contribution Optimizer — tax-efficient RRSP / TFSA / FHSA split for Ontario residents.
Uses 2025 federal + Ontario tax brackets. Reads remaining room from your logged data.
Accounts for payroll RRSP (employee + employer match) and projects the full calendar year.
"""
import streamlit as st
import plotly.graph_objects as go
from datetime import date
import math

from utils.sheets import get_contributions, get_withdrawals, get_settings
from utils.calculations import (
    tfsa_remaining_room,
    fhsa_remaining_room,
    rrsp_remaining_room,
)
from utils.constants import FHSA_ANNUAL_LIMIT

# ─── 2025 Federal + Ontario tax constants ─────────────────────────────────────

FED_BPA      = 16_129.0
FED_BRACKETS = [
    (57_375,       0.15),
    (114_750,      0.205),
    (158_519,      0.26),
    (220_000,      0.29),
    (float("inf"), 0.33),
]

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

RRSP_THRESHOLD   = 0.31   # combined marginal above which RRSP beats TFSA
FHSA_YEAR_MAX    = FHSA_ANNUAL_LIMIT * 2   # max FHSA in a single year ($16k)
PAY_FREQUENCIES  = {
    "Bi-weekly (26×/yr)":   26,
    "Semi-monthly (24×/yr)": 24,
    "Weekly (52×/yr)":      52,
    "Monthly (12×/yr)":     12,
}


# ─── Tax math helpers ─────────────────────────────────────────────────────────

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
    """
    Estimate Ontario + Federal income tax for a single Ontario filer.
    Returns (fed_tax, on_tax, total_tax, combined_marginal_rate).
    Does not account for CPP/EI credits or other deductions.
    """
    taxable   = max(0.0, gross - rrsp_ded - fhsa_ded)
    fed_basic = _bracket_tax(taxable, FED_BRACKETS)
    fed_tax   = max(0.0, fed_basic - FED_BPA * 0.15)
    on_basic  = _bracket_tax(taxable, ON_BRACKETS)
    on_net    = max(0.0, on_basic - ON_BPA * 0.0505)
    on_tax    = on_net + _ontario_surtax(on_net)
    fed_m     = _marginal_rate(taxable, FED_BRACKETS)
    on_m      = _marginal_rate(taxable, ON_BRACKETS)
    if on_net > ON_SURTAX_T2:
        on_m *= 1.56
    elif on_net > ON_SURTAX_T1:
        on_m *= 1.20
    return fed_tax, on_tax, fed_tax + on_tax, round(fed_m + on_m, 4)


# ─── Paycheque helpers ────────────────────────────────────────────────────────

def paychecks_remaining_this_year(pay_periods: int) -> int:
    """Estimate how many more pay periods remain in the current calendar year."""
    today      = date.today()
    year_start = date(today.year, 1, 1)
    year_end   = date(today.year, 12, 31)
    days_total     = (year_end - year_start).days + 1
    days_elapsed   = (today - year_start).days
    completed      = round(days_elapsed / days_total * pay_periods)
    return max(0, pay_periods - completed)


def paychecks_completed_this_year(pay_periods: int) -> int:
    return pay_periods - paychecks_remaining_this_year(pay_periods)


# ─── Contribution optimizer ───────────────────────────────────────────────────

def optimize_contributions(
    gross: float,
    rrsp_lumpsum_room: float,     # room remaining AFTER projected payroll RRSP
    tfsa_room: float,
    fhsa_room: float,
    fhsa_is_open: bool,
    budget: float,
    annual_payroll_rrsp: float = 0.0,  # total employee + employer for full year
):
    """
    Returns (rec, baseline_tax, new_tax, tax_savings, marginal_rate).
    Priority: FHSA → RRSP (high marginal) or TFSA (low marginal) → remainder.
    Tax calculation includes the full annual payroll RRSP deduction.
    """
    _, _, baseline_tax, marginal = calc_tax(gross)
    rec       = {"FHSA": 0.0, "RRSP": 0.0, "TFSA": 0.0}
    remaining = budget

    # Step 1: FHSA
    if fhsa_is_open and fhsa_room > 0 and remaining > 0:
        amt         = min(fhsa_room, FHSA_YEAR_MAX, remaining)
        rec["FHSA"] = amt
        remaining  -= amt

    # Step 2: RRSP vs TFSA based on marginal rate
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

    # Tax savings include the full-year payroll RRSP deduction (employee + employer)
    # Employer match shows up on the T4 as income but is offset by the RRSP deduction
    total_rrsp_ded = rec["RRSP"] + annual_payroll_rrsp
    _, _, new_tax, _ = calc_tax(gross, rrsp_ded=total_rrsp_ded, fhsa_ded=rec["FHSA"])
    savings = baseline_tax - new_tax

    return rec, baseline_tax, new_tax, savings, marginal


# ─── Page setup ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="Contribution Optimizer", page_icon="💡", layout="wide")
st.title("💡 Contribution Optimizer")
st.caption(
    "Get a tax-efficient RRSP / TFSA / FHSA contribution plan for the full calendar year, "
    "accounting for payroll RRSP deductions and employer matching. "
    "Based on **Ontario 2025** federal + provincial tax rates."
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

# ─── Pre-compute remaining room per person ────────────────────────────────────

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
    d["fhsa_remaining"]  = fhsa_remaining_room(
        d["fhsa_open"], contributions, d["fhsa_prior"], person=name
    )
    d["rrsp_remaining"]  = rrsp_remaining_room(d["rrsp_noa"], contributions, person=name)
    d["fhsa_is_open"]    = d["fhsa_open"] <= current_year
    # How much RRSP has been used so far this year (logged in app)
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

    # ── Income input ──────────────────────────────────────────────────────────
    gross = st.number_input(
        "Expected gross employment income this year (CA$)",
        min_value=0.0, step=1_000.0, format="%.2f",
        value=70_000.0,
        key=f"gross_{name}",
        help=(
            "Your total T4 employment income before any deductions. "
            "If your employer's RRSP match is reported as a taxable benefit on your T4, "
            "include it here."
        ),
    )

    # ── Payroll RRSP section ──────────────────────────────────────────────────
    with st.expander("💼 Payroll RRSP / Group RRSP", expanded=True):
        pc1, pc2, pc3 = st.columns(3)
        with pc1:
            employee_pct = st.number_input(
                "Your contribution (% of gross)",
                min_value=0.0, max_value=25.0, step=0.5, format="%.1f",
                value=0.0, key=f"emp_pct_{name}",
                help="The % of each paycheque that goes into your group RRSP automatically.",
            )
        with pc2:
            employer_pct = st.number_input(
                "Employer match (% of gross)",
                min_value=0.0, max_value=25.0, step=0.5, format="%.1f",
                value=0.0, key=f"er_pct_{name}",
                help=(
                    "Your employer's RRSP contribution as a % of your gross pay. "
                    "Counts against your contribution room just like your own contributions."
                ),
            )
        with pc3:
            pay_freq_label = st.selectbox(
                "Pay frequency", list(PAY_FREQUENCIES.keys()),
                key=f"freq_{name}",
            )

        pay_periods   = PAY_FREQUENCIES[pay_freq_label]
        checks_done   = paychecks_completed_this_year(pay_periods)
        checks_left   = paychecks_remaining_this_year(pay_periods)

        # Annual payroll RRSP amounts (full year)
        annual_employee_payroll = gross * employee_pct / 100
        annual_employer_match   = gross * employer_pct / 100
        annual_payroll_rrsp     = annual_employee_payroll + annual_employer_match

        # Per-cheque amounts
        per_cheque_employee = annual_employee_payroll / pay_periods if pay_periods else 0.0
        per_cheque_employer = annual_employer_match   / pay_periods if pay_periods else 0.0
        per_cheque_total    = annual_payroll_rrsp     / pay_periods if pay_periods else 0.0

        # Remaining vs completed payroll RRSP for this year
        payroll_ytd        = per_cheque_total * checks_done
        payroll_remaining  = per_cheque_total * checks_left

        if annual_payroll_rrsp > 0:
            pr1, pr2, pr3, pr4 = st.columns(4)
            with pr1:
                st.metric(
                    "Per paycheque (you)",
                    f"${per_cheque_employee:,.2f}",
                    help=f"${annual_employee_payroll:,.2f} / year",
                )
            with pr2:
                st.metric(
                    "Per paycheque (employer)",
                    f"${per_cheque_employer:,.2f}",
                    help=f"${annual_employer_match:,.2f} / year",
                )
            with pr3:
                st.metric(
                    f"Paycheques left in {current_year}",
                    str(checks_left),
                    help=f"{checks_done} of {pay_periods} pay periods completed.",
                )
            with pr4:
                st.metric(
                    "Payroll RRSP remaining this year",
                    f"${payroll_remaining:,.2f}",
                    help=(
                        f"${per_cheque_total:,.2f} × {checks_left} remaining paycheques. "
                        f"Full-year total: ${annual_payroll_rrsp:,.2f}."
                    ),
                )

            if employer_pct > 0:
                st.success(
                    f"💰 Employer match: your employer is adding "
                    f"**${annual_employer_match:,.2f}/year** (${per_cheque_employer:,.2f}/cheque) "
                    f"to your RRSP — free money that also reduces your taxable income."
                )
        else:
            st.caption("Enter a contribution % above to see your payroll RRSP breakdown.")

        st.caption(
            "ℹ️ If your YTD payroll RRSP contributions are already logged in the "
            "Contributions page, today's RRSP room already accounts for them — "
            "enter only the **remaining** portion of the year's payroll % to avoid double-counting."
        )

    # ── Full-year RRSP room breakdown ─────────────────────────────────────────
    st.markdown("#### RRSP Room — Full Year Picture")
    ry1, ry2, ry3, ry4 = st.columns(4)

    rrsp_after_payroll = max(0.0, d["rrsp_remaining"] - payroll_remaining)
    payroll_over_room  = max(0.0, payroll_remaining - d["rrsp_remaining"])

    with ry1:
        st.metric(
            "NOA Room (this year)",
            f"${d['rrsp_noa']:,.2f}",
            help="Your RRSP room from your most recent Notice of Assessment. Update in ⚙️ Settings.",
        )
    with ry2:
        st.metric(
            "Used so far (logged)",
            f"${d['rrsp_noa'] - d['rrsp_remaining']:,.2f}",
            help="RRSP contributions already recorded in the app this year.",
        )
    with ry3:
        st.metric(
            "Reserved for payroll",
            f"${payroll_remaining:,.2f}",
            help=f"Projected payroll RRSP for the {checks_left} remaining paycheques.",
        )
    with ry4:
        st.metric(
            "Available for lump-sum",
            f"${rrsp_after_payroll:,.2f}",
            help="Room left over after projected payroll contributions — what you can add manually.",
        )

    if payroll_over_room > 0:
        st.error(
            f"⚠️ Your projected payroll RRSP (${payroll_remaining:,.2f}) exceeds your remaining "
            f"room by **${payroll_over_room:,.2f}**. Consider reducing your payroll contribution "
            f"% or reviewing your NOA room in ⚙️ Settings."
        )

    # ── Budget input (lump-sum only) ──────────────────────────────────────────
    st.markdown("#### Lump-Sum Budget")
    max_lumpsum = d["tfsa_remaining"] + min(d["fhsa_remaining"], FHSA_YEAR_MAX) + rrsp_after_payroll
    default_budget = min(5_000.0, max_lumpsum) if max_lumpsum > 0 else 0.0

    budget = st.number_input(
        f"Additional lump-sum contributions this year (CA$) — beyond payroll RRSP",
        min_value=0.0, step=500.0, format="%.2f",
        value=default_budget,
        key=f"budget_{name}",
        help=(
            "How much you plan to contribute manually on top of what payroll deducts. "
            "This can go into any combination of RRSP, TFSA, or FHSA."
        ),
    )

    # ── Current room summary ──────────────────────────────────────────────────
    rm1, rm2, rm3 = st.columns(3)
    with rm1:
        st.metric("TFSA Room Remaining",     f"${d['tfsa_remaining']:,.2f}")
    with rm2:
        if d["fhsa_is_open"]:
            yr_cap  = min(d["fhsa_remaining"], FHSA_YEAR_MAX)
            st.metric("FHSA Room (this year)", f"${yr_cap:,.2f}",
                      help=f"Capped at ${FHSA_YEAR_MAX:,} max per year.")
        else:
            st.metric("FHSA Room (this year)", "Not open yet",
                      help=f"FHSA open year is set to {d['fhsa_open']} in ⚙️ Settings.")
    with rm3:
        lbl = f"${rrsp_after_payroll:,.2f}" if d["rrsp_noa"] > 0 else "—"
        st.metric("RRSP Lump-Sum Available", lbl,
                  help="RRSP room remaining after projected payroll contributions.")

    if budget <= 0:
        st.info("Enter a lump-sum budget above $0 to see recommendations.")
        st.divider()
        continue

    # ── Optimize ──────────────────────────────────────────────────────────────
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

    # ── Recommended lump-sum split ────────────────────────────────────────────
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
        if d["rrsp_noa"] <= 0:
            st.caption("Add your NOA room in ⚙️ Settings.")
    with r3:
        st.metric("🏦 TFSA", f"${rec['TFSA']:,.2f}", pct_label(rec["TFSA"]))

    if annual_payroll_rrsp > 0:
        st.info(
            f"**Full-year RRSP picture:** "
            f"Payroll RRSP (employee + employer): **${annual_payroll_rrsp:,.2f}** + "
            f"Lump-sum RRSP: **${rec['RRSP']:,.2f}** = "
            f"Total RRSP deduction: **${annual_payroll_rrsp + rec['RRSP']:,.2f}**"
        )

    if unallocated > 0.01:
        st.warning(
            f"${unallocated:,.2f} of your lump-sum budget can't be placed in registered accounts "
            f"(you've hit the room limits). Consider a non-registered account for the remainder."
        )

    # ── Tax impact ────────────────────────────────────────────────────────────
    st.markdown("#### Tax Impact")
    t1, t2, t3, t4 = st.columns(4)
    with t1:
        st.metric(
            "Combined Marginal Rate", f"{marginal * 100:.1f}%",
            help="Federal + Ontario marginal rate at this income. The value of your next deduction.",
        )
    with t2:
        st.metric("Est. Tax (no deductions)", f"${base_tax:,.2f}")
    with t3:
        st.metric(
            "Est. Tax (with all contributions)", f"${new_tax:,.2f}",
            delta=f"-${savings:,.2f}", delta_color="inverse",
        )
    with t4:
        st.metric(
            "💰 Total Est. Tax Savings", f"${savings:,.2f}",
            help=(
                "Reduction from RRSP + FHSA deductions (payroll + lump-sum combined). "
                "TFSA has no immediate tax impact."
            ),
        )

    # Per-cheque lump-sum equivalent
    if checks_left > 0 and rec["RRSP"] + rec["FHSA"] + rec["TFSA"] > 0:
        per_chq_lumpsum = total_rec / checks_left
        st.caption(
            f"💡 To spread your lump-sum evenly across {checks_left} remaining paycheques, "
            f"contribute **${per_chq_lumpsum:,.2f}/cheque** in addition to your payroll deductions."
        )

    # ── Bar chart ─────────────────────────────────────────────────────────────
    if annual_payroll_rrsp > 0 or total_rec > 0:
        # Show payroll RRSP as a stacked bar alongside lump-sum
        accounts = ["FHSA", "RRSP", "TFSA"]
        lumpsum_vals  = [rec["FHSA"], rec["RRSP"], rec["TFSA"]]
        payroll_vals  = [0.0, annual_payroll_rrsp, 0.0]   # payroll only goes into RRSP

        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Payroll (auto)",
            x=accounts, y=payroll_vals,
            marker_color="#90CAF9",
            text=[f"${v:,.0f}" if v > 0 else "" for v in payroll_vals],
            textposition="inside",
        ))
        fig.add_trace(go.Bar(
            name="Lump-sum (recommended)",
            x=accounts, y=lumpsum_vals,
            marker_color=["#2196F3", "#FF9800", "#4CAF50"],
            text=[f"${v:,.0f}" if v > 0 else "" for v in lumpsum_vals],
            textposition="outside",
        ))
        fig.update_layout(
            barmode    = "stack",
            title      = "Full-year contribution plan",
            yaxis      = dict(title="Amount (CA$)", tickformat="$,.0f"),
            height     = 320,
            margin     = dict(l=20, r=20, t=40, b=20),
            legend     = dict(orientation="h", yanchor="bottom", y=1.02),
        )
        st.plotly_chart(fig, use_container_width=True)

    # ── Reasoning ─────────────────────────────────────────────────────────────
    with st.expander("ℹ️ Why this recommendation?"):
        if employer_pct > 0:
            st.markdown(
                f"**Capture the employer match first** — your employer contributes "
                f"**{employer_pct:.1f}% ({per_cheque_employer:,.2f}/cheque)** to your RRSP automatically. "
                f"This is effectively a {employer_pct:.1f}% raise that also reduces your taxable income. "
                f"Always make sure you're contributing at least {employee_pct:.1f}% yourself to receive the full match."
            )

        if d["fhsa_is_open"] and rec["FHSA"] > 0:
            st.markdown(
                "**FHSA next** — deductible like an RRSP *and* tax-free like a TFSA. "
                "If used for a qualifying home purchase it's the best-of-both-worlds account in Canada."
            )

        if marginal >= RRSP_THRESHOLD:
            st.markdown(
                f"**RRSP before TFSA** — your combined marginal rate is **{marginal*100:.1f}%**. "
                f"Every $1,000 in RRSP deductions saves ~${marginal*1000:,.0f} in taxes today. "
                f"At this rate the guaranteed upfront deduction is worth more than TFSA's tax-free growth."
            )
        else:
            st.markdown(
                f"**TFSA before RRSP** — your combined marginal rate is **{marginal*100:.1f}%** "
                f"(below the ~31% crossover). TFSA's tax-free growth is generally the better "
                f"long-term choice at this income level, especially if you expect similar or "
                f"higher income in retirement."
            )

        st.caption(
            "⚠️ Estimates use 2025 Ontario brackets and exclude CPP/EI credits, other deductions, "
            "and income splitting. The employer RRSP match is assumed to be included in your T4 "
            "gross income and offset by the RRSP deduction. Confirm details with a tax professional."
        )

    st.divider()

# ─── Coming soon: Payroll deduction / real-time refund calculator ─────────────

with st.expander("🔜 Coming soon: Real-time tax refund / owing calculator"):
    st.markdown("""
    A future update will let you enter your pay stub details to estimate your
    real-time refund or balance owing — updating live as you adjust contributions above.

    **Planned inputs per pay period:**
    - Gross pay
    - CPP deductions withheld
    - EI premiums withheld
    - Income tax withheld
    - Other deductions (benefits, insurance)
    - Net pay

    The calculator will annualize your payroll deductions, factor in RRSP and FHSA
    contributions, and show your estimated refund or balance owing for the full year.
    """)
