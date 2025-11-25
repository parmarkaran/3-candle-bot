# bot.py
# ================================================
# 3-Candle Break Strategy using yfinance data
# NY Session only + 1 trade per day + 70% body rule
# ================================================

from flask import Flask
import requests
import threading
import time
from datetime import datetime
import os

import pytz
import yfinance as yf
import pandas as pd

# ================================================
# 🔹 CONFIGURATION
# ================================================
app = Flask(__name__)

# --- Telegram settings (replace with your own) ---
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"

# --- Symbols (yfinance tickers) ---
# Crypto examples: "BTC-USD", "ETH-USD"
SYMBOLS = ["BTC-USD", "ETH-USD"]

# --- Timezone: New York ---
NY_TZ = pytz.timezone("America/New_York")

# --- One-trade-per-day tracking ---
# Stores last trade date (NY date) per symbol
last_trade_day = {sym: None for sym in SYMBOLS}


# ================================================
# 🔹 TELEGRAM SENDER
# ================================================
def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠️ Telegram not configured. Set TELEGRAM_TOKEN and CHAT_ID.")
        print("Message would be:\n", message)
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }
        r = requests.post(url, json=payload, timeout=10)
        print("📨 Telegram response:", r.status_code, r.text)
    except Exception as e:
        print("❌ Telegram error:", e)


# ================================================
# 🔹 NEW YORK SESSION FILTER
#     (09:30 — 16:00 NY time)
# ================================================
def in_ny_session() -> bool:
    now_ny = datetime.now(NY_TZ)
    hour = now_ny.hour
    minute = now_ny.minute

    # (nyHour > 9 or (nyHour == 9 and nyMin >= 30)) and (nyHour < 16)
    return (hour > 9 or (hour == 9 and minute >= 30)) and (hour < 16)


# ================================================
# 🔹 FETCH 15m CANDLES FROM YFINANCE
# ================================================
def get_klines(symbol: str, limit: int = 3):
    """
    Fetch recent 15m candles using yfinance.

    We need the last 3 CLOSED candles:
      c1 = candle[0]  (like close[2])
      c2 = candle[1]  (like close[1])
      c3 = candle[2]  (like close)

    Returns list of klines:
      [openTime_ms, open, high, low, close, volume]
    """
    try:
        df = yf.download(
            tickers=symbol,
            interval="15m",
            period="2d",   # enough history for last few candles
            progress=False
        )
    except Exception as e:
        print(f"❌ yfinance download error for {symbol}: {e}")
        return None

    if df is None or df.empty:
        print(f"⚠️ No data from yfinance for {symbol}")
        return None

    if len(df) < limit:
        print(f"⚠️ Not enough candles for {symbol}. Have {len(df)}, need {limit}")
        return None

    df_tail = df.tail(limit)

    klines = []
    for ts, row in df_tail.iterrows():
        # ts is a pandas Timestamp
        open_time_ms = int(ts.timestamp() * 1000)
        klines.append([
            open_time_ms,
            float(row["Open"]),
            float(row["High"]),
            float(row["Low"]),
            float(row["Close"]),
            float(row["Volume"]),
        ])

    return klines


