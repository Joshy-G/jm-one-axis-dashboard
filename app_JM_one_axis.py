import streamlit as st
import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import uuid

# ==========================================
# CONFIG
# ==========================================

#STARTING_CAPITAL = 500000

st.set_page_config(page_title="JM ONE AXIS Dashboard", layout="wide")

# ==========================================
# GOOGLE SHEETS CONNECTION (CACHED)
# ==========================================

@st.cache_resource
def init_connection():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]

    # Read credentials from Streamlit secrets
    gcp_secrets = dict(st.secrets["gcp_service_account"])

    creds = ServiceAccountCredentials.from_json_keyfile_dict(gcp_secrets, scope)

    client = gspread.authorize(creds)
    sheet = client.open("JM ONE AXIS Trader – 1")

    trade_entry_ws = sheet.worksheet("TRADE_ENTRY")
    trade_log_ws = sheet.worksheet("TRADE_LOG")
    settings_ws = sheet.worksheet("SETTINGS")

    return trade_entry_ws, trade_log_ws, settings_ws


trade_entry_ws, trade_log_ws, settings_ws = init_connection()

# ==========================================
# LOAD SETTINGS (CACHED)
# ==========================================

@st.cache_data(ttl=3600)
def load_settings():
    settings_data = settings_ws.get_all_values()
    return pd.DataFrame(settings_data[1:], columns=settings_data[0])

settings_df = load_settings()

traders = settings_df["TRADERS"].dropna().tolist()
instruments = settings_df["INSTRUMENTS"].dropna().tolist()
setups = settings_df["SETUPS"].dropna().tolist()
option_types = settings_df["OPTION_TYPE"].dropna().tolist()
exit_reasons = settings_df["EXIT_REASON"].dropna().tolist()
violation_types = settings_df["RULE_VIOLATION_TYPE"].dropna().tolist()
passwords = settings_df["PASSWORD"].dropna().tolist()

password_map = dict(zip(traders, passwords))

# --- NEW: Read initial capital ---
try:
    INITIAL_CAPITAL = float(settings_df["INITIAL_CAPITAL"].dropna().iloc[0])
except (KeyError, IndexError, ValueError):
    INITIAL_CAPITAL = 500000  # fallback if column missing or empty
    st.warning("INITIAL_CAPITAL not found in SETTINGS, using default 500,000")
# --- Read fund adjustments ---
try:
    FUND_WITHDRAW = float(settings_df["FUND_WITHDRAW"].dropna().iloc[0])
except (KeyError, IndexError, ValueError):
    FUND_WITHDRAW = 0.0

try:
    FUND_TOPUP = float(settings_df["FUND_TOPUP"].dropna().iloc[0])
except (KeyError, IndexError, ValueError):
    FUND_TOPUP = 0.0

# Net adjustment (top‑ups increase capital, withdrawals decrease it)
NET_ADJUSTMENT = FUND_TOPUP - FUND_WITHDRAW

# ==========================================
# FUNCTION TO LOAD TRADE LOG (CACHED)
# ==========================================

@st.cache_data(ttl=60)  # Refresh every 60 seconds, or clear manually after submission
def load_and_process_trade_data():
    """Fetch from Google Sheets and compute all derived columns."""
    with st.spinner("Loading trade data..."):
        trade_data = trade_log_ws.get_all_records()
        raw_df = pd.DataFrame(trade_data)

    if raw_df.empty:
        return raw_df

     # Convert date column
    if "Date" in raw_df.columns:
        raw_df["Date"] = pd.to_datetime(raw_df["Date"], errors='coerce')
    else:
        raw_df["Date"] = pd.NaT

    # Convert numeric columns
    numeric_cols = ["Entry Price", "Exit Price", "SL Price", "Quantity"]
    for col in numeric_cols:
        if col in raw_df.columns:
            raw_df[col] = pd.to_numeric(raw_df[col], errors='coerce')

    # Compute P&L, Risk, R Multiple, Result
    if all(col in raw_df.columns for col in ["Exit Price", "Entry Price", "Quantity"]):
        raw_df["P&L"] = (raw_df["Exit Price"] - raw_df["Entry Price"]) * raw_df["Quantity"]
    else:
        raw_df["P&L"] = 0

    if all(col in raw_df.columns for col in ["Entry Price", "SL Price", "Quantity"]):
        raw_df["Risk"] = (raw_df["Entry Price"] - raw_df["SL Price"]) * raw_df["Quantity"]
    else:
        raw_df["Risk"] = 0

    raw_df["R Multiple"] = raw_df.apply(
        lambda row: row["P&L"] / row["Risk"] if row["Risk"] != 0 else 0, axis=1
    )
    raw_df["Result"] = raw_df["P&L"].apply(lambda x: "Win" if x > 0 else "Loss")

    # Sort and cumulative metrics
    raw_df = raw_df.sort_values("Date").reset_index(drop=True)
    # Adjusted starting capital
    adjusted_start = INITIAL_CAPITAL + NET_ADJUSTMENT

    # Cumulative metrics
    raw_df["Equity"] = adjusted_start + raw_df["P&L"].cumsum()
    raw_df["Peak"] = raw_df["Equity"].cummax()
    raw_df["Drawdown"] = raw_df["Equity"] - raw_df["Peak"]

    return raw_df

