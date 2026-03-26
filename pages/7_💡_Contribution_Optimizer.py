"""
Contribution Optimizer — tax-efficient RRSP / TFSA / FHSA split for Ontario residents.
Uses 2025 federal + Ontario tax brackets. Reads remaining room from your logged data.
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

# ─── 2025 Federal + Ontario tax constants ─────────────────────────────────────

FED_BPA      = 16_129.0          # Federal basic personal amount (2025)
FED_BRACKETS = [                 # (upper bound inclusive, marginal rate)
    (57_375,       0.15),
    (114_750,      0.205),
    (158_519,      0.26),
    (220_000,      0.29),
    (float("inf"), 0.33),
]

ON_BPA       = 11_865.0          # Ontario basic personal amount (2025)
ON_BRACKETS  = [
    (51_446,       0.0505),
    (102_894,      0.0915),
    (150_000,      0.1116),
    (220_000,      0.1216),
    (float("inf"), 0.1316),
]
ON_SURTAX_T1 = 5_315.0   # Ontario surtax: 20% on ON tax above this
ON_SURTAX_T2 = 6_802.0   # Ontario surtax: additional 36% on ON tax above this (56% total)

# Combined marginal threshold at which RRSP beats TFSA.
# Ontario 2nd bracket (11.16%) + federal 2nd bracket (20.5%) ≈ 31.66%,
# so incomes above ~$103k swing clearly RRSP-first; below ~$57k are TFSA-first.
RRSP_THRESHOLD = 0.31

# Max FHSA contribution in any single calendar year: annual + 1yr carryforward
FHSA_YEAR_MAX = FHSA_ANNUAL_LIMIT * 2  # $16,000


# ─── Tax math helpers ─────────────────────────────────────────────────────────

def _bracket_tax(income: float, brackets: list) -> float:
    """Compute tax on `income` using a list of (upper_bound, rate) brackets."""
    tax, prev = 0.0, 0.0
    for upper, rate in brackets:
        if income <= prev:
            break
        tax += (min(income, upper) - prev) * rate
        prev = upper
    return tax


def _marginal_rate(income: float, brackets: list) -> float:
    """Return the marginal rate at a given income level."""
    for upper, rate in brackets:
        if income <= upper:
            return rate
    return brackets[-1][1]


def _ontario_surtax(on_net: float) -> float:
    """Ontario surtax applied on top of basic Ontario tax (after BPA credit)."""
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
    Does not account for CPP/EI credits, other deductions, or income splitting.
    """
    taxable = max(0.0, gross - rrsp_ded - fhsa_ded)

    # ── Federal ──────────────────────────────────────────────────────────────
    fed_basic  = _bracket_tax(taxable, FED_BRACKETS)
    fed_credit = FED_BPA * 0.15
    fed_tax    = max(0.0, fed_basic - fed_credit)

    # ── Ontario ──────────────────────────────────────────────────────────────
    on_basic   = _bracket_tax(taxable, ON_BRACKETS)
    on_credit  = ON_BPA * 0.0505
    on_net     = max(0.0, on_basic - on_credit)
    on_surtax  = _ontario_surtax(on_net)
    on_tax     = on_net + on_surtax

    # ── Combined marginal (adjust ON marginal for surtax multiplier) ─────────
    fed_m = _marginal_rate(taxable, FED_BRACKETS)
    on_m  = _marginal_rate(taxable, ON_BRACKETS)
    if on_net > ON_SURTAX_T2:
        on_m *= 1.56
    elif on_net > ON_SURTAX_T1:
        on_m *= 1.20

    return fed_tax, on_tax, fed_tax + on_tax, round(fed_m + on_m, 4)


# ─── Contribution optimizer ───────────────────────────────────────────────────