# ================================================
# 🔹 CORE STRATEGY LOGIC (per symbol)
# ================================================
def run_strategy_for_symbol(symbol: str):
    global last_trade_day

    # --- One trade per day restriction ---
    today_ny = datetime.now(NY_TZ).date()
    if last_trade_day.get(symbol) == today_ny:
        # Already traded this symbol today
        return

    # --- NY session filter ---
    if not in_ny_session():
        return

    # --- Get last 3 candles ---
    klines = get_klines(symbol, limit=3)
    if not klines or len(klines) < 3:
        return

    # Map candles like in Pine:
    # c1 = close[2], c2 = close[1], c3 = close
    c1, c2, c3 = klines[0], klines[1], klines[2]

    # Extract OHLC
    o1, h1, l1, cl1 = float(c1[1]), float(c1[2]), float(c1[3]), float(c1[4])
    o2, h2, l2, cl2 = float(c2[1]), float(c2[2]), float(c2[3]), float(c2[4])
    o3, h3, l3, cl3 = float(c3[1]), float(c3[2]), float(c3[3]), float(c3[4])
    c3_open_time_ms = int(c3[0])

    # ==========================================================
    # CANDLE #3 — 70% BODY FILTER
    # bodyPercent3 = body3 / range3
    # ==========================================================
    body3 = abs(cl3 - o3)
    range3 = h3 - l3
    body_percent3 = body3 / range3 if range3 > 0 else 0.0
    candle3_is_70 = body_percent3 >= 0.70

    # ==========================================================
    # SHORT SETUP (Python version of your Pine)
    # ==========================================================
    s_c1_green = cl1 > o1
    s_c1_body = abs(cl1 - o1)

    s_c2_red = cl2 < o2
    s_c2_body = abs(cl2 - o2)
    s_c2_small_body = s_c2_body < s_c1_body

    s_c1_body_low = min(o1, cl1)
    s_breakdown = cl3 < s_c1_body_low

    short_pattern = (
        s_c1_green and
        s_c2_red and
        s_c2_small_body and
        s_breakdown and
        candle3_is_70
    )

    # ==========================================================
    # LONG SETUP
    # ==========================================================
    l_c1_red = cl1 < o1
    l_c1_body = abs(cl1 - o1)

    l_c2_green = cl2 > o2
    l_c2_body = abs(cl2 - o2)
    l_c2_small_body = l_c2_body < l_c1_body

    l_c1_body_high = max(o1, cl1)
    l_breakout = cl3 > l_c1_body_high

    long_pattern = (
        l_c1_red and
        l_c2_green and
        l_c2_small_body and
        l_breakout and
        candle3_is_70
    )

    # ==========================================================
    # STOP LOSS & TAKE PROFIT (1 : 1.5 RR)
    # ==========================================================

    # Short
    short_sl = max(o2, cl2)
    short_entry = cl3
    short_risk = short_sl - short_entry           # positive if valid
    short_tp = short_entry - 1.5 * short_risk if short_risk > 0 else None

    # Long
    long_sl = min(o2, cl2)
    long_entry = cl3
    long_risk = long_entry - long_sl              # positive if valid
    long_tp = long_entry + 1.5 * long_risk if long_risk > 0 else None

    # ==========================================================
    # DECIDE & SEND SIGNAL (1 trade/day)
    # ==========================================================
    now_ny = datetime.now(NY_TZ)
    now_str = now_ny.strftime("%Y-%m-%d %H:%M")

    # Prefer long first (same as example code), then short
    if long_pattern and long_risk > 0:
        last_trade_day[symbol] = today_ny

        msg = f"""
📢 <b>LONG ENTRY — 3-Candle Strategy</b>
<b>Symbol:</b> {symbol}

<b>Entry (close of candle 3):</b> {long_entry:.2f}
<b>Stop Loss:</b> {long_sl:.2f}
<b>Take Profit (1:1.5):</b> {long_tp:.2f}

<b>Candle 3 Body %:</b> {body_percent3 * 100:.1f}%
<b>Session:</b> New York
<b>Time:</b> {now_str}
"""
        print(msg)
        send_telegram(msg)
        return

    if short_pattern and short_risk > 0:
        last_trade_day[symbol] = today_ny

        msg = f"""
📢 <b>SHORT ENTRY — 3-Candle Strategy</b>
<b>Symbol:</b> {symbol}

<b>Entry (close of candle 3):</b> {short_entry:.2f}
<b>Stop Loss:</b> {short_sl:.2f}
<b>Take Profit (1:1.5):</b> {short_tp:.2f}

<b>Candle 3 Body %:</b> {body_percent3 * 100:.1f}%
<b>Session:</b> New York
<b>Time:</b> {now_str}
"""
        print(msg)
        send_telegram(msg)
        return

    # No signal
    print(f"No signal for {symbol} at {now_str}")


# ================================================
# 🔹 BACKGROUND STRATEGY LOOP
# ================================================
def strategy_loop():
    print("▶ Strategy loop started (yfinance, 15m, NY session, 1 trade/day)...")
    while True:
        try:
            for sym in SYMBOLS:
                print(f"\n=== Checking {sym} ===")
                run_strategy_for_symbol(sym)
        except Exception as e:
            print("❌ Error in strategy loop:", e)

        # Check every 60 seconds
        time.sleep(60)


# ================================================
# 🔹 FLASK ROUTES (HEALTH CHECK)
# ================================================
@app.route("/", methods=["GET"])
def home():
    return "3-Candle Strategy Bot (yfinance, NY session, 1 trade/day) ✔"


# ================================================
# 🔹 START APP + BACKGROUND THREAD
# ================================================
if __name__ == "__main__":
    # Start strategy loop in background
    t = threading.Thread(target=strategy_loop, daemon=True)
    t.start()

    # Use PORT env var for Render/Heroku etc.
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