# ==========================================
# SESSION STATE
# ==========================================

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "trade_submitted" not in st.session_state:
    st.session_state.trade_submitted = False

if "user" not in st.session_state:
    st.session_state.user = None

# ==========================================
# LOGIN
# ==========================================

if not st.session_state.logged_in:

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.image("logo.png", width=500)
        st.markdown("<h1 style='text-align: center;'>Please Login</h1>", unsafe_allow_html=True)

    selected_trader = st.selectbox("Select Trader", traders)
    entered_password = st.text_input("Password", type="password")

    if st.button("Login"):
        if password_map.get(selected_trader) == entered_password:
            st.session_state.logged_in = True
            st.session_state.user = selected_trader
            st.rerun()
        else:
            st.error("Invalid password")
    st.stop()  # Stop here – don't load data until logged in

# ==========================================
# MAIN APP
# ==========================================

col1, col2, col3 = st.columns([1, 2, 1])
with col2:
    st.image("logo.png", width=300)

st.sidebar.success(f"Logged in as: {st.session_state.user}")

# Load trade data – cached, so it won't refetch on every interaction
df = load_and_process_trade_data()

# Sidebar navigation
st.sidebar.divider()
menu = st.sidebar.radio(
    "Navigation",
    ["Trade Entry", "Performance Dashboard", "Risk Monitoring", "Reports"]
)

# ----------------------------------------------------------
# Sidebar: Today's Starting & Closing Capital
# ----------------------------------------------------------
st.sidebar.divider()
st.sidebar.markdown("### Today's Capital")

today = datetime.now().date()
adjusted_start = INITIAL_CAPITAL + NET_ADJUSTMENT

if df.empty:
    start_capital = adjusted_start
    close_capital = adjusted_start
else:
    # Equity before today (end of previous day)
    df_before_today = df[df["Date"].dt.date < today]
    if df_before_today.empty:
        start_capital = adjusted_start
    else:
        start_capital = df_before_today["Equity"].iloc[-1]

    close_capital = df["Equity"].iloc[-1]

st.sidebar.metric("Start", f"₹{start_capital:,.0f}")
st.sidebar.metric("Close", f"₹{close_capital:,.0f}")
if st.sidebar.button("Logout"):
    st.session_state.logged_in = False
    st.session_state.user = None
    st.cache_data.clear()  # Optional: clear cache on logout
    st.rerun()

