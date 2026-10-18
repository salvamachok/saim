# ============================================================
# SAI AI Trading Dashboard – Clean UI (with autonomous bot support)
# ============================================================
import streamlit as st
import pandas as pd
import numpy as np
import hashlib
import random
import time
import threading
import queue
import os
import sqlite3
from datetime import datetime
from contextlib import contextmanager
import plotly.express as px


st.set_page_config(page_title="SAI Trading", page_icon="📈", layout="wide")

# ============================================================
# DATABASE (SQLite – shared with the cron bot)
# ============================================================
DB_PATH = os.environ.get("DB_PATH", "/tmp/sai.db")

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def init_database():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users(
                username TEXT PRIMARY KEY, password TEXT, role TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT, time TEXT, symbol TEXT, action TEXT,
                price REAL, pnl REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS balance(
                username TEXT PRIMARY KEY, amount REAL
            )
        """)
        # Table for bot state (auto‑trade enabled users)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_state(
                username TEXT PRIMARY KEY,
                auto_trade INTEGER DEFAULT 0,
                risk REAL DEFAULT 2.0,
                strategy TEXT DEFAULT 'Scalper'
            )
        """)
        cur = conn.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] == 0:
            conn.execute("INSERT INTO users VALUES (?,?,?)", ("admin", hash_password("admin123"), "admin"))
            conn.execute("INSERT INTO balance VALUES (?,?)", ("admin", 10000.0))
            conn.execute("INSERT INTO bot_state (username) VALUES (?)", ("admin",))
        conn.commit()

try:
    init_database()
except Exception as e:
    st.error(f"Database error: {e}")
    st.stop()

# ============================================================
# USER FUNCTIONS
# ============================================================
def login(username, password):
    with get_db() as conn:
        row = conn.execute(
            "SELECT role FROM users WHERE username=? AND password=?",
            (username, hash_password(password))
        ).fetchone()
    return (True, row["role"]) if row else (False, None)

def register(username, password):
    try:
        with get_db() as conn:
            conn.execute("INSERT INTO users VALUES (?,?,?)", (username, hash_password(password), "user"))
            conn.execute("INSERT INTO balance VALUES (?,?)", (username, 10000.0))
            conn.execute("INSERT INTO bot_state (username) VALUES (?)", (username,))
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def change_password(username, old_pw, new_pw):
    with get_db() as conn:
        row = conn.execute(
            "SELECT username FROM users WHERE username=? AND password=?",
            (username, hash_password(old_pw))
        ).fetchone()
        if row:
            conn.execute("UPDATE users SET password=? WHERE username=?", (hash_password(new_pw), username))
            conn.commit()
            return True
    return False

def get_users():
    with get_db() as conn:
        return pd.read_sql_query("SELECT username, role FROM users", conn)

def remove_user(username):
    if username == "admin": return False
    with get_db() as conn:
        conn.execute("DELETE FROM users WHERE username=?", (username,))
        conn.execute("DELETE FROM balance WHERE username=?", (username,))
        conn.execute("DELETE FROM trades WHERE username=?", (username,))
        conn.execute("DELETE FROM bot_state WHERE username=?", (username,))
        conn.commit()
    return True

# ============================================================
# SESSION DEFAULTS
# ============================================================
for k, v in {
    "logged": False, "username": "", "role": "", "bot_running": False,
    "risk": 2, "auto_trade": False, "balance": 10000.0, "dark_mode": False,
    "live_feed": False, "strategy": "Scalper", "alerts_enabled": False,
    "alert_email": "", "alert_telegram": ""
}.items():
    if k not in st.session_state: st.session_state[k] = v

# ============================================================
# MARKET & AI (simulated)
# ============================================================
CURRENCIES = {"UGX":3800, "KES":130, "TZS":2600, "SSP":1600, "RWF":1350, "USD":1, "EUR":0.92}

def get_market():
    return {c: round(base + random.uniform(-5,5), 2) for c, base in CURRENCIES.items()}

