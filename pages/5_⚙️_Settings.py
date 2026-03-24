"""
Settings page — configure personal details, RRSP room, milestones, and TFSA/FHSA adjustments.
"""

import streamlit as st
from datetime import date

from utils.sheets import get_settings, update_settings

st.set_page_config(page_title="Settings", page_icon="⚙️", layout="wide")
st.title("⚙️ Settings")
st.caption("Configure your personal details, contribution room inputs, and projection milestones.")
st.divider()

settings = get_settings()

def s(key, default=""):
    return settings.get(key, default)


# ─── Section 1: Personal details ─────────────────────────────────────────────

st.subheader("👤 Personal Details")
st.caption("Birth years are used to calculate your TFSA contribution room eligibility.")

col1, col2 = st.columns(2)
with col1:
    st.markdown("**Isaac**")
    isaac_birth = st.number_input(
        "Birth Year",
        min_value=1950, max_value=date.today().year - 18,
        value=int(s("isaac_birth_year", "1995")),
        key="isaac_birth",
    )
with col2:
    st.markdown("**Katherine**")
    katherine_birth = st.number_input(
        "Birth Year",
        min_value=1950, max_value=date.today().year - 18,
        value=int(s("katherine_birth_year", "1995")),
        key="katherine_birth",
    )

st.divider()

# ─── Section 2: RRSP contribution room ───────────────────────────────────────

st.subheader("📋 RRSP Contribution Room")
st.caption(
    "Enter the **RRSP deduction limit** from your most recent Notice of Assessment (NOA). "
    "The app will subtract contributions you've logged here to estimate remaining room."
)

col1, col2 = st.columns(2)
with col1:
    st.markdown("**Isaac — NOA RRSP Room**")
    rrsp_isaac = st.number_input(
        "RRSP Room (CA$)",
        min_value=0.0, step=100.0, format="%.2f",
        value=float(s("rrsp_room_isaac", "0") or 0),
        key="rrsp_isaac",
    )
with col2:
    st.markdown("**Katherine — NOA RRSP Room**")
    rrsp_katherine = st.number_input(
        "RRSP Room (CA$)",
        min_value=0.0, step=100.0, format="%.2f",
        value=float(s("rrsp_room_katherine", "0") or 0),
        key="rrsp_katherine",
    )

st.divider()

# ─── Section 3: TFSA adjustments ─────────────────────────────────────────────

st.subheader("🏦 TFSA — Prior Contributions")
st.caption(
    "If you made TFSA contributions **before** using this app (e.g. through previous years "
    "or another platform), enter the total here so your remaining room is accurate."
)

col1, col2 = st.columns(2)
with col1:
    st.markdown("**Isaac — Pre-app TFSA contributions**")
    tfsa_prior_isaac = st.number_input(
        "Amount (CA$)",
        min_value=0.0, step=100.0, format="%.2f",
        value=float(s("tfsa_prior_contributions_isaac", "0") or 0),
        key="tfsa_prior_isaac",
    )
with col2:
    st.markdown("**Katherine — Pre-app TFSA contributions**")
    tfsa_prior_katherine = st.number_input(
        "Amount (CA$)",
        min_value=0.0, step=100.0, format="%.2f",
        value=float(s("tfsa_prior_contributions_katherine", "0") or 0),
        key="tfsa_prior_katherine",
    )

st.divider()

# ─── Section 4: FHSA setup ───────────────────────────────────────────────────

st.subheader("🏠 FHSA Setup")
st.caption(
    "Enter the year your FHSA was opened and any contributions made before this app. "
    "The FHSA launched April 1, 2023."
)

col1, col2 = st.columns(2)
with col1:
    st.markdown("**Isaac**")
    fhsa_open_isaac = st.number_input(
        "FHSA Account Opened (Year)",
        min_value=2023, max_value=date.today().year,
        value=int(s("fhsa_open_year_isaac", "2023")),
        key="fhsa_open_isaac",
    )
    fhsa_prior_isaac = st.number_input(
        "Pre-app FHSA contributions (CA$)",
        min_value=0.0, step=100.0, format="%.2f",
        value=float(s("fhsa_prior_contributions_isaac", "0") or 0),
        key="fhsa_prior_isaac",
    )
