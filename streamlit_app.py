
import os
import sqlite3
import hashlib
from datetime import datetime, timezone
from contextlib import contextmanager

import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from streamlit_autorefresh import st_autorefresh

try:
    import yfinance as yf
except ImportError:
    yf = None


# ============================================================
# CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="SAIM | Paper Trading",
    page_icon="S",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_PATH = os.environ.get("DB_PATH", "saim.db")
STARTING_BALANCE = 10_000.0
COMMISSION = 0.001
REFRESH_MS = 30_000

ASSETS = {
    "Bitcoin": "BTC-USD",
    "Ethereum": "ETH-USD",
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "Gold": "GC=F",
    "USD/UGX": "USDUGX=X",
    "USD/KES": "USDKES=X",
    "USD/TZS": "USDTZS=X",
    "USD/RWF": "USDRWF=X",
    "USD/ETB": "USDETB=X",
    "USD/SSP": "USDSSP=X",
}

TIMEFRAMES = {
    "1 hour": ("60d", "1h"),
    "4 hours": ("180d", "1h"),
    "1 day": ("2y", "1d"),
}

EAST_AFRICAN = {
    "USDUGX=X", "USDKES=X", "USDTZS=X",
    "USDRWF=X", "USDETB=X", "USDSSP=X",
}


# ============================================================
# STYLE
# ============================================================

st.markdown(
    """
    <style>
    .stApp { background: #0b1020; }
    [data-testid="stSidebar"] { background: #10172a; }
    .block-container {
        max-width: 1400px;
        padding-top: 1.5rem;
    }
    .saim-title {
        font-size: 2.4rem;
        font-weight: 800;
        letter-spacing: -1px;
    }
    .muted { color: #94a3b8; }
    .paper {
        background: #172235;
        border: 1px solid #355070;
        border-radius: 10px;
        padding: 12px 16px;
        margin: 8px 0 18px 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# DATABASE
# ============================================================

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def password_hash(password):
    # Kept compatible with the existing SAIM database.
    # For a production service, use Argon2 or scrypt.
    return hashlib.sha256(
        password.encode("utf-8")
    ).hexdigest()


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user'
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS balance (
                username TEXT PRIMARY KEY,
                amount REAL NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                username TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                qty REAL NOT NULL,
                entry REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,
                opened_at TEXT NOT NULL,
                PRIMARY KEY (username, symbol)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                time TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                qty REAL NOT NULL,
                entry REAL NOT NULL,
                exit REAL,
                pnl REAL,
                status TEXT NOT NULL
            )
        """)
        conn.commit()


init_db()


# ============================================================
# ACCOUNTS
# ============================================================

def authenticate(username, password):
    with db() as conn:
        row = conn.execute(
            """
            SELECT role FROM users
            WHERE username = ? AND password = ?
            """,
            (username.strip(), password_hash(password)),
        ).fetchone()

    if row:
        return True, row["role"]

    return False, None


def register(username, password):
    username = username.strip()

    if not username:
        return False, "Enter a username."

    if len(password) < 8:
        return False, "Password must contain at least 8 characters."

    try:
        with db() as conn:
            conn.execute(
                """
                INSERT INTO users (username, password, role)
                VALUES (?, ?, 'user')
                """,
                (username, password_hash(password)),
            )

            conn.execute(
                """
                INSERT INTO balance (username, amount)
                VALUES (?, ?)
                """,
                (username, STARTING_BALANCE),
            )
            conn.commit()

        return True, "Account created. You can now sign in."

    except sqlite3.IntegrityError:
        return False, "That username already exists."


def get_balance(username):
    with db() as conn:
        row = conn.execute(
            "SELECT amount FROM balance WHERE username = ?",
            (username,),
        ).fetchone()

    return float(row["amount"]) if row else STARTING_BALANCE


def get_position(username, symbol):
    with db() as conn:
        return conn.execute(
            """
            SELECT * FROM positions
            WHERE username = ? AND symbol = ?
            """,
            (username, symbol),
        ).fetchone()


def get_positions(username):
    with db() as conn:
        return pd.read_sql_query(
            """
            SELECT * FROM positions
            WHERE username = ?
            ORDER BY opened_at DESC
            """,
            conn,
            params=[username],
        )


