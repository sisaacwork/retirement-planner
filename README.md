# 🏦 Retirement Planner

A personal retirement tracker for Isaac & Katherine — built with Streamlit and backed by Google Sheets.

---

## Features

- **Log contributions** by account (TFSA, FHSA, RRSP, NRSP) and person
- **Log daily returns** from Wealthsimple and balance snapshots from Canada Life
- **Dashboard** with portfolio history chart, account breakdown, and per-person summary
- **Milestone projections** — estimated time to $250k, $500k, $1M (or your own targets)
- **Money-Weighted Rate of Return (XIRR)** calculated from your actual cash flows
- **Contribution room tracker** for TFSA, FHSA, and RRSP based on CRA guidelines
- **Monthly contribution targets** with YTD tracking

---

## Setup (one-time)

### Step 1 — Create the Google Sheet

1. Go to [Google Sheets](https://sheets.google.com) and create a new blank spreadsheet.
2. Name it exactly: **`Retirement Planner`** (or whatever you want — you'll set this in Step 3).

### Step 2 — Set up a Google Cloud service account

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (e.g. `retirement-planner`).
3. In the left menu, go to **APIs & Services → Library**.
4. Search for and enable:
   - **Google Sheets API**
   - **Google Drive API**
5. Go to **APIs & Services → Credentials**.
6. Click **Create Credentials → Service Account**.
7. Give it a name (e.g. `retirement-planner-bot`) and click **Done**.
8. Click on the service account you just created → **Keys** tab → **Add Key → Create new key → JSON**.
9. Download the JSON file — keep it safe and **never commit it to GitHub**.

### Step 3 — Share the sheet with the service account

1. Open your Google Sheet.
2. Click **Share** (top right).
3. Copy the `client_email` from the JSON file (looks like `something@project.iam.gserviceaccount.com`).
4. Paste it into the Share dialog and give it **Editor** access.

### Step 4 — Add credentials to the app

1. In your project folder, copy the example secrets file:
   ```
   cp .streamlit/secrets.toml.example .streamlit/secrets.toml
   ```
2. Open `.streamlit/secrets.toml` and fill in the values from your JSON key file.
3. Update `spreadsheet_name` to match your sheet's name.

> ⚠️ `secrets.toml` is already in `.gitignore` — it will **never** be committed to GitHub.

### Step 5 — Install dependencies

Make sure you have Python 3.10+ installed, then:

```bash
pip install -r requirements.txt
```

### Step 6 — Run the app

```bash
streamlit run app.py
```

The app will open in your browser at `http://localhost:8501`.

---

## First-time use

1. Open ⚙️ **Settings** and enter:
   - Your and Katherine's birth years (for TFSA room)
   - RRSP room from your last NOA
   - TFSA/FHSA contributions made before using this app
   - Your milestone targets
2. Go to 💰 **Log Contribution** and add your first contribution.
3. Periodically check 📈 **Log Returns** to enter Wealthsimple daily returns or Canada Life balance snapshots.

---

## How the numbers work

| Metric | Method |
|---|---|
| **Current Balance** | Latest balance snapshot + contributions since + returns since |
| **Rate of Return** | XIRR (Money-Weighted Rate of Return) using all contributions as outflows and current balance as inflow |
| **TFSA Room** | CRA annual limits from eligibility year − all tracked contributions − pre-app contributions |
| **FHSA Room** | $8,000/year from account open year, capped at $40,000 lifetime |
| **RRSP Room** | Your NOA figure − RRSP contributions tracked in app |
| **Milestone ETA** | Future value formula: FV = PV(1+r)ⁿ + PMT·((1+r)ⁿ−1)/r, solved for n |

---

## Keeping it up to date

| When | What to do |
|---|---|
| Every day you check Wealthsimple | Log the daily return in 📈 Log Returns |
| When you check Canada Life | Add a balance snapshot in 📈 Log Returns → Balance Snapshots |
| After every contribution | Log it in 💰 Log Contribution |
| After filing taxes | Update your RRSP NOA room in ⚙️ Settings |
| January each year | CRA adds new TFSA room automatically — no action needed |

---

## Tech stack

- [Streamlit](https://streamlit.io) — UI framework
- [gspread](https://docs.gspread.org/) — Google Sheets API client
- [Plotly](https://plotly.com/python/) — Charts
- [SciPy](https://scipy.org) — XIRR calculation (Brent's method)
- [Pandas](https://pandas.pydata.org) — Data wrangling
