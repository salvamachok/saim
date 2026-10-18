import os
import sqlite3
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
import plotly.graph_objects as go

try:
    import yfinance as yf
except ImportError:
    yf = None


# ============================================================
# SAIM - SIMPLE REALISTIC PAPER TRADING APP
# ============================================================
# Live market data when yfinance is available.
# All orders remain PAPER TRADES. No broker orders are sent.
# ============================================================

st.set_page_config(
    page_title="SAIM | Paper Trading",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_PATH = os.environ.get("DB_PATH", "saim.db")
STARTING_BALANCE = 10_000.0
COMMISSION = 0.001  # 0.10% per side
DEFAULT_RISK = 1.0


# -----------------------------
# Simple styling
# -----------------------------
st.markdown(
    """
    <style>
    .stApp { background: #0b1020; }
    [data-testid="stSidebar"] { background: #10172a; }
    .block-container { max-width: 1400px; padding-top: 1.5rem; }
    .saim-title { font-size: 2.4rem; font-weight: 800; letter-spacing: -1px; }
    .muted { color: #94a3b8; }
    .card {
        background: #131d32;
        border: 1px solid #283650;
        border-radius: 14px;
        padding: 16px;
        margin-bottom: 12px;
    }
    .paper {
        background: #172235;
        border: 1px solid #355070;
        border-radius: 10px;
        padding: 10px 14px;
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


def password_hash(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


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
                PRIMARY KEY(username, symbol)
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

        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count == 0:
            conn.execute(
                "INSERT INTO users(username,password,role) VALUES(?,?,?)",
                ("admin", password_hash("admin123"), "admin"),
            )
            conn.execute(
                "INSERT INTO balance(username,amount) VALUES(?,?)",
                ("admin", STARTING_BALANCE),
            )

        # Give existing users a balance if they came from the old app.
        users = conn.execute("SELECT username FROM users").fetchall()
        for row in users:
            conn.execute(
                "INSERT OR IGNORE INTO balance(username,amount) VALUES(?,?)",
                (row["username"], STARTING_BALANCE),
            )
        conn.commit()


init_db()


def authenticate(username, password):
    with db() as conn:
        row = conn.execute(
            "SELECT role FROM users WHERE username=? AND password=?",
            (username.strip(), password_hash(password)),
        ).fetchone()
    return (True, row["role"]) if row else (False, None)


def register(username, password):
    username = username.strip()
    if not username:
        return False, "Enter a username."
    if len(password) < 8:
        return False, "Password must contain at least 8 characters."

    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO users(username,password,role) VALUES(?,?,?)",
                (username, password_hash(password), "user"),
            )
            conn.execute(
                "INSERT INTO balance(username,amount) VALUES(?,?)",
                (username, STARTING_BALANCE),
            )
            conn.commit()
        return True, "Account created."
    except sqlite3.IntegrityError:
        return False, "That username already exists."


def get_balance(username):
    with db() as conn:
        row = conn.execute(
            "SELECT amount FROM balance WHERE username=?", (username,)
        ).fetchone()
    return float(row["amount"]) if row else STARTING_BALANCE


def get_position(username, symbol):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM positions WHERE username=? AND symbol=?",
            (username, symbol),
        ).fetchone()


def get_positions(username):
    with db() as conn:
        return pd.read_sql_query(
            "SELECT * FROM positions WHERE username=? ORDER BY opened_at DESC",
            conn,
            params=[username],
        )


def get_trades(username):
    with db() as conn:
        return pd.read_sql_query(
            """
            SELECT id,time,symbol,side,qty,entry,exit,pnl,status
            FROM paper_trades
            WHERE username=?
            ORDER BY id DESC
            """,
            conn,
            params=[username],
        )


# ============================================================
# DISPLAY HELPERS
# ============================================================

def price_decimals(symbol):
    if symbol in {"BTC-USD", "ETH-USD"}:
        return 2
    if symbol in {"EURUSD=X", "GBPUSD=X"}:
        return 5
    if symbol == "JPY=X":
        return 3
    if symbol == "GC=F":
        return 2
    # East African FX pairs are generally easier to read with 2 decimals.
    if symbol in {"USDUGX=X", "USDKES=X", "USDTZS=X", "USDRWF=X", "USDETB=X", "USDSSP=X"}:
        return 2
    return 4


def fmt_price(value, symbol):
    return f"{float(value):,.{price_decimals(symbol)}f}"


def market_group(symbol):
    if symbol in {"USDUGX=X", "USDKES=X", "USDTZS=X", "USDRWF=X", "USDETB=X", "USDSSP=X"}:
        return "East African FX"
    return "Global Markets"


# ============================================================
# MARKET DATA
# ============================================================

ASSETS = {
    # Major markets
    "Bitcoin": "BTC-USD",
    "Ethereum": "ETH-USD",
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "Gold": "GC=F",

    # East African currencies
    "USD/UGX 🇺🇬": "USDUGX=X",
    "USD/KES 🇰🇪": "USDKES=X",
    "USD/TZS 🇹🇿": "USDTZS=X",
    "USD/RWF 🇷🇼": "USDRWF=X",
    "USD/ETB 🇪🇹": "USDETB=X",
    "USD/SSP 🇸🇸": "USDSSP=X",
}

TIMEFRAMES = {
    "1 hour": ("60d", "1h"),
    "4 hours": ("180d", "1h"),
    "1 day": ("2y", "1d"),
}


@st.cache_data(ttl=60)
def load_market(symbol, period="60d", interval="1h"):
    if yf is None:
        return pd.DataFrame()

    try:
        data = yf.download(
            symbol,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=False,
        )
        if data.empty:
            return pd.DataFrame()

        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        needed = ["Open", "High", "Low", "Close", "Volume"]
        for col in needed:
            if col not in data.columns:
                data[col] = 0.0

        data = data[needed].dropna(subset=["Open", "High", "Low", "Close"]).copy()
        data.index = pd.to_datetime(data.index)
        return data
    except Exception:
        return pd.DataFrame()


def demo_market(symbol, rows=250):
    """Fallback data so the app still runs without market-data access."""
    import numpy as np

    rng = np.random.default_rng(abs(hash(symbol)) % (2**32))
    start = {
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
    }.get(symbol, 100)

    returns = rng.normal(0, 0.008, rows)
    close = start * np.exp(returns.cumsum())
    high = close * (1 + rng.uniform(0.0005, 0.012, rows))
    low = close * (1 - rng.uniform(0.0005, 0.012, rows))
    open_ = (high + low) / 2
    volume = rng.integers(1000, 100000, rows)

    idx = pd.date_range(end=pd.Timestamp.utcnow(), periods=rows, freq="h")
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )


def get_data(symbol, timeframe):
    period, interval = TIMEFRAMES[timeframe]
    data = load_market(symbol, period, interval)
    if data.empty:
        return demo_market(symbol)
    return data


# ============================================================
# INDICATORS + SIGNAL
# ============================================================

def add_indicators(df):
    d = df.copy()

    d["EMA20"] = d["Close"].ewm(span=20, adjust=False).mean()
    d["EMA50"] = d["Close"].ewm(span=50, adjust=False).mean()

    delta = d["Close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, pd.NA)
    d["RSI"] = 100 - (100 / (1 + rs))

    tr = pd.concat(
        [
            d["High"] - d["Low"],
            (d["High"] - d["Close"].shift()).abs(),
            (d["Low"] - d["Close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    d["ATR"] = tr.rolling(14).mean()

    return d


def make_signal(df):
    d = add_indicators(df).dropna().copy()
    if len(d) < 60:
        return {"signal": "WAIT", "score": 0, "rsi": 50, "atr": 0}

    last = d.iloc[-1]
    score = 0

    # Trend
    if last["EMA20"] > last["EMA50"]:
        score += 2
    elif last["EMA20"] < last["EMA50"]:
        score -= 2

    # Momentum
    if last["RSI"] > 55:
        score += 1
    elif last["RSI"] < 45:
        score -= 1

    # Recent price direction
    if last["Close"] > d["Close"].iloc[-4]:
        score += 1
    elif last["Close"] < d["Close"].iloc[-4]:
        score -= 1

    if score >= 3:
        signal = "BUY"
    elif score <= -3:
        signal = "SELL"
    else:
        signal = "WAIT"

    confidence = min(95, 50 + abs(score) * 10)

    return {
        "signal": signal,
        "score": score,
        "confidence": confidence,
        "rsi": float(last["RSI"]),
        "atr": float(last["ATR"]),
        "price": float(last["Close"]),
        "ema20": float(last["EMA20"]),
        "ema50": float(last["EMA50"]),
    }


# ============================================================
# PAPER TRADING
# ============================================================

def position_levels(price, atr, side):
    # 1.5 ATR stop, 3 ATR target = approximately 1:2 reward/risk.
    if side == "BUY":
        sl = price - 1.5 * atr
        tp = price + 3.0 * atr
    else:
        sl = price + 1.5 * atr
        tp = price - 3.0 * atr
    return max(sl, 0.000001), max(tp, 0.000001)


def calculate_qty(balance, price, stop, risk_pct):
    risk_money = balance * (risk_pct / 100)
    distance = abs(price - stop)

    if distance <= 0 or price <= 0:
        return 0.0

    # For crypto this gives a natural fractional quantity.
    qty = risk_money / distance

    # Never use more than 25% of account notional in this simple app.
    max_notional = balance * 0.25
    qty = min(qty, max_notional / price)

    return max(qty, 0.0)


def open_position(username, symbol, side, price, stop, target, qty):
    balance = get_balance(username)
    notional = qty * price
    fee = notional * COMMISSION

    if fee >= balance:
        return False, "Not enough balance for fees."

    # Reserve the fee only; position P/L is marked to market.
    with db() as conn:
        conn.execute(
            """
            INSERT INTO positions
            (username,symbol,side,qty,entry,stop_loss,take_profit,opened_at)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                username,
                symbol,
                side,
                qty,
                price,
                stop,
                target,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        conn.execute(
            "UPDATE balance SET amount=? WHERE username=?",
            (balance - fee, username),
        )
        conn.execute(
            """
            INSERT INTO paper_trades
            (username,time,symbol,side,qty,entry,exit,pnl,status)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                username,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                symbol,
                side,
                qty,
                price,
                None,
                -fee,
                "OPEN",
            ),
        )
        conn.commit()

    return True, f"Paper {side} opened."


def close_position(username, symbol, price):
    pos = get_position(username, symbol)
    if not pos:
        return False, "No open position."

    entry = float(pos["entry"])
    qty = float(pos["qty"])
    side = pos["side"]

    gross = (price - entry) * qty if side == "BUY" else (entry - price) * qty
    exit_fee = price * qty * COMMISSION
    pnl = gross - exit_fee

    with db() as conn:
        conn.execute(
            "UPDATE balance SET amount=amount+? WHERE username=?",
            (pnl, username),
        )
        conn.execute(
            """
            UPDATE paper_trades
            SET exit=?, pnl=?, status='CLOSED'
            WHERE username=? AND symbol=? AND status='OPEN'
            """,
            (price, pnl, username, symbol),
        )
        conn.execute(
            "DELETE FROM positions WHERE username=? AND symbol=?",
            (username, symbol),
        )
        conn.commit()

    return True, pnl


def check_stops(username, symbol, price):
    pos = get_position(username, symbol)
    if not pos:
        return None

    side = pos["side"]
    sl = float(pos["stop_loss"])
    tp = float(pos["take_profit"])

    hit = (
        price <= sl or price >= tp
        if side == "BUY"
        else price >= sl or price <= tp
    )

    if hit:
        close_position(username, symbol, price)
        return "Position automatically closed at stop/target."

    return None


# ============================================================
# SESSION
# ============================================================

for key, value in {
    "logged": False,
    "username": "",
    "role": "",
    "timeframe": "1 hour",
    "risk": DEFAULT_RISK,
}.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# LOGIN
# ============================================================

if not st.session_state.logged:
    _, center, _ = st.columns([1, 1.2, 1])

    with center:
        st.markdown(
            '<div class="saim-title">SAIM</div>'
            '<div class="muted">Simple paper trading intelligence</div>',
            unsafe_allow_html=True,
        )
        st.write("")

        tab1, tab2 = st.tabs(["Sign in", "Create account"])

        with tab1:
            with st.form("login"):
                u = st.text_input("Username")
                p = st.text_input("Password", type="password")
                go = st.form_submit_button("Sign in", type="primary", use_container_width=True)

            if go:
                ok, role = authenticate(u, p)
                if ok:
                    st.session_state.logged = True
                    st.session_state.username = u.strip()
                    st.session_state.role = role
                    st.rerun()
                else:
                    st.error("Incorrect username or password.")

        with tab2:
            with st.form("register"):
                u = st.text_input("Username", key="new_u")
                p = st.text_input("Password", type="password", key="new_p")
                cp = st.text_input("Confirm password", type="password")
                create = st.form_submit_button("Create account", use_container_width=True)

            if create:
                if p != cp:
                    st.error("Passwords do not match.")
                else:
                    ok, message = register(u, p)
                    (st.success if ok else st.error)(message)

        st.caption("Paper trading only. Market data may be delayed. No real orders are sent.")

    st.stop()


# ============================================================
# SIDEBAR
# ============================================================

username = st.session_state.username

with st.sidebar:
    st.markdown('<div class="saim-title">SAIM</div>', unsafe_allow_html=True)
    st.caption(f"Account: {username}")

    st.divider()

    asset_name = st.selectbox("Market", list(ASSETS.keys()), help="East African FX pairs are included alongside the original markets.")
    symbol = ASSETS[asset_name]
    if market_group(symbol) == "East African FX":
        st.caption("🌍 East African FX · indicative market data")

    st.session_state.timeframe = st.selectbox(
        "Timeframe",
        list(TIMEFRAMES.keys()),
        index=list(TIMEFRAMES.keys()).index(st.session_state.timeframe),
    )

    st.session_state.risk = st.slider(
        "Risk per trade (%)",
        0.25,
        2.0,
        float(st.session_state.risk),
        step=0.25,
    )

    page = st.radio(
        "Page",
        ["Dashboard", "Trade", "History"],
        label_visibility="collapsed",
    )

    st.divider()
    st.caption("East Africa")
    st.caption("🇺🇬 UGX  ·  🇰🇪 KES  ·  🇹🇿 TZS")
    st.caption("🇷🇼 RWF  ·  🇪🇹 ETB  ·  🇸🇸 SSP")

    if st.button("Sign out", use_container_width=True):
        st.session_state.clear()
        st.rerun()


# ============================================================
# LOAD DATA
# ============================================================

data = get_data(symbol, st.session_state.timeframe)
data = add_indicators(data)
last_price = float(data["Close"].iloc[-1])
signal = make_signal(data)
balance = get_balance(username)

# Check stop/target every time the app refreshes.
stop_message = check_stops(username, symbol, last_price)
if stop_message:
    st.toast(stop_message)


# ============================================================
# DASHBOARD
# ============================================================

if page == "Dashboard":
    st.markdown('<div class="saim-title">Dashboard</div>', unsafe_allow_html=True)

    st.markdown(
        '<div class="paper"><strong>🟦 PAPER MODE</strong> — '
        'This app simulates trades using market prices. It never sends money to a broker.</div>',
        unsafe_allow_html=True,
    )

    positions = get_positions(username)
    open_pos = get_position(username, symbol)

    unrealized = 0.0
    if open_pos:
        entry = float(open_pos["entry"])
        qty = float(open_pos["qty"])
        side = open_pos["side"]
        unrealized = (
            (last_price - entry) * qty
            if side == "BUY"
            else (entry - last_price) * qty
        )

    trades = get_trades(username)
    closed = trades[trades["status"] == "CLOSED"] if not trades.empty else trades

    total_realized = float(closed["pnl"].sum()) if not closed.empty else 0.0
    wins = int((closed["pnl"] > 0).sum()) if not closed.empty else 0
    win_rate = (wins / len(closed) * 100) if len(closed) else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cash balance", f"${balance:,.2f}")
    c2.metric("Unrealized P/L", f"${unrealized:,.2f}")
    c3.metric("Realized P/L", f"${total_realized:,.2f}")
    c4.metric("Win rate", f"{win_rate:.1f}%")

    st.subheader(f"{asset_name} · {st.session_state.timeframe}")

    if market_group(symbol) == "East African FX":
        st.caption(
            "East African currencies are shown as local currency units per 1 USD. "
            "Quotes may be delayed and availability depends on the data provider."
        )

    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=data.index,
            open=data["Open"],
            high=data["High"],
            low=data["Low"],
            close=data["Close"],
            name="Price",
        )
    )
    fig.add_trace(go.Scatter(x=data.index, y=data["EMA20"], name="EMA 20"))
    fig.add_trace(go.Scatter(x=data.index, y=data["EMA50"], name="EMA 50"))

    if open_pos:
        fig.add_hline(y=float(open_pos["entry"]), annotation_text="Entry")
        fig.add_hline(y=float(open_pos["stop_loss"]), annotation_text="Stop")
        fig.add_hline(y=float(open_pos["take_profit"]), annotation_text="Target")

    fig.update_layout(
        template="plotly_dark",
        height=520,
        xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=20, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)

    left, right = st.columns([1, 1.3])

    with left:
        st.subheader("SAIM signal")

        if signal["signal"] == "BUY":
            st.success("BUY")
        elif signal["signal"] == "SELL":
            st.error("SELL")
        else:
            st.info("WAIT")

        st.metric("Confidence", f'{signal["confidence"]:.0f}%')
        st.write(f"RSI: **{signal['rsi']:.1f}**")
        st.write(f"EMA 20: **{signal['ema20']:,.5f}**")
        st.write(f"EMA 50: **{signal['ema50']:,.5f}**")

        st.caption(
            "The signal combines trend, momentum and recent price direction. "
            "It is a rule-based indicator, not a guaranteed prediction."
        )

    with right:
        st.subheader("Current position")

        if open_pos:
            entry = float(open_pos["entry"])
            stop = float(open_pos["stop_loss"])
            target = float(open_pos["take_profit"])
            qty = float(open_pos["qty"])

            st.write(
                f"**{open_pos['side']}** · {qty:.6f} {symbol}  \n"
                f"Entry: **{entry:,.5f}**  \n"
                f"Stop: **{stop:,.5f}**  \n"
                f"Target: **{target:,.5f}**"
            )
            st.metric("Position P/L", f"${unrealized:,.2f}")
        else:
            st.info("No open position for this market.")


# ============================================================
# TRADE PAGE
# ============================================================

elif page == "Trade":
    st.markdown('<div class="saim-title">Paper Trade</div>', unsafe_allow_html=True)
    st.caption("Orders are simulated. Risk is calculated from the selected stop distance.")

    c1, c2, c3 = st.columns(3)
    c1.metric("Current price", fmt_price(last_price, symbol))
    c2.metric("Signal", signal["signal"])
    c3.metric("Risk", f"{st.session_state.risk:.2f}%")

    pos = get_position(username, symbol)

    if pos:
        st.warning(f"You already have an open {pos['side']} position in {symbol}.")

        entry = float(pos["entry"])
        qty = float(pos["qty"])
        side = pos["side"]
        pnl = (
            (last_price - entry) * qty
            if side == "BUY"
            else (entry - last_price) * qty
        )

        st.write(
            f"Entry **{fmt_price(entry, symbol)}** · Current **{fmt_price(last_price, symbol)}** · "
            f"Qty **{qty:.6f}**"
        )
        st.metric("Unrealized P/L", f"${pnl:,.2f}")

        if st.button("Close position", type="primary", use_container_width=True):
            ok, result = close_position(username, symbol, last_price)
            if ok:
                st.success(f"Position closed. P/L: ${result:,.2f}")
                st.rerun()
            else:
                st.error(result)

    else:
        if signal["atr"] <= 0:
            st.error("Not enough market data to calculate risk levels.")
        else:
            side = st.radio("Direction", ["BUY", "SELL"], horizontal=True)

            stop, target = position_levels(last_price, signal["atr"], side)
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
            c4.metric("R:R", "1 : 2")

            st.write(
                f"Suggested size: **{qty:.6f}**  ·  "
                f"Max planned risk: **${risk_money:,.2f}**  ·  "
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
    st.subheader("Why this trade?")
    st.write(
        f"Trend: **{'bullish' if signal['ema20'] > signal['ema50'] else 'bearish'}** · "
        f"RSI: **{signal['rsi']:.1f}** · "
        f"Signal: **{signal['signal']}**"
    )


# ============================================================
# HISTORY
# ============================================================

elif page == "History":
    st.markdown('<div class="saim-title">Trade History</div>', unsafe_allow_html=True)

    trades = get_trades(username)

    if trades.empty:
        st.info("No paper trades yet.")
    else:
        closed = trades[trades["status"] == "CLOSED"]
        total = float(closed["pnl"].sum()) if not closed.empty else 0.0
        wins = int((closed["pnl"] > 0).sum()) if not closed.empty else 0
        losses = int((closed["pnl"] <= 0).sum()) if not closed.empty else 0
        win_rate = wins / len(closed) * 100 if len(closed) else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Closed trades", len(closed))
        c2.metric("Net P/L", f"${total:,.2f}")
        c3.metric("Wins", wins)
        c4.metric("Win rate", f"{win_rate:.1f}%")

        show = trades.copy()

        # SQLite returns NULL for the exit price of OPEN trades.
        # Convert numeric columns explicitly before rounding so pandas
        # does not try to round an object/None value.
        for col, decimals in {
            "pnl": 2,
            "entry": 5,
            "exit": 5,
        }.items():
            if col in show.columns:
                show[col] = pd.to_numeric(show[col], errors="coerce").round(decimals)

        st.dataframe(show, hide_index=True, use_container_width=True)

        if not closed.empty:
            curve = closed.sort_values("id").copy()
            curve["Equity change"] = curve["pnl"].cumsum()

            fig = go.Figure()
            fig.add_trace(
                go.Scatter(
                    x=curve["id"],
                    y=curve["Equity change"],
                    mode="lines+markers",
                    name="Cumulative P/L",
                )
            )
            fig.update_layout(
                template="plotly_dark",
                height=350,
                margin=dict(l=10, r=10, t=20, b=10),
            )
            st.plotly_chart(fig, use_container_width=True)

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
    "SAIM · Simple realistic paper trading · "
    "East African FX included · No real broker orders are placed · Market data can be delayed"
)