def run_ai(prices, strategy="Scalper"):
    results = {}
    for ccy, rate in prices.items():
        if strategy == "Scalper":
            rsi = round(random.uniform(30,70),1); tb, ts = 45, 55
        elif strategy == "Trend":
            rsi = round(random.uniform(20,80),1); tb, ts = 35, 65
        else:
            rsi = round(random.uniform(25,75),1); tb, ts = 40, 60
        signal = "BUY" if rsi < tb else "SELL" if rsi > ts else random.choice(["BUY","SELL","HOLD"])
        confidence = round(random.uniform(50,99),1)
        forecast = round(rate + random.uniform(-3,3),2)
        results[ccy] = {"signal":signal,"confidence":confidence,"rsi":rsi,"forecast":forecast}
    return results

def forecast_currency(ccy, days):
    val = CURRENCIES.get(ccy,1)
    return [round(val := val * (1 + random.uniform(-0.02,0.02)),2) for _ in range(days)]

# ============================================================
# TRADING
# ============================================================
def get_account():
    with get_db() as conn:
        row = conn.execute("SELECT amount FROM balance WHERE username=?", (st.session_state.username,)).fetchone()
        if row: st.session_state.balance = row["amount"]
    return {"balance": st.session_state.balance}

def execute_trade(symbol, action, price):
    uname = st.session_state.username
    pnl = random.uniform(-30,80) * (st.session_state.risk/100) * st.session_state.balance / 100
    new_bal = st.session_state.balance + pnl
    if new_bal < 0: pnl = -st.session_state.balance; new_bal = 0
    with get_db() as conn:
        conn.execute("UPDATE balance SET amount=? WHERE username=?", (new_bal, uname))
        conn.execute("INSERT INTO trades(username,time,symbol,action,price,pnl) VALUES(?,?,?,?,?,?)",
                     (uname, datetime.now().isoformat(), symbol, action, price, pnl))
        conn.commit()
    st.session_state.balance = new_bal
    return pnl

def get_trade_history():
    with get_db() as conn:
        return pd.read_sql_query("SELECT * FROM trades WHERE username=? ORDER BY id DESC",
                                 conn, params=[st.session_state.username])

# ============================================================
# BOT THREAD (runs only while dashboard is open)
# ============================================================
signal_queue = queue.Queue()
bot_settings = {"bot_running":False,"auto_trade":False,"risk":2,"strategy":"Scalper"}
bot_lock = threading.Lock()

def bot_loop():
    while True:
        with bot_lock:
            run = bot_settings["bot_running"] and bot_settings["auto_trade"]
            strategy = bot_settings["strategy"]
        if not run: time.sleep(2); continue
        prices = get_market()
        ai = run_ai(prices, strategy)
        for ccy, d in ai.items():
            if d["signal"] in ("BUY","SELL") and random.random()<0.3:
                signal_queue.put({"symbol":ccy,"action":d["signal"],"price":prices[ccy]})
        time.sleep(5)

if "bot_thread" not in st.session_state:
    threading.Thread(target=bot_loop, daemon=True).start()
    st.session_state.bot_thread = True

def process_signals():
    while not signal_queue.empty():
        t = signal_queue.get()
        execute_trade(t["symbol"], t["action"], t["price"])
        if st.session_state.alerts_enabled:
            st.toast(f"Bot: {t['action']} {t['symbol']} @ {t['price']}")

# ============================================================
# LOGIN PAGE – ONLY LOGO + FORM
# ============================================================
if not st.session_state.logged:
    _, center_col, _ = st.columns([1,2,1])
    with center_col:
        st.markdown("""
            <div style="text-align:center; margin-top: 3rem;">
                <span style="font-size: 4rem; font-weight: 900; background: linear-gradient(135deg, #4CAF50, #2196F3); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">
                    SAI
                </span>
                <p style="font-size: 1.2rem; color: #888; margin-top: -10px;">Trading Intelligence</p>
            </div>
        """, unsafe_allow_html=True)
        st.write("")
        tab1, tab2 = st.tabs(["Login", "Register"])
        with tab1:
            user = st.text_input("Username")
            pw = st.text_input("Password", type="password")
            if st.button("Login", use_container_width=True):
                ok, role = login(user, pw)
                if ok:
                    st.session_state.logged = True
                    st.session_state.username = user
                    st.session_state.role = role
                    get_account()
                    st.rerun()
                else:
                    st.error("Invalid credentials")
        with tab2:
            new_user = st.text_input("New Username")
            new_pw = st.text_input("New Password", type="password")
            if st.button("Create Account", use_container_width=True):
                if register(new_user, new_pw):
                    st.success("Account created! Please login.")
                else:
                    st.error("Username already exists")
    st.stop()

