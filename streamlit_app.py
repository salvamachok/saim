
import os
import sqlite3
import hashlib
import random
from contextlib import contextmanager
from datetime import datetime

import streamlit as st
import pandas as pd
import plotly.express as px

# ============================================================
# SAIM - TRADING DASHBOARD
# Demonstration only: no live market data or real broker orders.
# ============================================================

st.set_page_config(
    page_title="Saim | Trading Dashboard",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# DESIGN
# ============================================================

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif;
}

.stApp {
    background: #0b1020;
    color: #eef2ff;
}

[data-testid="stSidebar"] {
    background: #10172a;
    border-right: 1px solid #26324a;
}

.block-container {
    max-width: 1450px;
    padding-top: 2rem;
    padding-bottom: 3rem;
}

.saim-logo {
    font-family: 'Manrope', sans-serif;
    font-size: 2.5rem;
    font-weight: 800;
    letter-spacing: -2px;
    color: #83a7ff;
    margin-bottom: 0;
}

.saim-subtitle {
    color: #94a3b8;
    font-size: 0.9rem;
    margin-top: -5px;
    margin-bottom: 20px;
}

.saim-card {
    background: #131d32;
    border: 1px solid #283650;
    border-radius: 16px;
    padding: 18px;
    margin-bottom: 12px;
}

.saim-muted {
    color: #94a3b8;
    font-size: 0.88rem;
}

div[data-testid="stMetric"] {
    background: #131d32;
    border: 1px solid #283650;
    padding: 18px;
    border-radius: 14px;
}

div[data-testid="stMetricLabel"] {
    color: #a8b4ca;
}

div[data-testid="stButton"] button,
div[data-testid="stFormSubmitButton"] button,
div[data-testid="stDownloadButton"] button {
    border-radius: 9px;
    min-height: 42px;
    font-weight: 600;
}

div[data-testid="stTextInput"] input {
    border-radius: 9px;
}

h1, h2, h3 {
    font-family: 'Manrope', sans-serif;
}

.saim-notice {
    background: #241f15;
    border: 1px solid #70552a;
    border-radius: 10px;
    padding: 12px 15px;
    color: #f5d99b;
    margin: 12px 0 20px 0;
}
</style>
""", unsafe_allow_html=True)

# ============================================================
# DATABASE
# ============================================================

DB_PATH = os.environ.get("DB_PATH", "/tmp/sai.db")


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def hash_password(password):
    # Kept compatible with passwords in the original database.
    # For production, migrate to a password-hashing library such as Argon2.
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def init_database():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                role TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                time TEXT,
                symbol TEXT,
                action TEXT,
                price REAL,
                pnl REAL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS balance (
                username TEXT PRIMARY KEY,
                amount REAL NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_state (
                username TEXT PRIMARY KEY,
                auto_trade INTEGER DEFAULT 0,
                risk REAL DEFAULT 2.0,
                strategy TEXT DEFAULT 'Scalper'
            )
        """)

        user_count = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        if user_count == 0:
            conn.execute(
                "INSERT INTO users VALUES (?, ?, ?)",
                ("admin", hash_password("admin123"), "admin"),
            )
            conn.execute(
                "INSERT INTO balance VALUES (?, ?)",
                ("admin", 10000.0),
            )
            conn.execute(
                "INSERT INTO bot_state (username) VALUES (?)",
                ("admin",),
            )

        conn.commit()


try:
    init_database()
except Exception as exc:
    st.error("The database could not be initialized.")
    st.code(str(exc))
    st.stop()


# ============================================================
# ACCOUNT FUNCTIONS
# ============================================================

def login(username, password):
    username = username.strip()

    with get_db() as conn:
        row = conn.execute(
            "SELECT role FROM users WHERE username=? AND password=?",
            (username, hash_password(password)),
        ).fetchone()

    if row:
        return True, row["role"]

    return False, None


def register(username, password):
    username = username.strip()

    if not username:
        return False, "Enter a username."

    if len(username) > 40:
        return False, "Username must be 40 characters or fewer."

    if len(password) < 8:
        return False, "Password must contain at least 8 characters."

    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users VALUES (?, ?, ?)",
                (username, hash_password(password), "user"),
            )
            conn.execute(
                "INSERT INTO balance VALUES (?, ?)",
                (username, 10000.0),
            )
            conn.execute(
                "INSERT INTO bot_state (username) VALUES (?)",
                (username,),
            )
            conn.commit()

        return True, "Account created. You can now sign in."

    except sqlite3.IntegrityError:
        return False, "That username already exists."