# -------------------- Trade Entry --------------------
if menu == "Trade Entry":
    # Show submission feedback if present
    if st.session_state.get("submit_message"):
        if st.session_state.submit_message == "success":
            st.success("Trade Submitted Successfully")
        elif st.session_state.submit_message == "risk_violation":
            st.warning("⚠ Trade recorded but exceeded 1% risk limit.")
        del st.session_state.submit_message
        st.session_state.trade_submitted = False

    st.title("Trade Entry")

    col1, col2 = st.columns(2)
    with col1:
        instrument = st.selectbox("Instrument", instruments)
        option_type = st.selectbox("Option Type", option_types)
        strike = st.text_input("Strike")
        expiry = st.date_input("Expiry")
        setup = st.selectbox("Setup", setups)
        entry_price = st.number_input("Entry Price", min_value=0.0)
        sl_price = st.number_input("SL Price", min_value=0.0)
        target_price = st.number_input("Target Price", min_value=0.0)

    with col2:
        quantity = st.number_input("Quantity", min_value=1)
        exit_price = st.number_input("Exit Price", min_value=0.0)
        exit_reason = st.selectbox("Exit Reason", exit_reasons)
        rule_violation = st.selectbox("Rule Violation?", ["No", "Yes"])
        violation_type = st.selectbox("Violation Type", violation_types)
        remarks = st.text_area("Remarks")
        chart_link = st.text_input("Chart Link")

    if st.button("Submit Trade") and not st.session_state.trade_submitted:
        # Validation
        if (not instrument or not option_type or not strike or not setup or
            entry_price <= 0 or sl_price <= 0 or quantity <= 0 or exit_price <= 0):
            st.error("Please fill all mandatory fields correctly.")
            st.stop()

        trade_risk = (entry_price - sl_price) * quantity
        MAX_RISK_PER_TRADE = (INITIAL_CAPITAL + NET_ADJUSTMENT) * 0.01
        risk_flag = "OK" if trade_risk <= MAX_RISK_PER_TRADE else "Risk Violation"

        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

        # Use current df to get next trade ID (fast, no extra API call)
        trade_count = len(df) + 1
        trade_id = f"JM-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"

        row = [
            trade_id, timestamp, date_str, st.session_state.user,
            instrument, option_type, strike, expiry.strftime("%Y-%m-%d"),
            setup, entry_price, sl_price, target_price, quantity,
            exit_price, exit_reason, rule_violation, violation_type,
            remarks, chart_link, risk_flag
        ]

        trade_entry_ws.append_row(row)

        # Invalidate ONLY the trade data cache – settings remain untouched
        st.cache_data.clear()  # Clears all cached functions (simplest approach)
        # Alternatively, we could use a version number to force reload, but clear() is fine.

        st.session_state.submit_message = "risk_violation" if risk_flag == "Risk Violation" else "success"
        st.session_state.trade_submitted = True
        st.rerun()

            
                
