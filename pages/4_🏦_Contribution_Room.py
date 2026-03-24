"""
Contribution Room page — TFSA, FHSA, and RRSP room tracking for Isaac and Katherine.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from utils.sheets import get_contributions, get_settings
from utils.calculations import (
    tfsa_cumulative_room, tfsa_remaining_room,
    fhsa_remaining_room, rrsp_remaining_room,
)
from utils.constants import TFSA_ANNUAL_LIMITS, FHSA_ANNUAL_LIMIT, FHSA_LIFETIME_LIMIT

st.set_page_config(page_title="Contribution Room", page_icon="🏦", layout="wide")
st.title("🏦 Contribution Room")
st.caption("Track your remaining registered account room based on CRA guidelines.")
st.divider()

# ─── Load data ────────────────────────────────────────────────────────────────

contributions = get_contributions()
settings      = get_settings()

def s(key, default="0"):
    """Safe settings getter."""
    return settings.get(key, default)

try:
    isaac_birth_year        = int(s("isaac_birth_year", "1995"))
    katherine_birth_year    = int(s("katherine_birth_year", "1995"))
    isaac_tfsa_eligible     = int(s("tfsa_eligible_year_isaac", "2025"))
    katherine_tfsa_eligible = int(s("tfsa_eligible_year_katherine", "2026"))
    isaac_fhsa_open         = int(s("fhsa_open_year_isaac", "2025"))
    katherine_fhsa_open     = int(s("fhsa_open_year_katherine", "2026"))
    rrsp_room_isaac         = float(s("rrsp_room_isaac", "0"))
    rrsp_room_katherine     = float(s("rrsp_room_katherine", "0"))
    tfsa_prior_isaac        = float(s("tfsa_prior_contributions_isaac", "0"))
    tfsa_prior_katherine    = float(s("tfsa_prior_contributions_katherine", "0"))
    fhsa_prior_isaac        = float(s("fhsa_prior_contributions_isaac", "0"))
    fhsa_prior_katherine    = float(s("fhsa_prior_contributions_katherine", "0"))
except (ValueError, TypeError):
    st.error("⚠️ Some settings are missing or invalid. Please visit ⚙️ Settings to configure them.")
    st.stop()

# ─── Helper: gauge chart ──────────────────────────────────────────────────────

def gauge(used: float, total: float, label: str, colour: str) -> go.Figure:
    remaining = max(total - used, 0)
    pct_used  = min(used / total * 100, 100) if total > 0 else 0

    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=remaining,
        number={"prefix": "$", "valueformat": ",.0f"},
        delta={"reference": total, "valueformat": ",.0f",
               "prefix": "Remaining of $", "relative": False},
        title={"text": label},
        gauge={
            "axis":  {"range": [0, total], "tickformat": ",.0f"},
            "bar":   {"color": colour},
            "steps": [
                {"range": [0, total], "color": "#f0f0f0"},
            ],
            "threshold": {
                "line":  {"color": "red", "width": 3},
                "thickness": 0.75,
                "value": total,
            },
        },
    ))
    fig.update_layout(height=250, margin=dict(l=20, r=20, t=60, b=10))
    return fig


# ─── Per-person panels ────────────────────────────────────────────────────────

for person, birth_year, tfsa_eligible, fhsa_open, rrsp_room, tfsa_prior, fhsa_prior in [
    ("Isaac",     isaac_birth_year,    isaac_tfsa_eligible,     isaac_fhsa_open,
     rrsp_room_isaac,     tfsa_prior_isaac,    fhsa_prior_isaac),
    ("Katherine", katherine_birth_year, katherine_tfsa_eligible, katherine_fhsa_open,
     rrsp_room_katherine, tfsa_prior_katherine, fhsa_prior_katherine),
]:
    st.subheader(f"👤 {person}")

    # ── TFSA ──────────────────────────────────────────────────────────────────
    tfsa_total     = tfsa_cumulative_room(birth_year, eligible_from_year=tfsa_eligible)
    tfsa_remaining = tfsa_remaining_room(birth_year, contributions, tfsa_prior,
                                         person=person, eligible_from_year=tfsa_eligible)
    tfsa_used      = tfsa_total - tfsa_remaining

    # ── FHSA ──────────────────────────────────────────────────────────────────
    fhsa_remaining = fhsa_remaining_room(fhsa_open, contributions, fhsa_prior, person=person)
    fhsa_total     = FHSA_LIFETIME_LIMIT
    fhsa_used      = fhsa_total - fhsa_remaining

    # ── RRSP ──────────────────────────────────────────────────────────────────
    rrsp_remaining = rrsp_remaining_room(rrsp_room, contributions, person=person)

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("**🏦 TFSA**")
        st.metric("Remaining Room",   f"${tfsa_remaining:,.2f}")
        st.metric("Total Accumulated", f"${tfsa_total:,.2f}")
        st.metric("Used (tracked)",    f"${tfsa_used:,.2f}")
        used_pct = min(tfsa_used / tfsa_total * 100, 100) if tfsa_total > 0 else 0
        st.progress(used_pct / 100, text=f"{used_pct:.1f}% used")

        if tfsa_remaining < 0:
            st.error(f"⚠️ Over-contributed by ${abs(tfsa_remaining):,.2f}! Check CRA My Account.")

    with col2:
        st.markdown("**🏠 FHSA**")
        if fhsa_open < 2023:
            st.info("FHSA opened before 2023 — adjusted to 2023 launch date.")
        st.metric("Remaining Room",  f"${fhsa_remaining:,.2f}")
        st.metric("Lifetime Limit",  f"${fhsa_total:,.2f}")
        st.metric("Used (tracked)",  f"${fhsa_used:,.2f}")
        fhsa_pct = min(fhsa_used / fhsa_total * 100, 100) if fhsa_total > 0 else 0
        st.progress(fhsa_pct / 100, text=f"{fhsa_pct:.1f}% of lifetime limit")
        st.caption(f"Annual FHSA limit: ${FHSA_ANNUAL_LIMIT:,}/year · Max lifetime: ${fhsa_total:,}")

    with col3:
        st.markdown("**📋 RRSP**")
        if rrsp_room <= 0:
            st.warning("Enter your RRSP room from your last NOA in ⚙️ Settings.")
        else:
            st.metric("Remaining Room", f"${rrsp_remaining:,.2f}")
            st.metric("NOA Room",       f"${rrsp_room:,.2f}")
            rrsp_used = rrsp_room - rrsp_remaining
            st.metric("Used (tracked)", f"${rrsp_used:,.2f}")
            rrsp_pct = min(rrsp_used / rrsp_room * 100, 100) if rrsp_room > 0 else 0
            st.progress(rrsp_pct / 100, text=f"{rrsp_pct:.1f}% used")
        st.caption("Update your NOA room each year after filing taxes in ⚙️ Settings.")

    # TFSA year-by-year table (collapsed)
    with st.expander(f"📋 {person}'s TFSA Room by Year"):
        age_eligible  = max(birth_year + 18, 2009)
        eligible_from = max(age_eligible, tfsa_eligible)
        rows = []
        for year, limit in sorted(TFSA_ANNUAL_LIMITS.items()):
            if year >= eligible_from:
                rows.append({"Year": year, "Annual Limit": f"${limit:,}"})
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(
                f"Eligible from **{eligible_from}** (Canadian resident since {tfsa_eligible}). "
                f"Cumulative room through {max(TFSA_ANNUAL_LIMITS.keys())}: **${tfsa_total:,.0f}**"
            )

    st.divider()

# ─── Important notes ──────────────────────────────────────────────────────────

with st.expander("ℹ️ How contribution room works"):
    st.markdown("""
**TFSA**
- Room accumulates every January 1st once you're 18 and a Canadian resident.
- Unused room carries forward indefinitely.
- Withdrawals are added back to your room the following January 1st (not tracked here — adjust manually in Settings if needed).
- Always verify your exact room at [CRA My Account](https://www.canada.ca/en/revenue-agency/services/e-services/digital-services-individuals/account-individuals.html).

**FHSA (First Home Savings Account)**
- $8,000/year, up to $40,000 lifetime.
- Launched April 1, 2023 — only available to first-time home buyers.
- Unused annual room carries forward **1 year only**.
- Once you buy a home, the FHSA must be closed or transferred to an RRSP.

**RRSP**
- 18% of your previous year's earned income (up to the CRA annual maximum).
- Your exact room is on your Notice of Assessment (NOA) from CRA.
- Update this in ⚙️ Settings each year after you file your taxes.
- RRSP contributions reduce your taxable income — a powerful tax deduction!

**Disclaimer:** This app estimates room based on data you've entered. Always confirm your exact contribution room through [CRA My Account](https://www.canada.ca/en/revenue-agency/services/e-services/digital-services-individuals/account-individuals.html) before making contributions.
    """)