def get_trades(username):
    with db() as conn:
        return pd.read_sql_query(
            """
            SELECT id, time, symbol, side, qty, entry,
                   exit, pnl, status
            FROM paper_trades
            WHERE username = ?
            ORDER BY id DESC
            """,
            conn,
            params=[username],
        )


# ============================================================
# MARKET DATA
# ============================================================

@st.cache_data(ttl=30, show_spinner=False)
def load_market(symbol, period, interval):
    """Fetch provider data. An empty DataFrame means unavailable."""

    if yf is None:
        return pd.DataFrame()

    try:
        data = yf.download(
            symbol,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=False,
            threads=False,
        )

        if data.empty:
            return pd.DataFrame()

        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        needed = ["Open", "High", "Low", "Close", "Volume"]

        for column in needed:
            if column not in data.columns:
                data[column] = 0.0

        return data[needed].dropna(
            subset=["Open", "High", "Low", "Close"]
        ).copy()

    except Exception:
        return pd.DataFrame()


def demo_market(symbol, rows=250):
    """
    Simulated fallback data.
    This must never be presented as a live market quote.
    """
    import numpy as np

    rng = np.random.default_rng(abs(hash(symbol)) % (2**32))

    starting_prices = {
        "BTC-USD": 65000,
        "ETH-USD": 2500,
        "EURUSD=X": 1.17,
        "GBPUSD=X": 1.34,
        "JPY=X": 151,
        "GC=F": 4300,
        "USDUGX=X": 3600,
        "USDKES=X": 130,
        "USDTZS=X": 2450,
        "USDRWF=X": 1450,
        "USDETB=X": 150,
        "USDSSP=X": 550,
    }

    start = starting_prices.get(symbol, 100)

    returns = rng.normal(0, 0.008, rows)
    close = start * np.exp(returns.cumsum())
    high = close * (1 + rng.uniform(0.0005, 0.012, rows))
    low = close * (1 - rng.uniform(0.0005, 0.012, rows))
    open_price = (high + low) / 2

    index = pd.date_range(
        end=pd.Timestamp.now(tz="UTC"),
        periods=rows,
        freq="h",
    )

    return pd.DataFrame(
        {
            "Open": open_price,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": rng.integers(1000, 100000, rows),
        },
        index=index,
    )


def get_data(symbol, timeframe):
    period, interval = TIMEFRAMES[timeframe]
    data = load_market(symbol, period, interval)

    if data.empty:
        st.session_state["market_data_mode"] = "DEMO DATA"
        return demo_market(symbol)

    st.session_state["market_data_mode"] = "PROVIDER DATA"
    return data


# ============================================================
# INDICATORS AND SIGNALS
# ============================================================