def change_password(username, old_password, new_password):
    if len(new_password) < 8:
        return False, "The new password must contain at least 8 characters."

    with get_db() as conn:
        row = conn.execute(
            "SELECT username FROM users WHERE username=? AND password=?",
            (username, hash_password(old_password)),
        ).fetchone()

        if not row:
            return False, "The current password is incorrect."

        conn.execute(
            "UPDATE users SET password=? WHERE username=?",
            (hash_password(new_password), username),
        )
        conn.commit()

    return True, "Password updated successfully."


def get_users():
    with get_db() as conn:
        return pd.read_sql_query(
            "SELECT username, role FROM users ORDER BY username",
            conn,
        )


def remove_user(username):
    if username == "admin":
        return False

    with get_db() as conn:
        conn.execute("DELETE FROM users WHERE username=?", (username,))
        conn.execute("DELETE FROM balance WHERE username=?", (username,))
        conn.execute("DELETE FROM trades WHERE username=?", (username,))
        conn.execute("DELETE FROM bot_state WHERE username=?", (username,))
        conn.commit()

    return True


def get_balance(username):
    with get_db() as conn:
        row = conn.execute(
            "SELECT amount FROM balance WHERE username=?",
            (username,),
        ).fetchone()

    return float(row["amount"]) if row else 0.0


def get_trade_history(username):
    with get_db() as conn:
        return pd.read_sql_query(
            """
            SELECT id, time, symbol, action, price, pnl
            FROM trades
            WHERE username=?
            ORDER BY id DESC
            """,
            conn,
            params=[username],
        )


# ============================================================
# SESSION STATE
# ============================================================