with col2:
    st.markdown("**Katherine**")
    fhsa_open_katherine = st.number_input(
        "FHSA Account Opened (Year)",
        min_value=2023, max_value=date.today().year,
        value=int(s("fhsa_open_year_katherine", "2023")),
        key="fhsa_open_katherine",
    )
    fhsa_prior_katherine = st.number_input(
        "Pre-app FHSA contributions (CA$)",
        min_value=0.0, step=100.0, format="%.2f",
        value=float(s("fhsa_prior_contributions_katherine", "0") or 0),
        key="fhsa_prior_katherine",
    )

st.divider()

# ─── Section 5: Milestone targets ────────────────────────────────────────────

st.subheader("🎯 Milestone Targets")
st.caption("Set up to three portfolio value milestones to track on the home page.")

col1, col2, col3 = st.columns(3)
with col1:
    milestone_1 = st.number_input(
        "Milestone 1 (CA$)",
        min_value=1000.0, step=10000.0, format="%.0f",
        value=float(s("milestone_1", "250000") or 250000),
        key="m1",
    )
with col2:
    milestone_2 = st.number_input(
        "Milestone 2 (CA$)",
        min_value=1000.0, step=10000.0, format="%.0f",
        value=float(s("milestone_2", "500000") or 500000),
        key="m2",
    )
with col3:
    milestone_3 = st.number_input(
        "Milestone 3 (CA$)",
        min_value=1000.0, step=10000.0, format="%.0f",
        value=float(s("milestone_3", "1000000") or 1000000),
        key="m3",
    )

st.divider()

# ─── Section 6: Monthly contribution target ───────────────────────────────────

st.subheader("📅 Monthly Contribution Target")
st.caption("Used to track whether you're hitting your savings goals each month.")

monthly_target = st.number_input(
    "Target Monthly Contribution (CA$)",
    min_value=0.0, step=50.0, format="%.2f",
    value=float(s("monthly_contribution_target", "1000") or 1000),
    key="monthly_target",
)

st.divider()

# ─── Save button ──────────────────────────────────────────────────────────────

if st.button("💾 Save All Settings", type="primary", use_container_width=True):
    updates = {
        "isaac_birth_year":                   str(int(isaac_birth)),
        "katherine_birth_year":               str(int(katherine_birth)),
        "rrsp_room_isaac":                    str(rrsp_isaac),
        "rrsp_room_katherine":                str(rrsp_katherine),
        "tfsa_prior_contributions_isaac":     str(tfsa_prior_isaac),
        "tfsa_prior_contributions_katherine": str(tfsa_prior_katherine),
        "fhsa_open_year_isaac":               str(int(fhsa_open_isaac)),
        "fhsa_open_year_katherine":           str(int(fhsa_open_katherine)),
        "fhsa_prior_contributions_isaac":     str(fhsa_prior_isaac),
        "fhsa_prior_contributions_katherine": str(fhsa_prior_katherine),
        "milestone_1":                        str(milestone_1),
        "milestone_2":                        str(milestone_2),
        "milestone_3":                        str(milestone_3),
        "monthly_contribution_target":        str(monthly_target),
    }
    with st.spinner("Saving to Google Sheets…"):
        update_settings(updates)
    st.success("✅ Settings saved!")
    st.rerun()

st.divider()

# ─── Quick tips ───────────────────────────────────────────────────────────────

with st.expander("💡 Tips for getting started"):
    st.markdown("""
1. **Set your birth years** — TFSA room calculation depends on when you turned 18.
2. **Enter your RRSP room from your last NOA** — find it at CRA My Account or on your paper NOA.
3. **Enter pre-app TFSA/FHSA contributions** — any money contributed before you started using this app, so your remaining room is accurate.
4. **Set realistic milestones** — the home page will show you how far away each one is based on your current return rate and contribution pace.
5. **Keep your RRSP room updated** — after you file your taxes each year, update the NOA room field with the new figure from CRA.
    """)