def add_indicators(data):
    result = data.copy()

    result["EMA20"] = result["Close"].ewm(
        span=20, adjust=False
    ).mean()

    result["EMA50"] = result["Close"].ewm(
        span=50, adjust=False
    ).mean()

    delta = result["Close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()

    rs = gain / loss.replace(0, float("nan"))
    result["RSI"] = 100 - (100 / (1 + rs))

    true_range = pd.concat(
        [
            result["High"] - result["Low"],
            (result["High"] - result["Close"].shift()).abs(),
            (result["Low"] - result["Close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)

    result["ATR"] = true_range.rolling(14).mean()

    return result


def make_signal(data):
    clean = add_indicators(data).dropna()
    price = float(data["Close"].iloc[-1])

    if len(clean) < 60:
        return {
            "signal": "WAIT",
            "score": 0,
            "confidence": 50,
            "rsi": 50,
            "atr": 0,
            "price": price,
            "ema20": price,
            "ema50": price,
        }

    last = clean.iloc[-1]
    score = 0

    if last["EMA20"] > last["EMA50"]:
        score += 2
    elif last["EMA20"] < last["EMA50"]:
        score -= 2

    if last["RSI"] > 55:
        score += 1
    elif last["RSI"] < 45:
        score -= 1

    if last["Close"] > clean["Close"].iloc[-4]:
        score += 1
    elif last["Close"] < clean["Close"].iloc[-4]:
        score -= 1

    if score >= 3:
        signal = "BUY"
    elif score <= -3:
        signal = "SELL"
    else:
        signal = "WAIT"

    return {
        "signal": signal,
        "score": score,
        "confidence": min(95, 50 + abs(score) * 10),
        "rsi": float(last["RSI"]),
        "atr": float(last["ATR"]),
        "price": float(last["Close"]),
        "ema20": float(last["EMA20"]),
        "ema50": float(last["EMA50"]),
    }


def price_decimals(symbol):
    if symbol in {"BTC-USD", "ETH-USD", "GC=F"}:
        return 2
    if symbol in {"EURUSD=X", "GBPUSD=X"}:
        return 5
    if symbol == "JPY=X":
        return 3
    if symbol in EAST_AFRICAN:
        return 2
    return 4


def fmt_price(value, symbol):
    return f"{float(value):,.{price_decimals(symbol)}f}"


# ============================================================
# PAPER TRADING
# ============================================================

def position_levels(price, atr, side):
    if side == "BUY":
        stop = max(price - 1.5 * atr, 0.000001)
        target = max(price + 3 * atr, 0.000001)
    else:
        stop = max(price + 1.5 * atr, 0.000001)
        target = max(price - 3 * atr, 0.000001)

    return stop, target


def calculate_qty(balance, price, stop, risk_pct):
    risk_money = balance * risk_pct / 100
    distance = abs(price - stop)

    if distance <= 0 or price <= 0:
        return 0.0

    return max(
        min(risk_money / distance, balance * 0.25 / price),
        0.0,
    )


def open_position(username, symbol, side, price, stop, target, qty):
    if get_position(username, symbol):
        return False, "A position is already open for this market."

    balance = get_balance(username)
    fee = qty * price * COMMISSION

    if qty <= 0 or fee >= balance:
        return False, "Insufficient paper balance for this order."

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with db() as conn:
        conn.execute(
            """
            INSERT INTO positions
            (username, symbol, side, qty, entry,
             stop_loss, take_profit, opened_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username, symbol, side, qty, price,
                stop, target, now,
            ),
        )

        conn.execute(
            "UPDATE balance SET amount = amount - ? WHERE username = ?",
            (fee, username),
        )

        conn.execute(
            """
            INSERT INTO paper_trades
            (username, time, symbol, side, qty,
             entry, exit, pnl, status)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 'OPEN')
            """,
            (username, now, symbol, side, qty, price, -fee),
        )

        conn.commit()

    return True, f"Paper {side} position opened."


def close_position(username, symbol, price):
    position = get_position(username, symbol)

    if not position:
        return False, "No open position."

    entry = float(position["entry"])
    qty = float(position["qty"])
    side = position["side"]

    if side == "BUY":
        gross_pnl = (price - entry) * qty
    else:
        gross_pnl = (entry - price) * qty

    exit_fee = price * qty * COMMISSION
    pnl = gross_pnl - exit_fee

    with db() as conn:
        conn.execute(
            "UPDATE balance SET amount = amount + ? WHERE username = ?",
            (pnl, username),
        )

        conn.execute(
            """
            UPDATE paper_trades
            SET exit = ?, pnl = pnl + ?, status = 'CLOSED'
            WHERE username = ? AND symbol = ? AND status = 'OPEN'
            """,
            (price, pnl, username, symbol),
        )

        conn.execute(
            "DELETE FROM positions WHERE username = ? AND symbol = ?",
            (username, symbol),
        )

        conn.commit()

    return True, pnl


def check_stops(username, symbol, price):
    position = get_position(username, symbol)

    if not position:
        return None

    side = position["side"]
    stop = float(position["stop_loss"])
    target = float(position["take_profit"])

    if side == "BUY":
        hit = price <= stop or price >= target
    else:
        hit = price >= stop or price <= target

    if hit:
        close_position(username, symbol, price)
        return "Paper position closed at stop-loss or take-profit."

    return None


# ============================================================
# SESSION STATE
# ============================================================

defaults = {
    "logged": False,
    "username": "",
    "role": "",
    "timeframe": "1 hour",
    "risk": 1.0,
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# SIGN-IN
# ============================================================

if not st.session_state.logged:
    _, center, _ = st.columns([1, 1.2, 1])

    with center:
        st.markdown(
            '<div class="saim-title">SAIM</div>'
            '<div class="muted">Paper Trading Intelligence</div>',
            unsafe_allow_html=True,
        )

        login_tab, register_tab = st.tabs(
            ["Sign in", "Create account"]
        )

        with login_tab:
            with st.form("login_form"):
                login_username = st.text_input("Username")
                login_password = st.text_input(
                    "Password", type="password"
                )
                login_submit = st.form_submit_button(
                    "Sign in",
                    type="primary",
                    use_container_width=True,
                )

            if login_submit:
                ok, role = authenticate(
                    login_username, login_password
                )

                if ok:
                    st.session_state.logged = True
                    st.session_state.username = login_username.strip()
                    st.session_state.role = role
                    st.rerun()
                else:
                    st.error("Incorrect username or password.")

        with register_tab:
            with st.form("register_form"):
                new_username = st.text_input(
                    "Username", key="new_username"
                )
                new_password = st.text_input(
                    "Password (minimum 8 characters)",
                    type="password",
                    key="new_password",
                )
                confirm_password = st.text_input(
                    "Confirm password", type="password"
                )
                register_submit = st.form_submit_button(
                    "Create account",
                    use_container_width=True,
                )

            if register_submit:
                if new_password != confirm_password:
                    st.error("Passwords do not match.")
                else:
                    ok, message = register(
                        new_username, new_password
                    )
                    if ok:
                        st.success(message)
                    else:
                        st.error(message)

        st.caption(
            "Paper trading only. No real broker orders are sent."
        )

    st.stop()


# ============================================================
# SIDEBAR
# ============================================================

username = st.session_state.username

with st.sidebar:
    st.markdown(
        '<div class="saim-title">SAIM</div>',
        unsafe_allow_html=True,
    )
    st.caption(f"Account: {username}")
    st.divider()

    asset_name = st.selectbox("Market", list(ASSETS.keys()))
    symbol = ASSETS[asset_name]

    if symbol in EAST_AFRICAN:
        st.caption("East African FX · indicative data")

    st.session_state.timeframe = st.selectbox(
        "Timeframe",
        list(TIMEFRAMES.keys()),
        index=list(TIMEFRAMES.keys()).index(
            st.session_state.timeframe
        ),
    )

    st.session_state.risk = st.slider(
        "Risk per trade (%)",
        min_value=0.25,
        max_value=2.0,
        value=float(st.session_state.risk),
        step=0.25,
    )

    page = st.radio(
        "Navigation",
        ["Dashboard", "Trade", "History"],
        label_visibility="collapsed",
    )

    st.divider()
    st.caption("UGX · KES · TZS · RWF · ETB · SSP")

    if st.button("Sign out", use_container_width=True):
        st.session_state.clear()
        st.rerun()


# ============================================================
# AUTO-REFRESH AND CURRENT MARKET
# ============================================================

# Requires streamlit-autorefresh in requirements.txt.
st_autorefresh(
    interval=REFRESH_MS,
    limit=None,
    key="saim_live_refresh",
)

data = add_indicators(
    get_data(symbol, st.session_state.timeframe)
)

last_price = float(data["Close"].iloc[-1])
signal = make_signal(data)
balance = get_balance(username)

stop_message = check_stops(username, symbol, last_price)

if stop_message:
    st.toast(stop_message)

if st.session_state.get("market_data_mode") == "DEMO DATA":
    st.warning(
        "DEMO DATA ONLY — the provider returned no quotes. "
        "Prices displayed are simulated, not live."
    )
else:
    st.caption(
        "Market data: provider feed. The dashboard refreshes every "
        "30 seconds while open. Provider quotes may be delayed."
    )


# ============================================================
# DASHBOARD
# ============================================================

if page == "Dashboard":
    st.markdown(
        '<div class="saim-title">Dashboard</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="paper"><strong>PAPER MODE</strong> — '
        'all trades are simulated. No real money is used.</div>',
        unsafe_allow_html=True,
    )

    open_position_row = get_position(username, symbol)
    trades = get_trades(username)

    unrealized = 0.0

    if open_position_row:
        entry = float(open_position_row["entry"])
        qty = float(open_position_row["qty"])
        side = open_position_row["side"]

        if side == "BUY":
            unrealized = (last_price - entry) * qty
        else:
            unrealized = (entry - last_price) * qty

    closed = (
        trades[trades["status"] == "CLOSED"]
        if not trades.empty else trades
    )

    realized = (
        float(closed["pnl"].sum())
        if not closed.empty else 0.0
    )

    wins = (
        int((closed["pnl"] > 0).sum())
        if not closed.empty else 0
    )

    win_rate = (
        wins / len(closed) * 100
        if len(closed) else 0
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cash balance", f"${balance:,.2f}")
    c2.metric("Unrealized P/L", f"${unrealized:,.2f}")
    c3.metric("Realized P/L", f"${realized:,.2f}")
    c4.metric("Win rate", f"{win_rate:.1f}%")

    st.subheader(f"{asset_name} · {st.session_state.timeframe}")

    if symbol in EAST_AFRICAN:
        st.caption(
            "Indicative local currency units per USD. "
            "Provider availability varies by currency."
        )

    chart = go.Figure()

    chart.add_trace(
        go.Candlestick(
            x=data.index,
            open=data["Open"],
            high=data["High"],
            low=data["Low"],
            close=data["Close"],
            name="Price",
        )
    )

    chart.add_trace(
        go.Scatter(
            x=data.index,
            y=data["EMA20"],
            name="EMA 20",
        )
    )

    chart.add_trace(
        go.Scatter(
            x=data.index,
            y=data["EMA50"],
            name="EMA 50",
        )
    )

    if open_position_row:
        chart.add_hline(
            y=float(open_position_row["entry"]),
            annotation_text="Entry",
        )
        chart.add_hline(
            y=float(open_position_row["stop_loss"]),
            annotation_text="Stop",
        )
        chart.add_hline(
            y=float(open_position_row["take_profit"]),
            annotation_text="Target",
        )

    chart.update_layout(
        template="plotly_dark",
        height=520,
        xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=20, b=10),
    )

    st.plotly_chart(chart, use_container_width=True)

    left, right = st.columns([1, 1.3])

    with left:
        st.subheader("SAIM Signal")

        if signal["signal"] == "BUY":
            st.success("BUY")
        elif signal["signal"] == "SELL":
            st.error("SELL")
        else:
            st.info("WAIT")

        st.metric("Indicator score", signal["confidence"])
        st.write(f'RSI: **{signal["rsi"]:.1f}**')
        st.write(f'EMA 20: **{signal["ema20"]:,.5f}**')
        st.write(f'EMA 50: **{signal["ema50"]:,.5f}**')
        st.caption(
            "Rule-based indicators are not guaranteed predictions."
        )

    with right:
        st.subheader("Current Position")

        if open_position_row:
            st.write(
                f"**{open_position_row['side']}** · "
                f"{float(open_position_row['qty']):.6f} {symbol}"
            )
            st.write(
                f"Entry: **{float(open_position_row['entry']):,.5f}**"
            )
            st.write(
                f"Stop: **{float(open_position_row['stop_loss']):,.5f}**"
            )
            st.write(
                f"Target: **{float(open_position_row['take_profit']):,.5f}**"
            )
            st.metric("Position P/L", f"${unrealized:,.2f}")
        else:
            st.info("No open position for this market.")


# ============================================================
# PAPER TRADING PAGE
# ============================================================

elif page == "Trade":
    st.markdown(
        '<div class="saim-title">Paper Trade</div>',
        unsafe_allow_html=True,
    )

    st.caption(
        "Orders are simulated. Risk sizing uses the stop-loss distance."
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Current price", fmt_price(last_price, symbol))
    c2.metric("Signal", signal["signal"])
    c3.metric("Risk per trade", f"{st.session_state.risk:.2f}%")

    position = get_position(username, symbol)

    if position:
        st.warning(
            f"You already have an open {position['side']} position."
        )

        entry = float(position["entry"])
        qty = float(position["qty"])
        side = position["side"]

        pnl = (
            (last_price - entry) * qty
            if side == "BUY"
            else (entry - last_price) * qty
        )

        st.write(
            f"Entry **{fmt_price(entry, symbol)}** · "
            f"Current **{fmt_price(last_price, symbol)}** · "
            f"Quantity **{qty:.6f}**"
        )

        st.metric("Unrealized P/L", f"${pnl:,.2f}")

        if st.button(
            "Close paper position",
            type="primary",
            use_container_width=True,
        ):
            ok, result = close_position(
                username, symbol, last_price
            )

            if ok:
                st.success(
                    f"Paper position closed. P/L: ${result:,.2f}"
                )
                st.rerun()
            else:
                st.error(result)

    elif signal["atr"] <= 0:
        st.error("Not enough data to calculate risk levels.")

    else:
        side = st.radio(
            "Direction",
            ["BUY", "SELL"],
            horizontal=True,
        )

        stop, target = position_levels(
            last_price, signal["atr"], side
        )

        qty = calculate_qty(
            balance,
            last_price,
            stop,
            st.session_state.risk,
        )

        risk_money = balance * st.session_state.risk / 100
        reward_money = abs(target - last_price) * qty

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Entry", fmt_price(last_price, symbol))
        c2.metric("Stop loss", fmt_price(stop, symbol))
        c3.metric("Take profit", fmt_price(target, symbol))
        c4.metric("Risk/reward", "1 : 2")

        st.write(
            f"Suggested size: **{qty:.6f}** · "
            f"Planned risk: **${risk_money:,.2f}** · "
            f"Potential reward: **${reward_money:,.2f}**"
        )

        if st.button(
            f"Open paper {side}",
            type="primary",
            use_container_width=True,
        ):
            ok, message = open_position(
                username,
                symbol,
                side,
                last_price,
                stop,
                target,
                qty,
            )

            if ok:
                st.success(message)
                st.rerun()
            else:
                st.error(message)

    st.divider()
    st.subheader("Trade indicators")

    trend = (
        "bullish"
        if signal["ema20"] > signal["ema50"]
        else "bearish"
    )

    st.write(
        f"Trend: **{trend}** · "
        f"RSI: **{signal['rsi']:.1f}** · "
        f"Signal: **{signal['signal']}**"
    )


# ============================================================
# TRADE HISTORY
# ============================================================

else:
    st.markdown(
        '<div class="saim-title">Trade History</div>',
        unsafe_allow_html=True,
    )

    trades = get_trades(username)

    if trades.empty:
        st.info("No paper trades yet.")

    else:
        closed = trades[trades["status"] == "CLOSED"]

        total = (
            float(closed["pnl"].sum())
            if not closed.empty else 0.0
        )

        wins = (
            int((closed["pnl"] > 0).sum())
            if not closed.empty else 0
        )

        win_rate = (
            wins / len(closed) * 100
            if len(closed) else 0
        )

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Closed trades", len(closed))
        c2.metric("Net P/L", f"${total:,.2f}")
        c3.metric("Wins", wins)
        c4.metric("Win rate", f"{win_rate:.1f}%")

        display_trades = trades.copy()

        for column, decimals in {
            "pnl": 2,
            "entry": 5,
            "exit": 5,
        }.items():
            if column in display_trades.columns:
                display_trades[column] = pd.to_numeric(
                    display_trades[column],
                    errors="coerce",
                ).round(decimals)

        st.dataframe(
            display_trades,
            hide_index=True,
            use_container_width=True,
        )

        if not closed.empty:
            curve = closed.sort_values("id").copy()
            curve["Cumulative P/L"] = curve["pnl"].cumsum()

            figure = go.Figure()

            figure.add_trace(
                go.Scatter(
                    x=curve["id"],
                    y=curve["Cumulative P/L"],
                    mode="lines+markers",
                    name="Cumulative P/L",
                )
            )

            figure.update_layout(
                template="plotly_dark",
                height=350,
                margin=dict(l=10, r=10, t=20, b=10),
            )

            st.plotly_chart(
                figure,
                use_container_width=True,
            )

        st.download_button(
            "Export CSV",
            data=trades.to_csv(index=False).encode("utf-8"),
            file_name=f"saim_trades_{username}.csv",
            mime="text/csv",
        )


# ============================================================
# FOOTER
# ============================================================

st.divider()
st.caption(
    "SAIM · Paper trading only · No real broker orders are placed · "
    "Market data may be delayed"
)