defaults = {
    "logged": False,
    "username": "",
    "role": "",
    "bot_running": False,
    "risk": 2,
    "auto_trade": False,
    "strategy": "Scalper",
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# SIMULATED MARKET AND SIGNALS
# ============================================================

CURRENCIES = {
    "UGX": 3800,
    "KES": 130,
    "TZS": 2600,
    "SSP": 1600,
    "RWF": 1350,
    "USD": 1,
    "EUR": 0.92,
}


def get_market():
    return {
        currency: round(base + random.uniform(-5, 5), 2)
        for currency, base in CURRENCIES.items()
    }


def run_ai(prices, strategy):
    results = {}

    thresholds = {
        "Scalper": (45, 55, 30, 70),
        "Trend": (35, 65, 20, 80),
        "Breakout": (40, 60, 25, 75),
    }

    buy_limit, sell_limit, low, high = thresholds.get(
        strategy, thresholds["Scalper"]
    )

    for currency, rate in prices.items():
        rsi = round(random.uniform(low, high), 1)

        if rsi < buy_limit:
            signal = "BUY"
        elif rsi > sell_limit:
            signal = "SELL"
        else:
            signal = random.choice(["BUY", "SELL", "HOLD"])

        results[currency] = {
            "signal": signal,
            "confidence": round(random.uniform(50, 99), 1),
            "rsi": rsi,
            "forecast": round(rate + random.uniform(-3, 3), 2),
        }

    return results


def forecast_currency(currency, days):
    value = CURRENCIES.get(currency, 1)
    predictions = []

    for _ in range(days):
        value *= 1 + random.uniform(-0.02, 0.02)
        predictions.append(round(value, 2))

    return predictions


# ============================================================
# SIMULATED TRADING LEDGER
# ============================================================

def execute_trade(username, symbol, action, price, risk, balance):
    # This records a demo trade. It does not contact a broker.
    pnl = random.uniform(-30, 80) * (risk / 100) * balance / 100
    new_balance = max(0.0, balance + pnl)
    pnl = new_balance - balance

    with get_db() as conn:
        conn.execute(
            "UPDATE balance SET amount=? WHERE username=?",
            (new_balance, username),
        )

        conn.execute(
            """
            INSERT INTO trades(username,time,symbol,action,price,pnl)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                username,
                datetime.now().isoformat(timespec="seconds"),
                symbol,
                action,
                price,
                pnl,
            ),
        )

        conn.commit()

    return pnl, new_balance


# ============================================================
# LOGIN AND REGISTRATION
# ============================================================

if not st.session_state.logged:
    left, center, right = st.columns([1, 1.15, 1])

    with center:
        st.markdown(
            """
            <div style="text-align:center; padding:30px 0 20px 0;">
                <div class="saim-logo">Saim</div>
                <div class="saim-subtitle">Trading Intelligence</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.container(border=True):
            login_tab, register_tab = st.tabs(
                ["Sign in", "Create account"]
            )

            with login_tab:
                with st.form("login_form"):
                    username_input = st.text_input("Username")
                    password_input = st.text_input(
                        "Password", type="password"
                    )
                    submitted = st.form_submit_button(
                        "Sign in",
                        type="primary",
                        use_container_width=True,
                    )

                if submitted:
                    success, role = login(
                        username_input, password_input
                    )

                    if success:
                        st.session_state.logged = True
                        st.session_state.username = username_input.strip()
                        st.session_state.role = role
                        st.rerun()
                    else:
                        st.error("Incorrect username or password.")

            with register_tab:
                with st.form("register_form"):
                    new_username = st.text_input("Choose a username")
                    new_password = st.text_input(
                        "Choose a password", type="password"
                    )
                    confirm_password = st.text_input(
                        "Confirm password", type="password"
                    )
                    create_account = st.form_submit_button(
                        "Create account",
                        type="primary",
                        use_container_width=True,
                    )

                if create_account:
                    if new_password != confirm_password:
                        st.error("The passwords do not match.")
                    else:
                        success, message = register(
                            new_username, new_password
                        )
                        if success:
                            st.success(message)
                        else:
                            st.error(message)

        st.caption(
            "Demonstration environment. Prices, signals, and trade results are simulated."
        )

    st.stop()


# ============================================================
# SIDEBAR NAVIGATION
# ============================================================

username = st.session_state.username

with st.sidebar:
    st.markdown('<div class="saim-logo">Saim</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="saim-subtitle">Trading Intelligence</div>',
        unsafe_allow_html=True,
    )

    st.write(f"Account: **{username}**")
    st.caption(f"Role: {st.session_state.role}")
    st.divider()

    page = st.radio(
        "Navigation",
        [
            "Dashboard",
            "Market Analysis",
            "AI Signals",
            "Price Forecasts",
            "Manual Trading",
            "Trade History",
            "Settings",
        ],
        label_visibility="collapsed",
    )

    st.divider()
    st.subheader("Bot controls")

    st.session_state.auto_trade = st.checkbox(
        "Enable simulated auto-trade",
        value=st.session_state.auto_trade,
    )

    st.session_state.risk = st.slider(
        "Risk setting (%)",
        min_value=1,
        max_value=20,
        value=int(st.session_state.risk),
    )

    strategies = ["Scalper", "Trend", "Breakout"]

    st.session_state.strategy = st.selectbox(
        "Strategy",
        strategies,
        index=strategies.index(st.session_state.strategy),
    )

    col_start, col_stop = st.columns(2)

    with col_start:
        if st.button("Start bot", use_container_width=True):
            st.session_state.bot_running = True

            with get_db() as conn:
                conn.execute(
                    """
                    UPDATE bot_state
                    SET auto_trade=?, risk=?, strategy=?
                    WHERE username=?
                    """,
                    (
                        int(st.session_state.auto_trade),
                        st.session_state.risk,
                        st.session_state.strategy,
                        username,
                    ),
                )
                conn.commit()

            st.rerun()

    with col_stop:
        if st.button("Stop bot", use_container_width=True):
            st.session_state.bot_running = False
            st.session_state.auto_trade = False

            with get_db() as conn:
                conn.execute(
                    "UPDATE bot_state SET auto_trade=0 WHERE username=?",
                    (username,),
                )
                conn.commit()

            st.rerun()

    st.caption(
        "These controls change the demo state only. "
        "A persistent autonomous trading worker is not configured."
    )

    st.divider()

    with st.expander("Change password"):
        with st.form("password_form"):
            old_password = st.text_input(
                "Current password", type="password"
            )
            new_password = st.text_input(
                "New password", type="password"
            )
            confirm_new_password = st.text_input(
                "Confirm new password", type="password"
            )
            update_password = st.form_submit_button("Update password")

        if update_password:
            if new_password != confirm_new_password:
                st.error("The new passwords do not match.")
            else:
                success, message = change_password(
                    username, old_password, new_password
                )
                if success:
                    st.success(message)
                else:
                    st.error(message)

    if st.session_state.role == "admin":
        with st.expander("Admin: user management"):
            users = get_users()
            st.dataframe(
                users,
                hide_index=True,
                use_container_width=True,
            )

            removable_users = users.loc[
                users["username"] != "admin", "username"
            ].tolist()

            selected_user = st.selectbox(
                "Select user to remove",
                [""] + removable_users,
            )

            if st.button(
                "Remove selected user",
                disabled=not selected_user,
                use_container_width=True,
            ):
                remove_user(selected_user)
                st.success("User removed.")
                st.rerun()

    if st.button("Sign out", use_container_width=True):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()


# ============================================================
# PAGE HEADER
# ============================================================

st.title(page)
st.caption(
    f"Last updated: {datetime.now().strftime('%d %b %Y, %H:%M:%S')}"
)

st.markdown(
    """
    <div class="saim-notice">
        <strong>Demo mode:</strong> Market rates, AI signals, forecasts,
        and profit/loss results are simulated. No real broker orders are placed.
    </div>
    """,
    unsafe_allow_html=True,
)

balance = get_balance(username)


# ============================================================
# DASHBOARD
# ============================================================

if page == "Dashboard":
    prices = get_market()
    signals = run_ai(prices, st.session_state.strategy)
    history = get_trade_history(username)

    total_pnl = (
        float(history["pnl"].sum()) if not history.empty else 0.0
    )

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Account balance", f"${balance:,.2f}")
    c2.metric(
        "Bot status",
        "Running" if st.session_state.bot_running else "Stopped",
    )
    c3.metric("Risk setting", f"{st.session_state.risk}%")
    c4.metric("Recorded trades", len(history))

    left, right = st.columns([1.35, 1])

    with left:
        st.subheader("Market overview")

        market_df = pd.DataFrame({
            "Currency": list(prices.keys()),
            "Indicative rate": list(prices.values()),
        })

        fig = px.bar(
            market_df,
            x="Currency",
            y="Indicative rate",
            title="Simulated currency rates",
            template="plotly_dark",
        )

        fig.update_layout(
            margin=dict(l=10, r=10, t=55, b=10),
            showlegend=False,
        )

        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.subheader("Latest AI signals")

        signal_df = pd.DataFrame([
            {
                "Currency": currency,
                "Signal": details["signal"],
                "Confidence": f'{details["confidence"]:.1f}%',
            }
            for currency, details in signals.items()
        ])

        st.dataframe(
            signal_df,
            hide_index=True,
            use_container_width=True,
        )

        st.metric("Total simulated P/L", f"${total_pnl:,.2f}")

    st.subheader("Recent trades")

    if history.empty:
        st.info("No trades have been recorded yet.")
    else:
        st.dataframe(
            history.head(5),
            hide_index=True,
            use_container_width=True,
        )


# ============================================================
# MARKET ANALYSIS
# ============================================================

elif page == "Market Analysis":
    st.subheader("Currency rates")
    st.caption("These are demonstration values, not live exchange rates.")

    prices = get_market()

    market_df = pd.DataFrame({
        "Currency": list(prices.keys()),
        "Indicative rate": list(prices.values()),
    })

    fig = px.bar(
        market_df,
        x="Currency",
        y="Indicative rate",
        color="Currency",
        title="Simulated currency rates",
        template="plotly_dark",
    )

    fig.update_layout(margin=dict(l=10, r=10, t=55, b=10))

    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(
        market_df,
        hide_index=True,
        use_container_width=True,
    )

    if st.button("Refresh rates"):
        st.rerun()


# ============================================================
# AI SIGNALS
# ============================================================

elif page == "AI Signals":
    st.subheader("Signal analysis")
    st.write(f"Selected strategy: **{st.session_state.strategy}**")

    ai = run_ai(get_market(), st.session_state.strategy)

    ai_df = pd.DataFrame([
        {
            "Currency": currency,
            "Signal": details["signal"],
            "Confidence (%)": details["confidence"],
            "RSI (simulated)": details["rsi"],
            "Indicative forecast": details["forecast"],
        }
        for currency, details in ai.items()
    ])

    st.dataframe(
        ai_df,
        hide_index=True,
        use_container_width=True,
    )

    c1, c2, c3 = st.columns(3)

    c1.metric("Buy signals", int((ai_df["Signal"] == "BUY").sum()))
    c2.metric("Sell signals", int((ai_df["Signal"] == "SELL").sum()))
    c3.metric("Hold signals", int((ai_df["Signal"] == "HOLD").sum()))

    if st.button("Generate new signals"):
        st.rerun()


# ============================================================
# PRICE FORECASTS
# ============================================================

elif page == "Price Forecasts":
    st.subheader("Currency forecast")

    st.caption(
        "Forecasts are randomly generated illustrations, not financial advice."
    )

    c1, c2 = st.columns(2)

    with c1:
        currency = st.selectbox("Currency", list(CURRENCIES.keys()))

    with c2:
        days = st.slider("Forecast period (days)", 1, 30, 7)

    predictions = forecast_currency(currency, days)

    forecast_df = pd.DataFrame({
        "Day": range(1, days + 1),
        "Simulated value": predictions,
    })

    fig = px.line(
        forecast_df,
        x="Day",
        y="Simulated value",
        markers=True,
        title=f"{currency} forecast illustration",
        template="plotly_dark",
    )

    fig.update_layout(margin=dict(l=10, r=10, t=55, b=10))

    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(
        forecast_df,
        hide_index=True,
        use_container_width=True,
    )

    if st.button("Regenerate forecast"):
        st.rerun()


# ============================================================
# MANUAL TRADING
# ============================================================

elif page == "Manual Trading":
    st.subheader("Record a simulated trade")

    st.caption(
        "This records a demonstration trade in your database. "
        "It does not send an order to a broker."
    )

    with st.container(border=True):
        with st.form("trade_form"):
            symbol = st.selectbox("Currency", list(CURRENCIES.keys()))

            action = st.radio(
                "Action",
                ["BUY", "SELL"],
                horizontal=True,
            )

            current_price = get_market()[symbol]
            st.metric("Indicative price", f"{current_price:,.2f}")

            submitted = st.form_submit_button(
                "Record simulated trade",
                type="primary",
                use_container_width=True,
            )

        if submitted:
            pnl, new_balance = execute_trade(
                username,
                symbol,
                action,
                current_price,
                st.session_state.risk,
                balance,
            )

            st.success(
                f"Trade recorded. Simulated P/L: ${pnl:,.2f}. "
                f"New balance: ${new_balance:,.2f}."
            )

            st.rerun()


# ============================================================
# TRADE HISTORY
# ============================================================

elif page == "Trade History":
    st.subheader("Trade ledger")

    history = get_trade_history(username)

    if history.empty:
        st.info("There are no recorded trades for this account.")

    else:
        total_pnl = float(history["pnl"].sum())
        profitable = int((history["pnl"] > 0).sum())

        c1, c2, c3 = st.columns(3)

        c1.metric("Total trades", len(history))
        c2.metric("Net simulated P/L", f"${total_pnl:,.2f}")
        c3.metric("Profitable trades", profitable)

        st.dataframe(
            history,
            hide_index=True,
            use_container_width=True,
        )

        chronological = history.sort_values("id").copy()
        chronological["Cumulative P/L"] = (
            chronological["pnl"].cumsum()
        )

        fig = px.line(
            chronological,
            x="id",
            y="Cumulative P/L",
            markers=True,
            title="Cumulative simulated profit and loss",
            template="plotly_dark",
        )

        fig.update_layout(margin=dict(l=10, r=10, t=55, b=10))

        st.plotly_chart(fig, use_container_width=True)

        csv_data = history.to_csv(index=False).encode("utf-8")

        st.download_button(
            "Export trade history as CSV",
            data=csv_data,
            file_name=f"saim_trades_{username}.csv",
            mime="text/csv",
        )


# ============================================================
# SETTINGS
# ============================================================

elif page == "Settings":
    st.subheader("Account settings")

    left, right = st.columns(2)

    with left:
        st.markdown("#### Account details")
        st.write(f"**Username:** {username}")
        st.write(f"**Role:** {st.session_state.role}")
        st.write(f"**Balance:** ${balance:,.2f}")

    with right:
        st.markdown("#### Bot configuration")
        st.write(
            f"**Status:** "
            f"{'Running' if st.session_state.bot_running else 'Stopped'}"
        )
        st.write(
            f"**Auto-trade setting:** "
            f"{'Enabled' if st.session_state.auto_trade else 'Disabled'}"
        )
        st.write(f"**Strategy:** {st.session_state.strategy}")
        st.write(f"**Risk:** {st.session_state.risk}%")

    st.divider()

    st.markdown("#### Service status")
    st.write("Market data: Simulated")
    st.write("AI signal engine: Demonstration logic")
    st.write("Broker connection: Not configured")
    st.write("Persistent autonomous worker: Not configured")
    st.write(f"Database path: `{DB_PATH}`")

    st.warning(
        "On a new, empty database, the initial administrator login is "
        "admin / admin123. Change this password immediately. "
        "Do not use this demo authentication setup for a public production service."
    )


# ============================================================
# FOOTER
# ============================================================

st.divider()
st.caption(
    "Saim Trading Dashboard | Demonstration environment | "
    "No real market orders are placed"
)