# -------------------- Performance Dashboard --------------------
elif menu == "Performance Dashboard":
    st.divider()
    st.header("Performance Analytics")

    if df.empty:
        st.info("No trade data available.")
    else:
        # ---------- Trader filter ----------
        trader_list = ["All"]
        if "Trader" in df.columns:
            trader_list += df["Trader"].unique().tolist()
        selected_trader_filter = st.selectbox("Select Trader", trader_list)

        # Filter by trader first
        if selected_trader_filter != "All":
            filtered_df = df[df["Trader"] == selected_trader_filter].copy()
        else:
            filtered_df = df.copy()

        # ---------- Date range filter ----------
        if not filtered_df.empty and "Date" in filtered_df.columns:
            min_date = filtered_df["Date"].min().date()
            max_date = filtered_df["Date"].max().date()
            col_date1, col_date2 = st.columns(2)
            with col_date1:
                start_date = st.date_input("Start Date", min_date, min_value=min_date, max_value=max_date)
            with col_date2:
                end_date = st.date_input("End Date", max_date, min_value=min_date, max_value=max_date)

            # Apply date filter
            mask = (filtered_df["Date"].dt.date >= start_date) & (filtered_df["Date"].dt.date <= end_date)
            period_df = filtered_df[mask].copy()
        else:
            period_df = filtered_df.copy()
            st.warning("Date column missing – cannot filter by date.")

        if period_df.empty:
            st.warning("No trades in the selected period.")
        else:
            # ---------- Cumulative metrics for the period ----------
            total_trades = len(period_df)
            win_rate = (period_df["Result"] == "Win").mean() * 100
            total_pnl = period_df["P&L"].sum()
            avg_r = period_df["R Multiple"].mean()
            period_df["Equity_Period"] = INITIAL_CAPITAL + period_df["P&L"].cumsum()
            period_df["Peak_Period"] = period_df["Equity_Period"].cummax()
            period_df["Drawdown_Period"] = period_df["Equity_Period"] - period_df["Peak_Period"]
            max_dd = period_df["Drawdown_Period"].min()

            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Total Trades", total_trades)
            col2.metric("Win Rate %", round(win_rate, 2))
            col3.metric("Total P&L (₹)", round(total_pnl, 2))
            col4.metric("Avg R", round(avg_r, 2))
            col5.metric("Max Drawdown (₹)", round(max_dd, 2))

            st.divider()
            st.subheader("Setup Performance Breakdown")
            if "Setup" in period_df.columns:
                setup_stats = (
                    period_df.groupby("Setup")
                    .agg(Trades=("Setup", "count"),
                         Total_PnL=("P&L", "sum"),
                         Avg_R=("R Multiple", "mean"),
                         Win_Rate=("Result", lambda x: (x == "Win").mean() * 100))
                    .reset_index()
                )
                if not setup_stats.empty:
                    st.dataframe(setup_stats.sort_values(by="Total_PnL", ascending=False).round(2))
                else:
                    st.info("No setup data for this period.")
            else:
                st.info("Setup column not found.")

            # ---------- Equity Curve ----------
            st.divider()
            st.subheader("Equity Curve (Selected Period)")
            if not period_df["Equity_Period"].empty:
                st.line_chart(period_df.set_index("Date")["Equity_Period"])
            else:
                st.info("Not enough data for equity curve.")

            # ---------- P&L Trend (Monthly/Weekly) ----------
            st.divider()
            st.subheader("P&L Over Time")
            freq = st.radio("Group by", ["Monthly", "Weekly"], horizontal=True)
            if freq == "Monthly":
                period_df["Period"] = period_df["Date"].dt.to_period("M").dt.start_time
            else:
                period_df["Period"] = period_df["Date"].dt.to_period("W").dt.start_time

            pnl_trend = period_df.groupby("Period")["P&L"].sum().reset_index()
            pnl_trend["Period"] = pnl_trend["Period"].dt.strftime("%Y-%m" if freq=="Monthly" else "%Y-W%W")
            st.bar_chart(pnl_trend.set_index("Period")["P&L"])

            # ---------- Rolling Win Rate (10 trades) ----------
            st.divider()
            st.subheader("Rolling Win Rate (10 trades)")
            period_df_sorted = period_df.sort_values("Date").reset_index(drop=True)
            period_df_sorted["Win_Flag"] = (period_df_sorted["Result"] == "Win").astype(int)
            period_df_sorted["Rolling_Win_Rate"] = period_df_sorted["Win_Flag"].rolling(10, min_periods=1).mean() * 100
            if len(period_df_sorted) >= 5:
                st.line_chart(period_df_sorted.set_index("Date")["Rolling_Win_Rate"])
            else:
                st.info("Need at least 5 trades for rolling win rate.")

            # ---------- Violations Over Time ----------
            st.divider()
            st.subheader("Rule Violations Over Time")
            if "Rule Violation?" in period_df.columns:
                # Count violations per month/week
                if freq == "Monthly":
                    period_df["Viol_Period"] = period_df["Date"].dt.to_period("M").dt.start_time
                else:
                    period_df["Viol_Period"] = period_df["Date"].dt.to_period("W").dt.start_time

                viol_count = (
                    period_df[period_df["Rule Violation?"] == "Yes"]
                    .groupby("Viol_Period")
                    .size()
                    .reset_index(name="Violations")
                )
                if not viol_count.empty:
                    viol_count["Viol_Period"] = viol_count["Viol_Period"].dt.strftime("%Y-%m" if freq=="Monthly" else "%Y-W%W")
                    st.bar_chart(viol_count.set_index("Viol_Period")["Violations"])
                else:
                    st.success("No rule violations in this period.")
            else:
                st.info("Rule Violation column not found.")

            # ---------- Setup Contribution Over Time (Stacked Bar) ----------
            st.divider()
            st.subheader("Setup P&L Contribution Over Time")
            if "Setup" in period_df.columns:
                # Pivot table: rows = Period, columns = Setup, values = sum(P&L)
                setup_pivot = period_df.pivot_table(
                    index="Period", columns="Setup", values="P&L", aggfunc="sum", fill_value=0
                )
                if not setup_pivot.empty:
                    # Format index for display
                    setup_pivot.index = setup_pivot.index.strftime("%Y-%m" if freq=="Monthly" else "%Y-W%W")
                    st.bar_chart(setup_pivot)
                else:
                    st.info("No setup data to display.")
            else:
                st.info("Setup column not found.")