process_signals()

# ============================================================
# SIDEBAR – TABS WITH PANELS
# ============================================================
with st.sidebar:
    st.markdown("## 🤖 SAI Trading")
    st.write(f"👤 {st.session_state.username} | Role: {st.session_state.role}")
    st.divider()
    side_tab1, side_tab2, side_tab3 = st.tabs(["⚡ Control", "🔧 Settings", "👑 Admin"])

    with side_tab1:
        st.subheader("Bot Control")
        st.session_state.auto_trade = st.checkbox("Enable Auto-Trade", value=st.session_state.auto_trade)
        st.session_state.risk = st.slider("Risk %", 1, 20, st.session_state.risk)
        col1, col2 = st.columns(2)
        with col1:
            if st.button("▶ Start", use_container_width=True):
                with bot_lock:
                    bot_settings["bot_running"] = True
                    bot_settings["auto_trade"] = st.session_state.auto_trade
                    bot_settings["risk"] = st.session_state.risk
                    bot_settings["strategy"] = st.session_state.strategy
                # Persist auto-trade state for autonomous bot
                with get_db() as conn:
                    conn.execute("UPDATE bot_state SET auto_trade=?, risk=?, strategy=? WHERE username=?",
                                 (1 if st.session_state.auto_trade else 0, st.session_state.risk,
                                  st.session_state.strategy, st.session_state.username))
                    conn.commit()
                st.session_state.bot_running = True
                st.rerun()
        with col2:
            if st.button("⏹ Stop", use_container_width=True):
                with bot_lock:
                    bot_settings["bot_running"] = False
                    bot_settings["auto_trade"] = False
                with get_db() as conn:
                    conn.execute("UPDATE bot_state SET auto_trade=0 WHERE username=?",
                                 (st.session_state.username,))
                    conn.commit()
                st.session_state.bot_running = False
                st.rerun()

        st.divider()
        st.subheader("AI Strategy")
        strategies = ["Scalper", "Trend", "Breakout"]
        st.session_state.strategy = st.selectbox("Strategy", strategies,
                                                 index=strategies.index(st.session_state.strategy))

    with side_tab2:
        st.subheader("Appearance")
        dark = st.checkbox("🌙 Dark Mode", value=st.session_state.dark_mode)
        if dark != st.session_state.dark_mode:
            st.session_state.dark_mode = dark
            st.rerun()
        st.divider()
        st.subheader("Alerts")
        st.session_state.alerts_enabled = st.checkbox("Enable Alerts", value=st.session_state.alerts_enabled)
        if st.session_state.alerts_enabled:
            st.session_state.alert_email = st.text_input("Email", value=st.session_state.alert_email)
            st.session_state.alert_telegram = st.text_input("Telegram ID", value=st.session_state.alert_telegram)
        st.divider()
        with st.expander("🔑 Change Password"):
            old_pw = st.text_input("Old Password", type="password")
            new_pw1 = st.text_input("New Password", type="password")
            new_pw2 = st.text_input("Confirm", type="password")
            if st.button("Update Password"):
                if new_pw1 != new_pw2: st.error("Passwords don't match")
                elif not old_pw or not new_pw1: st.error("Fill all fields")
                elif change_password(st.session_state.username, old_pw, new_pw1):
                    st.success("Password updated")
                else: st.error("Old password incorrect")

    with side_tab3:
        if st.session_state.role == "admin":
            st.subheader("User Management")
            users = get_users()
            st.dataframe(users, hide_index=True, use_container_width=True)
            del_user = st.selectbox("Remove user", users["username"].tolist() if not users.empty else [])
            if st.button("Delete User", disabled=(del_user=="admin")):
                remove_user(del_user)
                st.success(f"User {del_user} removed")
                st.rerun()
        else:
            st.info("Admin access only")

    st.divider()
    if st.button("🚪 Logout", use_container_width=True):
        with bot_lock:
            bot_settings["bot_running"] = False
            bot_settings["auto_trade"] = False
        st.session_state.clear()
        st.rerun()

# ============================================================
# DARK MODE CSS
# ============================================================
if st.session_state.dark_mode:
    st.markdown("""
        <style> body { background-color:#0e1117; color:#fafafa; } .stApp { background-color:#0e1117; } .stMetric { background-color:#262730; } </style>
    """, unsafe_allow_html=True)