def optimize_contributions(
    gross: float,
    rrsp_room: float,
    tfsa_room: float,
    fhsa_room: float,
    fhsa_is_open: bool,
    budget: float,
):
    """
    Return (recommendations, baseline_tax, new_tax, tax_savings, marginal_rate).

    Priority order:
      1. FHSA  — deductible like RRSP *and* tax-free like TFSA; always fill first.
      2. RRSP  — if combined marginal ≥ 31% (high earner, deduction most valuable).
         TFSA  — if combined marginal < 31% (lower earner, tax-free growth wins).
      3. Remainder goes to whichever of RRSP/TFSA wasn't first.
    """
    _, _, baseline_tax, marginal = calc_tax(gross)
    rec       = {"FHSA": 0.0, "RRSP": 0.0, "TFSA": 0.0}
    remaining = budget

    # Step 1: FHSA
    if fhsa_is_open and fhsa_room > 0 and remaining > 0:
        amt        = min(fhsa_room, FHSA_YEAR_MAX, remaining)
        rec["FHSA"] = amt
        remaining  -= amt

    # Step 2: RRSP vs TFSA based on marginal rate
    if remaining > 0:
        if marginal >= RRSP_THRESHOLD:
            rrsp_amt    = min(rrsp_room, remaining)
            rec["RRSP"] = rrsp_amt
            remaining  -= rrsp_amt
            if remaining > 0:
                rec["TFSA"] = min(tfsa_room, remaining)
        else:
            tfsa_amt    = min(tfsa_room, remaining)
            rec["TFSA"] = tfsa_amt
            remaining  -= tfsa_amt
            if remaining > 0:
                rec["RRSP"] = min(rrsp_room, remaining)

    _, _, new_tax, _ = calc_tax(gross, rrsp_ded=rec["RRSP"], fhsa_ded=rec["FHSA"])
    savings = baseline_tax - new_tax

    return rec, baseline_tax, new_tax, savings, marginal


