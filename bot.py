# bot.py
# =====================================================
# 3-Candle Break Strategy (24/7, Unlimited Trades)
# Using yfinance 15m candles + Telegram Alerts
# =====================================================

from flask import Flask
import requests
import threading
import time
from datetime import datetime
import os

import pytz
import yfinance as yf
import pandas as pd

# =====================================================
# 🔹 CONFIG
# =====================================================
app = Flask(__name__)

TELEGRAM_TOKEN = "7265623033:AAFn8y8GO4W3GKbgzkaoFVqyBcpZ0JgGHJg"
CHAT_ID = "1039559105"

SYMBOLS = ["BTC-USD", "ETH-USD"]
NY_TZ = pytz.timezone("America/New_York")


# =====================================================
# 🔹 TELEGRAM SENDER
# =====================================================
def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠️ Telegram not configured.")
        print("Message would be sent:\n", message)
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }
        r = requests.post(url, json=payload, timeout=10)
        print("📨 Telegram Response:", r.status_code, r.text)
    except Exception as e:
        print("❌ Telegram Error:", e)


# =====================================================
# 🔹 FETCH 15m OHLCV
# =====================================================
def get_klines(symbol: str, limit: int = 3):
    try:
        df = yf.download(
            tickers=symbol,
            interval="15m",
            period="2d",
            progress=False
        )
    except Exception as e:
        print(f"❌ yfinance download error for {symbol}: {e}")
        return None

    if df is None or df.empty or len(df) < limit:
        print(f"⚠️ Not enough candles for {symbol}")
        return None

    df_tail = df.tail(limit)

    klines = []
    for ts, row in df_tail.iterrows():

        # timestamp → ms
        open_time_ms = int(ts.timestamp() * 1000)

        # SAFE extraction to avoid pandas warnings
        open_  = row["Open"].item()
        high_  = row["High"].item()
        low_   = row["Low"].item()
        close_ = row["Close"].item()
        vol_   = row["Volume"].item()

        klines.append([
            open_time_ms,
            open_,
            high_,
            low_,
            close_,
            vol_
        ])

    return klines


# =====================================================
# 🔹 STRATEGY (UNLIMITED TRADES)
# =====================================================
def run_strategy_for_symbol(symbol: str):

    klines = get_klines(symbol, limit=3)
    if not klines:
        return

    # c1 = candle 1 (oldest), c3 = newest
    c1, c2, c3 = klines[0], klines[1], klines[2]

    o1, h1, l1, cl1 = c1[1], c1[2], c1[3], c1[4]
    o2, h2, l2, cl2 = c2[1], c2[2], c2[3], c2[4]
    o3, h3, l3, cl3 = c3[1], c3[2], c3[3], c3[4]

    # =====================================================
    # 🔹 CANDLE #3 — 70% BODY FILTER
    # =====================================================
    body3 = abs(cl3 - o3)
    range3 = h3 - l3
    body_percent3 = body3 / range3 if range3 > 0 else 0
    candle3_is_70 = body_percent3 >= 0.70

    # =====================================================
    # 🔹 SHORT SETUP
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
    # 🔹 LONG SETUP
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
    short_sl = max(o2, cl2)
    short_entry = cl3
    short_risk = short_sl - short_entry
    short_tp = short_entry - 1.5 * short_risk if short_risk > 0 else None

    long_sl = min(o2, cl2)
    long_entry = cl3
    long_risk = long_entry - long_sl
    long_tp = long_entry + 1.5 * long_risk if long_risk > 0 else None

    now_str = datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S")

    # =====================================================
    # 🔹 SEND SIGNALS (NO LIMIT PER DAY)
    # =====================================================
    if long_pattern and long_risk > 0:
        msg = f"""
📢 <b>LONG — 3-Candle Strategy</b>
<b>Symbol:</b> {symbol}

<b>Entry:</b> {long_entry:.2f}
<b>Stop Loss:</b> {long_sl:.2f}
<b>Take Profit (1:1.5):</b> {long_tp:.2f}

<b>Candle 3 Body %:</b> {body_percent3 * 100:.1f}%
<b>Time:</b> {now_str}
"""
        print(msg)
        send_telegram(msg)

    if short_pattern and short_risk > 0:
        msg = f"""
📢 <b>SHORT — 3-Candle Strategy</b>
<b>Symbol:</b> {symbol}

<b>Entry:</b> {short_entry:.2f}
<b>Stop Loss:</b> {short_sl:.2f}
<b>Take Profit (1:1.5):</b> {short_tp:.2f}

<b>Candle 3 Body %:</b> {body_percent3 * 100:.1f}%
<b>Time:</b> {now_str}
"""
        print(msg)
        send_telegram(msg)


# =====================================================
# 🔹 BACKGROUND LOOP
# =====================================================
def strategy_loop():
    print("▶ Strategy loop started (24/7, Unlimited Trades, 15m)...")
    while True:
        try:
            for sym in SYMBOLS:
                run_strategy_for_symbol(sym)
        except Exception as e:
            print("❌ Strategy loop error:", e)

        time.sleep(60)  # check every minute


# =====================================================
# 🔹 FLASK HEALTH CHECK
# =====================================================
@app.route("/")
def home():
    return "3-Candle Strategy Bot Running ✔ (24/7, Unlimited Trades)"


# =====================================================
# 🔹 START
# =====================================================
if __name__ == "__main__":
    threading.Thread(target=strategy_loop, daemon=True).start()

    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
