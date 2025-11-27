# bot.py
# =====================================================
# 3-Candle Break Strategy (15m, Unlimited Trades, 1 Signal/Candle)
# Using yfinance 15m candles + Telegram Alerts + Flask (for Render)
# Checks EXACTLY at candle close (NY time)
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

# ⚠️ Put your REAL token here (do NOT share it)
TELEGRAM_TOKEN = "8184326642:AAHOkXm5MaLH1f58YtsRc9xNAN3QbEl_hNs"
CHAT_ID = "1039559105"  # confirmed from getUpdates

SYMBOLS = ["BTC-USD", "ETH-USD"]

NY_TZ = pytz.timezone("America/New_York")

# Track last signal candle (close time) per symbol
last_signal_candle = {sym: None for sym in SYMBOLS}

# In-memory log of signals for the dashboard
logs = []


# =====================================================
# 🔹 TELEGRAM SENDER
# =====================================================
def send_telegram(message: str):
    """Send a formatted message to your Telegram bot."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
        }
        res = requests.post(url, json=payload, timeout=10)
        print("Telegram Response:", res.status_code, res.text)
    except Exception as e:
        print(f"❌ Telegram Error: {e}")


# =====================================================
# 🔹 FETCH YFINANCE CANDLES
# =====================================================
def get_latest_klines(symbol: str):
    """Get last 3 x 15m candles using yfinance."""
    try:
        df = yf.download(
            interval="15m",
            period="1d",
            tickers=symbol,
            progress=False,
        )
        if df is None or df.empty:
            print(f"⚠️ No data for {symbol}")
            return None
        if len(df) < 3:
            print(f"⚠️ Not enough candles for {symbol}")
            return None
        return df.tail(3)
    except Exception as e:
        print(f"❌ yfinance error for {symbol}: {e}")
        return None


# =====================================================
# 🔹 STRATEGY LOGIC (3-Candle Break, 70% body, 1:1.5 RR)
# =====================================================
def run_strategy_for_symbol(symbol: str):
    global last_signal_candle, logs

    df = get_latest_klines(symbol)
    if df is None:
        return

    # Oldest to newest
    c1, c2, c3 = df.iloc[0], df.iloc[1], df.iloc[2]

    # yfinance index is candle CLOSE time
    c3_time = df.index[2]
    if c3_time.tzinfo is None:
        c3_time = pytz.utc.localize(c3_time)
    c3_time_ny = c3_time.astimezone(NY_TZ)

    now_ny = datetime.now(NY_TZ)

    # Extra safety: only trade if we are AFTER candle close
    if now_ny < c3_time_ny:
        print(f"{symbol}: last candle not closed yet.")
        return

    # Prevent duplicate alerts for same candle
    if last_signal_candle.get(symbol) == c3_time_ny:
        print(f"{symbol}: already signaled for candle {c3_time_ny}")
        return

    # Extract OHLC as floats
    o1, h1, l1, cl1 = float(c1["Open"]), float(c1["High"]), float(c1["Low"]), float(c1["Close"])
    o2, h2, l2, cl2 = float(c2["Open"]), float(c2["High"]), float(c2["Low"]), float(c2["Close"])
    o3, h3, l3, cl3 = float(c3["Open"]), float(c3["High"]), float(c3["Low"]), float(c3["Close"])

    # -------------------------------------------------
    # Candle #3 — 70% body rule
    # -------------------------------------------------
    body3 = abs(cl3 - o3)
    range3 = h3 - l3
    body_percent3 = body3 / range3 if range3 > 0 else 0.0
    candle3_is_70 = body_percent3 >= 0.70

    # -------------------------------------------------
    # SHORT SETUP
    # -------------------------------------------------
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

    # -------------------------------------------------
    # LONG SETUP
    # -------------------------------------------------
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

    # -------------------------------------------------
    # RR 1 : 1.5
    # -------------------------------------------------
    short_sl = max(o2, cl2)
    short_entry = cl3
    short_risk = short_sl - short_entry
    short_tp = short_entry - 1.5 * short_risk if short_risk > 0 else None

    long_sl = min(o2, cl2)
    long_entry = cl3
    long_risk = long_entry - long_sl
    long_tp = long_entry + 1.5 * long_risk if long_risk > 0 else None

    now_str = now_ny.strftime("%Y-%m-%d %H:%M:%S")

    # -------------------------------------------------
    # SEND ALERT & LOG IT
    # -------------------------------------------------
    if short_pattern and short_risk > 0:
        msg = (
            f"🔻 *3-Candle SHORT Signal*\n"
            f"Symbol: `{symbol}`\n"
            f"Time (NY): {now_str}\n\n"
            f"*Entry*: `{short_entry:.2f}`\n"
            f"*Stop Loss*: `{short_sl:.2f}`\n"
            f"*Take Profit (1:1.5)*: `{short_tp:.2f}`\n"
            f"Candle #3 Body: `{body_percent3*100:.1f}%` of range\n"
        )
        send_telegram(msg)
        last_signal_candle[symbol] = c3_time_ny

        logs.append({
            "time": now_str,
            "symbol": symbol,
            "side": "SHORT",
            "entry": f"{short_entry:.2f}",
            "sl": f"{short_sl:.2f}",
            "tp": f"{short_tp:.2f}",
        })

        print(f"{symbol}: SHORT signal at {now_str}")
        return

    if long_pattern and long_risk > 0:
        msg = (
            f"🔺 *3-Candle LONG Signal*\n"
            f"Symbol: `{symbol}`\n"
            f"Time (NY): {now_str}\n\n"
            f"*Entry*: `{long_entry:.2f}`\n"
            f"*Stop Loss*: `{long_sl:.2f}`\n"
            f"*Take Profit (1:1.5)*: `{long_tp:.2f}`\n"
            f"Candle #3 Body: `{body_percent3*100:.1f}%` of range\n"
        )
        send_telegram(msg)
        last_signal_candle[symbol] = c3_time_ny

        logs.append({
            "time": now_str,
            "symbol": symbol,
            "side": "LONG",
            "entry": f"{long_entry:.2f}",
            "sl": f"{long_sl:.2f}",
            "tp": f"{long_tp:.2f}",
        })

        print(f"{symbol}: LONG signal at {now_str}")
        return

    print(f"{symbol}: no signal on candle {c3_time_ny}")


# =====================================================
# 🔹 STRATEGY LOOP (Option C: run at each 15m close)
# =====================================================
def strategy_loop():
    print("🚀 Strategy loop started (15m close mode).")
    while True:
        try:
            # Current NY time
            now_ny = datetime.now(NY_TZ)
            minute = now_ny.minute
            second = now_ny.second

            # Next 15m boundary (0,15,30,45)
            next_quarter_min = (minute // 15 + 1) * 15
            if next_quarter_min >= 60:
                # Move to next hour
                next_hour = now_ny.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                next_close = next_hour
            else:
                next_close = now_ny.replace(
                    minute=next_quarter_min,
                    second=0,
                    microsecond=0
                )

            # Sleep until just AFTER the candle close (extra 5s)
            sleep_seconds = (next_close - now_ny).total_seconds() + 5
            if sleep_seconds < 5:
                sleep_seconds = 5

            print(f"⏳ Sleeping {sleep_seconds:.1f}s until next 15m close at {next_close} NY time")
            time.sleep(sleep_seconds)

            # Now a new 15m candle has just closed → run strategy for all symbols
            print("🔔 15m candle closed — checking signals...")
            for sym in SYMBOLS:
                run_strategy_for_symbol(sym)
                time.sleep(1)

        except Exception as e:
            print(f"❌ Error in strategy loop: {e}")
            time.sleep(10)


# =====================================================
# 🔹 FLASK ROUTES (Render)
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

@app.route("/logs")
def view_logs():
    """Simple HTML dashboard of past signals."""
    if not logs:
        return "<h1>Trade Signals</h1><p>No signals yet.</p>"

    html = """
    <html>
    <head>
        <title>3-Candle Bot Logs</title>
        <style>
            body { font-family: Arial, sans-serif; padding: 20px; }
            table { border-collapse: collapse; width: 100%; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: center; }
            th { background-color: #f2f2f2; }
        </style>
    </head>
    <body>
        <h1>3-Candle Bot Signals</h1>
        <table>
            <tr>
                <th>Time (NY)</th>
                <th>Symbol</th>
                <th>Side</th>
                <th>Entry</th>
                <th>SL</th>
                <th>TP</th>
            </tr>
    """

    for log in reversed(logs):  # newest first
        html += (
            f"<tr>"
            f"<td>{log['time']}</td>"
            f"<td>{log['symbol']}</td>"
            f"<td>{log['side']}</td>"
            f"<td>{log['entry']}</td>"
            f"<td>{log['sl']}</td>"
            f"<td>{log['tp']}</td>"
            f"</tr>"
        )

    html += """
        </table>
    </body>
    </html>
    """
    return html


# =====================================================
# 🔹 START THREAD
# =====================================================
strategy_thread = threading.Thread(target=strategy_loop, daemon=True)
strategy_thread.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