# ─── Page setup ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="Contribution Optimizer", page_icon="💡", layout="wide")
st.title("💡 Contribution Optimizer")
st.caption(
    "Enter your expected income and available contribution budget to get a "
    "tax-efficient RRSP / TFSA / FHSA split recommendation. "
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
    "Isaac": {
        "birth_year":   isaac_birth_year,
        "tfsa_eligible": isaac_tfsa_eligible,
        "fhsa_open":    isaac_fhsa_open,
        "rrsp_room":    rrsp_room_isaac,
        "tfsa_prior":   tfsa_prior_isaac,
        "tfsa_prior_w": tfsa_prior_w_isaac,
        "fhsa_prior":   fhsa_prior_isaac,
    },
    "Katherine": {
        "birth_year":   katherine_birth_year,
        "tfsa_eligible": katherine_tfsa_eligible,
        "fhsa_open":    katherine_fhsa_open,
        "rrsp_room":    rrsp_room_katherine,
        "tfsa_prior":   tfsa_prior_katherine,
        "tfsa_prior_w": tfsa_prior_w_katherine,
        "fhsa_prior":   fhsa_prior_katherine,
    },
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
    d["rrsp_remaining"]  = rrsp_remaining_room(
        d["rrsp_room"], contributions, person=name
    )
    d["fhsa_is_open"]    = d["fhsa_open"] <= current_year
    d["max_room"]        = d["tfsa_remaining"] + d["fhsa_remaining"] + d["rrsp_remaining"]

# ─── Per-person panels ────────────────────────────────────────────────────────

for name, d in people.items():
    st.subheader(f"👤 {name}")

    # ── Inputs ────────────────────────────────────────────────────────────────
    in1, in2 = st.columns(2)
    with in1:
        gross = st.number_input(
            "Expected gross employment income (CA$)",
            min_value=0.0, step=1_000.0, format="%.2f",
            value=70_000.0,
            key=f"gross_{name}",
            help="Your total income before any deductions — what your employer reports on your T4.",
        )
    with in2:
        default_budget = min(10_000.0, d["max_room"]) if d["max_room"] > 0 else 0.0
        budget = st.number_input(
            "Total you can contribute this year (CA$)",
            min_value=0.0, step=500.0, format="%.2f",
            value=default_budget,
            key=f"budget_{name}",
            help="How many dollars you have available to put into registered accounts this year.",
        )

    # ── Current room summary ──────────────────────────────────────────────────
    rm1, rm2, rm3 = st.columns(3)
    with rm1:
        st.metric("TFSA Room Remaining",  f"${d['tfsa_remaining']:,.2f}")
    with rm2:
        if d["fhsa_is_open"]:
            fhsa_disp = f"${min(d['fhsa_remaining'], FHSA_YEAR_MAX):,.2f}"
            fhsa_help = f"Capped at ${FHSA_YEAR_MAX:,} (annual limit + 1yr carryforward)"
        else:
            fhsa_disp = "Not open yet"
            fhsa_help = f"FHSA open year is set to {d['fhsa_open']} in ⚙️ Settings."
        st.metric("FHSA Room (this year)", fhsa_disp, help=fhsa_help)
    with rm3:
        if d["rrsp_remaining"] > 0:
            st.metric("RRSP Room Remaining",  f"${d['rrsp_remaining']:,.2f}")
        else:
            st.metric("RRSP Room Remaining", "—",
                      help="Add your NOA room in ⚙️ Settings to enable RRSP recommendations.")

    if budget <= 0:
        st.info("Enter a contribution budget above $0 to see recommendations.")
        st.divider()
        continue

    # ── Optimize ──────────────────────────────────────────────────────────────
    rec, base_tax, new_tax, savings, marginal = optimize_contributions(
        gross,
        rrsp_room   = d["rrsp_remaining"],
        tfsa_room   = d["tfsa_remaining"],
        fhsa_room   = min(d["fhsa_remaining"], FHSA_YEAR_MAX),
        fhsa_is_open= d["fhsa_is_open"],
        budget      = budget,
    )
    total_rec    = sum(rec.values())
    unallocated  = max(0.0, budget - total_rec)

    # ── Recommended split ─────────────────────────────────────────────────────
    st.markdown("#### Recommended Split")
    r1, r2, r3 = st.columns(3)

    def pct_label(amt):
        return f"{amt / total_rec * 100:.0f}% of budget" if total_rec > 0 and amt > 0 else None

    with r1:
        st.metric("🏠 FHSA", f"${rec['FHSA']:,.2f}", pct_label(rec["FHSA"]))
        if not d["fhsa_is_open"]:
            st.caption("Open an FHSA to access this account's unique tax benefits.")
    with r2:
        st.metric("📋 RRSP", f"${rec['RRSP']:,.2f}", pct_label(rec["RRSP"]))
        if d["rrsp_remaining"] <= 0 and gross > 0:
            st.caption("No RRSP room on file — add your NOA room in ⚙️ Settings.")
    with r3:
        st.metric("🏦 TFSA", f"${rec['TFSA']:,.2f}", pct_label(rec["TFSA"]))

    if unallocated > 0.01:
        st.warning(
            f"${unallocated:,.2f} of your budget can't be placed — "
            f"you've run out of room in all registered accounts. "
            f"Consider investing in a non-registered account for the remainder."
        )

    # ── Tax impact ────────────────────────────────────────────────────────────
    st.markdown("#### Tax Impact")
    t1, t2, t3, t4 = st.columns(4)
    with t1:
        st.metric(
            "Combined Marginal Rate", f"{marginal * 100:.1f}%",
            help="Your federal + Ontario marginal rate at this income. "
                 "This is the rate on your last dollar of income — and the value of your next deduction.",
        )
    with t2:
        st.metric("Est. Tax (no contributions)", f"${base_tax:,.2f}")
    with t3:
        st.metric(
            "Est. Tax (with contributions)", f"${new_tax:,.2f}",
            delta=f"-${savings:,.2f}", delta_color="inverse",
        )
    with t4:
        st.metric(
            "💰 Estimated Tax Savings", f"${savings:,.2f}",
            help="Reduction from RRSP + FHSA deductions. TFSA contributions have no tax impact.",
        )

    # ── Bar chart ─────────────────────────────────────────────────────────────
    if total_rec > 0:
        fig = go.Figure(go.Bar(
            x   = ["FHSA", "RRSP", "TFSA"],
            y   = [rec["FHSA"], rec["RRSP"], rec["TFSA"]],
            marker_color = ["#2196F3", "#FF9800", "#4CAF50"],
            text         = [f"${v:,.0f}" if v > 0 else "" for v in [rec["FHSA"], rec["RRSP"], rec["TFSA"]]],
            textposition = "outside",
        ))
        fig.update_layout(
            title    = "Recommended contribution split",
            yaxis    = dict(title="Amount (CA$)", tickformat="$,.0f"),
            height   = 300,
            margin   = dict(l=20, r=20, t=40, b=20),
        )
        st.plotly_chart(fig, use_container_width=True)

    # ── Reasoning ─────────────────────────────────────────────────────────────
    with st.expander("ℹ️ Why this recommendation?"):
        bullets = []

        if d["fhsa_is_open"] and rec["FHSA"] > 0:
            bullets.append(
                "**FHSA first** — your FHSA contribution is deductible from income (like RRSP) "
                "*and* the account grows completely tax-free (like TFSA). If you use the funds "
                "for a qualifying home purchase, it's the best-of-both-worlds account available "
                "in Canada. It always gets filled first when room is available."
            )
        elif not d["fhsa_is_open"]:
            bullets.append(
                "**FHSA not open** — if you're a first-time home buyer, opening an FHSA would "
                "unlock $8,000/year of deductible, tax-free room. Consider opening one in "
                "⚙️ Settings."
            )

        if marginal >= RRSP_THRESHOLD:
            bullets.append(
                f"**RRSP before TFSA** — your combined marginal rate is **{marginal*100:.1f}%**, "
                f"above the ~31% crossover point. Every $1,000 you contribute to your RRSP "
                f"saves approximately **${marginal * 1_000:,.0f}** in taxes this year. "
                f"At this rate, the guaranteed upfront deduction is generally more valuable than "
                f"TFSA's tax-free growth."
            )
        else:
            bullets.append(
                f"**TFSA before RRSP** — your combined marginal rate is **{marginal*100:.1f}%**, "
                f"below the ~31% crossover point. At this rate, the RRSP deduction provides "
                f"a smaller immediate saving. TFSA's tax-free growth tends to be a better "
                f"long-term choice, especially if your income in retirement is expected to be "
                f"similar to or higher than it is now."
            )

        if rec["RRSP"] > 0:
            bullets.append(
                f"**RRSP deduction value** — contributing ${rec['RRSP']:,.2f} to your RRSP "
                f"reduces your taxable income by the same amount, saving you "
                f"approximately **${savings - (base_tax - calc_tax(gross, fhsa_ded=rec['FHSA'])[2]):,.0f}** "
                f"of that total saving."
            )

        for b in bullets:
            st.markdown(f"- {b}")

        st.caption(
            "⚠️ These estimates use 2025 Ontario brackets and do not account for CPP/EI credits, "
            "other non-refundable credits, dividend income, capital gains, income splitting, "
            "or provincial surtax nuances. Always verify with a tax professional or CRA My Account."
        )

    st.divider()

# ─── Coming soon: Payroll deduction calculator ────────────────────────────────

with st.expander("🔜 Coming soon: Real-time tax refund / owing calculator"):
    st.markdown("""
    A future update will let you enter your pay stub details to estimate your
    real-time tax refund or balance owing — updating live as you adjust your contribution amounts above.

    **Planned inputs per pay period:**
    - Gross pay
    - CPP deductions
    - EI deductions
    - Income tax withheld
    - Other deductions (benefits, insurance)
    - Net pay

    The calculator will annualize your payroll deductions, factor in your RRSP and FHSA
    contributions above, and show your estimated refund or balance owing for the year.
    """)
