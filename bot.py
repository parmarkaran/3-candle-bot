# bot.py
# =====================================================
# 3-Candle Break Strategy (15m, Unlimited Trades, 1 Signal/Candle)
# Using yfinance 15m candles + Telegram Alerts + Flask (for Render)
# =====================================================

from flask import Flask
import requests
import threading
import time
from datetime import datetime, timedelta
import os

import pytz
import yfinance as yf
import pandas as pd

# =====================================================
# 🔹 CONFIG
# =====================================================
app = Flask(__name__)

# YOUR TELEGRAM CREDENTIALS
TELEGRAM_TOKEN = "8184326642:AAHOkXm5MaLH1f58YtsRc9xNAN3QbEl_hNs"
CHAT_ID = "1039559105"

# Symbols (yfinance tickers)
SYMBOLS = ["BTC-USD", "ETH-USD"]

# Timezone: New York (for timestamps in messages)
NY_TZ = pytz.timezone("America/New_York")

# One-signal-per-candle tracking
last_signal_candle = {sym: None for sym in SYMBOLS}

# Length of 15m candle
BAR_SECONDS = 15 * 60


# =====================================================
# 🔹 TELEGRAM SENDER
# =====================================================
def send_telegram(message: str):
    """Send message to Telegram bot."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"❌ Telegram error: {resp.status_code} - {resp.text}")
        else:
            print("✅ Telegram message sent.")
    except Exception as e:
        print(f"❌ Telegram exception: {e}")


# =====================================================
# 🔹 YFINANCE 15m CANDLE FETCHER
# =====================================================
def get_latest_klines(symbol: str, limit: int = 3) -> pd.DataFrame | None:
    try:
        df = yf.download(
            tickers=symbol,
            interval="15m",
            period="1d",
            auto_adjust=False,
            progress=False
        )

        if df is None or df.empty:
            print(f"⚠️ No data from yfinance for {symbol}")
            return None

        if len(df) < limit:
            print(f"⚠️ Not enough candles for {symbol}")
            return None

        return df.tail(limit)

    except Exception as e:
        print(f"❌ yfinance error for {symbol}: {e}")
        return None


# =====================================================
# 🔹 STRATEGY LOGIC (MATCHES PINE SCRIPT EXACTLY)
# =====================================================
def run_strategy_for_symbol(symbol: str):
    global last_signal_candle

    df = get_latest_klines(symbol, limit=3)
    if df is None or len(df) < 3:
        return

    c1, c2, c3 = df.iloc[0], df.iloc[1], df.iloc[2]

    # timestamp of candle-3 (yfinance gives close time)
    c3_time = df.index[2]
    if c3_time.tzinfo is None:
        c3_time = pytz.utc.localize(c3_time)

    c3_time_ny = c3_time.astimezone(NY_TZ)
    now_ny = datetime.now(NY_TZ)

    # ensure candle is CLOSED (very important!)
    if now_ny < c3_time_ny:
        return

    # prevent duplicate alerts on SAME candle
    if last_signal_candle.get(symbol) == c3_time_ny:
        return

    # Extract OHLC as float
    o1, h1, l1, cl1 = float(c1["Open"]), float(c1["High"]), float(c1["Low"]), float(c1["Close"])
    o2, h2, l2, cl2 = float(c2["Open"]), float(c2["High"]), float(c2["Low"]), float(c2["Close"])
    o3, h3, l3, cl3 = float(c3["Open"]), float(c3["High"]), float(c3["Low"]), float(c3["Close"])

    # =====================================================
    # 🔹 CANDLE #3 — 70% BODY RULE
    # =====================================================
    body3 = abs(cl3 - o3)
    range3 = h3 - l3
    body_percent3 = body3 / range3 if range3 > 0 else 0
    candle3_is_70 = body_percent3 >= 0.70

    # =====================================================
    # 🔹 SHORT SETUP (matches Pine Script)
    # =====================================================
    s_c1_green = cl1 > o1
    s_c1_body = abs(cl1 - o1)

    s_c2_red = cl2 < o2
    s_c2_body = abs(cl2 - o2)
    s_c2_small = s_c2_body < s_c1_body

    s_breakdown = cl3 < min(o1, cl1)

    short_pattern = (
        s_c1_green and
        s_c2_red and
        s_c2_small and
        s_breakdown and
        candle3_is_70
    )

    # =====================================================
    # 🔹 LONG SETUP (matches Pine Script)
    # =====================================================
    l_c1_red = cl1 < o1
    l_c1_body = abs(cl1 - o1)

    l_c2_green = cl2 > o2
    l_c2_body = abs(cl2 - o2)
    l_c2_small = l_c2_body < l_c1_body

    l_breakout = cl3 > max(o1, cl1)

    long_pattern = (
        l_c1_red and
        l_c2_green and
        l_c2_small and
        l_breakout and
        candle3_is_70
    )

    # =====================================================
    # 🔹 RR = 1 : 1.5
    # =====================================================
    # Short
    short_sl = max(o2, cl2)
    short_entry = cl3
    short_risk = short_sl - short_entry
    short_tp = short_entry - 1.5 * short_risk if short_risk > 0 else None

    # Long
    long_sl = min(o2, cl2)
    long_entry = cl3
    long_risk = long_entry - long_sl
    long_tp = long_entry + 1.5 * long_risk if long_risk > 0 else None

    ny_time_str = now_ny.strftime("%Y-%m-%d %H:%M:%S")

    # =====================================================
    # 🔹 SHORT ALERT
    # =====================================================
    if short_pattern and short_risk > 0:
        msg = (
            f"🔻 *3-Candle SHORT Signal*\n"
            f"Symbol: `{symbol}`\n"
            f"Time (NY): {ny_time_str}\n\n"
            f"*Entry*: `{short_entry:.2f}`\n"
            f"*SL*: `{short_sl:.2f}`\n"
            f"*TP (1:1.5)*: `{short_tp:.2f}`\n"
            f"*Candle3 Body*: `{body_percent3*100:.1f}%`\n"
        )
        send_telegram(msg)
        last_signal_candle[symbol] = c3_time_ny
        print(f"{symbol} SHORT @ {ny_time_str}")
        return

    # =====================================================
    # 🔹 LONG ALERT
    # =====================================================
    if long_pattern and long_risk > 0:
        msg = (
            f"🔺 *3-Candle LONG Signal*\n"
            f"Symbol: `{symbol}`\n"
            f"Time (NY): {ny_time_str}\n\n"
            f"*Entry*: `{long_entry:.2f}`\n"
            f"*SL*: `{long_sl:.2f}`\n"
            f"*TP (1:1.5)*: `{long_tp:.2f}`\n"
            f"*Candle3 Body*: `{body_percent3*100:.1f}%`\n"
        )
        send_telegram(msg)
        last_signal_candle[symbol] = c3_time_ny
        print(f"{symbol} LONG @ {ny_time_str}")
        return


# =====================================================
# 🔹 STRATEGY LOOP
# =====================================================
def strategy_loop():
    print("🚀 Strategy loop started")
    while True:
        try:
            for sym in SYMBOLS:
                run_strategy_for_symbol(sym)
                time.sleep(2)
        except Exception as e:
            print(f"❌ Strategy error: {e}")
        time.sleep(60)  # check once per minute


# =====================================================
# 🔹 FLASK ROUTES FOR RENDER
# =====================================================
@app.route("/")
def home():
    return "3-Candle Break Bot Running."

@app.route("/healthz")
def health():
    return "ok"

@app.route("/test")
def test_message():
    send_telegram("🚀 *Test Message:* Your bot is working!")
    return "Test message sent!"


# =====================================================
# 🔹 START THREAD
# =====================================================
strategy_thread = threading.Thread(target=strategy_loop, daemon=True)
strategy_thread.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