# -------------------- Risk Monitoring --------------------
elif menu == "Risk Monitoring":
    st.divider()
    st.header("Risk Monitoring")

    if df.empty:
        st.info("No trade data available.")
    else:
        MAX_DAILY_LOSS = (INITIAL_CAPITAL + NET_ADJUSTMENT) * 0.02
        MAX_DRAWDOWN = (INITIAL_CAPITAL + NET_ADJUSTMENT) * 0.10
        MAX_RISK_PER_TRADE = (INITIAL_CAPITAL + NET_ADJUSTMENT) * 0.01

        # Daily P&L (requires Date column as datetime)
        if "Date" in df.columns:
            daily_pnl = df.groupby(df["Date"].dt.date)["P&L"].sum()
            today = pd.to_datetime("today").date()
            today_pnl = daily_pnl.get(today, 0)
        else:
            today_pnl = 0
            st.warning("Date column missing; daily P&L unavailable.")

        col1, col2, col3 = st.columns(3)
        col1.metric("Today's P&L", round(today_pnl, 2))
        col2.metric("Max Daily Loss Limit", -MAX_DAILY_LOSS)
        col3.metric("Max Overall DD Limit", -MAX_DRAWDOWN)

        if today_pnl <= -MAX_DAILY_LOSS:
            st.error("🚨 DAILY LOSS LIMIT BREACHED")
        else:
            st.success("Daily Loss Within Limit")

            current_dd = df["Drawdown"].iloc[-1] if not df.empty else 0
            if current_dd <= -MAX_DRAWDOWN:
                st.error("🚨 MAX DRAWDOWN LIMIT BREACHED")
            else:
                st.success("Overall Drawdown Within Limit")

            # Risk Per Trade Check
            if "Risk" in df.columns:
                df["Risk Violation"] = df["Risk"].apply(
                    lambda x: "Violation" if x > MAX_RISK_PER_TRADE else "OK"
                )
                risk_violations = df[df["Risk Violation"] == "Violation"]

                st.subheader("Risk Per Trade Violations")
                if not risk_violations.empty:
                    st.warning("Some trades exceeded 1% risk")
                    # Show relevant columns if they exist
                    display_cols = ["Trade ID", "Risk"] if "Trade ID" in df.columns else ["Risk"]
                    st.dataframe(risk_violations[display_cols])
                else:
                    st.success("All trades within risk limits")
            else:
                st.info("Risk column not available.")

# -------------------- Reports --------------------
elif menu == "Reports":
    st.divider()
    st.header("Periodic Reports")

    if df.empty:
        st.info("No trade data available.")
    else:
        # Ensure Date column exists and is datetime
        if "Date" not in df.columns or df["Date"].isna().all():
            st.error("Date column missing or invalid. Cannot generate reports.")
        else:
            # Drop rows with missing dates
            report_df = df.dropna(subset=["Date"]).copy()
            if report_df.empty:
                st.warning("No trades with valid dates.")
            else:
                # Report type selector
                report_type = st.selectbox("Select Report Period", ["Daily", "Weekly", "Monthly"])

                if report_type == "Daily":
                    # Group by date
                    report_df["Period"] = report_df["Date"].dt.date
                    freq = "D"
                elif report_type == "Weekly":
                    # Group by week (start of week Monday)
                    report_df["Period"] = report_df["Date"].dt.to_period("W").apply(lambda r: r.start_time)
                    freq = "W"
                else:  # Monthly
                    report_df["Period"] = report_df["Date"].dt.to_period("M").apply(lambda r: r.start_time)
                    freq = "M"

                # Aggregate metrics
                grouped = report_df.groupby("Period").agg(
                    Trades=("P&L", "count"),
                    Total_PnL=("P&L", "sum"),
                    Avg_R=("R Multiple", "mean"),
                    Win_Rate=("Result", lambda x: (x == "Win").mean() * 100)
                ).reset_index()

                # Format Period for display
                if report_type == "Daily":
                    grouped["Period"] = grouped["Period"].astype(str)
                elif report_type == "Weekly":
                    grouped["Period"] = grouped["Period"].dt.strftime("%Y-%W")
                else:
                    grouped["Period"] = grouped["Period"].dt.strftime("%Y-%m")

                # Display summary metrics
                total_trades_period = grouped["Trades"].sum()
                total_pnl_period = grouped["Total_PnL"].sum()
                avg_win_rate = grouped["Win_Rate"].mean()

                col1, col2, col3 = st.columns(3)
                col1.metric(f"Total {report_type} Trades", total_trades_period)
                col2.metric(f"Total {report_type} P&L (₹)", round(total_pnl_period, 2))
                col3.metric(f"Avg {report_type} Win Rate %", round(avg_win_rate, 2))

                st.divider()
                st.subheader(f"{report_type} Breakdown")
                st.dataframe(grouped.round(2))

                # Bar chart of daily/weekly/monthly P&L
                st.divider()
                st.subheader(f"{report_type} P&L Trend")
                chart_data = grouped.set_index("Period")["Total_PnL"]
                st.bar_chart(chart_data)