# ============================================================
# MAIN DASHBOARD
# ============================================================
st.title("📈 Trading Dashboard")
st.caption(f"Last update: {datetime.now().strftime('%H:%M:%S')}")

acc = get_account()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Balance", f"${acc['balance']:,.2f}")
c2.metric("Bot", "RUNNING" if st.session_state.bot_running else "STOPPED")
c3.metric("Risk", f"{st.session_state.risk}%")
c4.metric("Signals", signal_queue.qsize())

dash_tab1, dash_tab2, dash_tab3, dash_tab4, dash_tab5 = st.tabs(
    ["🌍 Market", "🧠 AI Signals", "🔮 Forecast", "📊 Trading", "📜 History"]
)

with dash_tab1:
    st.subheader("Live Market Rates")
    live = st.checkbox("Auto-refresh", value=st.session_state.live_feed)
    if live != st.session_state.live_feed:
        st.session_state.live_feed = live
        st.rerun()
    prices = get_market()
    df_market = pd.DataFrame({"Currency": list(prices.keys()), "Rate": list(prices.values())})
    fig_bar = px.bar(df_market, x="Currency", y="Rate", color="Currency", title="Current Rates")
    st.plotly_chart(fig_bar, use_container_width=True)
    st.dataframe(df_market, hide_index=True)
    if live:
        st.caption("Auto-refresh every 2s")
        time.sleep(2)
        st.rerun()

with dash_tab2:
    st.subheader("AI Decision Engine")
    prices = get_market()
    ai = run_ai(prices, st.session_state.strategy)
    rows = [{"Currency": c, "Signal": d["signal"], "Confidence %": d["confidence"],
             "RSI": d["rsi"], "Forecast": d["forecast"]} for c,d in ai.items()]
    ai_df = pd.DataFrame(rows)
    st.dataframe(ai_df, hide_index=True, use_container_width=True)
    for row in rows:
        if row["Signal"] == "BUY":
            st.success(f"BUY {row['Currency']} (Confidence {row['Confidence %']}%)")
        elif row["Signal"] == "SELL":
            st.error(f"SELL {row['Currency']} (Confidence {row['Confidence %']}%)")
        else:
            st.info(f"{row['Signal']} {row['Currency']}")

with dash_tab3:
    st.subheader("Price Forecast")
    ccy = st.selectbox("Currency", list(CURRENCIES.keys()))
    days = st.slider("Days", 1, 30, 7)
    pred = forecast_currency(ccy, days)
    df_fc = pd.DataFrame({"Day": range(1, days+1), "Forecast": pred})
    st.line_chart(df_fc.set_index("Day"))
    st.dataframe(df_fc, hide_index=True)

with dash_tab4:
    st.subheader("Manual Trading")
    sym = st.selectbox("Symbol", list(CURRENCIES.keys()), key="trade_sym")
    act = st.radio("Action", ["BUY", "SELL"])
    price_now = get_market()[sym]
    st.metric("Current Price", price_now)
    if st.button("Execute Trade", type="primary"):
        pnl = execute_trade(sym, act, price_now)
        st.success(f"Trade executed | PnL: ${pnl:.2f}")

with dash_tab5:
    st.subheader("Trade History")
    hist = get_trade_history()
    if not hist.empty:
        st.dataframe(hist, hide_index=True, use_container_width=True)
        col_h1, col_h2 = st.columns(2)
        col_h1.metric("Total Trades", len(hist))
        col_h2.metric("Total PnL", f"${hist['pnl'].sum():.2f}")
        hist_sorted = hist.sort_values("id")
        hist_sorted["Cumulative PnL"] = hist_sorted["pnl"].cumsum()
        fig_pnl = px.line(hist_sorted, x="id", y="Cumulative PnL", title="Cumulative Profit/Loss")
        st.plotly_chart(fig_pnl, use_container_width=True)
        csv = hist.to_csv(index=False).encode()
        st.download_button("Export CSV", data=csv, file_name=f"trades_{st.session_state.username}.csv")
    else:
        st.info("No trades yet")

st.divider()
st.caption("SAI Trading Dashboard v2.1 | Autonomous bot active via Cron")
