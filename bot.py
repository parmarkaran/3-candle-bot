# ============================================================
# 3-Candle Reversal + Breakout Strategy Bot
# With:
# - Breakeven trailing (+1R)
# - Fixed sizes
# - NY session filter
# - 1 trade/day/symbol
# - Full logging system
# - Signal history (even skipped)
# - Performance tracking
# - Daily report
# - Flask dashboard endpoints
# ============================================================

from flask import Flask
import threading
import time
import os
from datetime import datetime

import requests
import pytz
import yfinance as yf
import pandas as pd
import ccxt

# ============================================================
# 🔹 CONFIG
# ============================================================
app = Flask(__name__)

# Insert your keys locally — DO NOT PASTE HERE
TELEGRAM_TOKEN = "8184326642:AAHOkXm5MaLH1f58YtsRc9xNAN3QbEl_hNs"
CHAT_ID = "1039559105"  # confirmed from getUpdates

MEXC_API_KEY = "mx0vglytZwiliiKIOK"
MEXC_API_SECRET = "b44bf509cb7d46e9a6ee338c20b0f777"

SYMBOLS = ["BTC-USD", "ETH-USD"]
MEXC_SYMBOL_MAP = {
    "BTC-USD": "BTC/USDT:USDT",
    "ETH-USD": "ETH/USDT:USDT",
}

FIXED_SIZES = {
    "BTC/USDT:USDT": 0.05,
    "ETH/USDT:USDT": 0.5,
}

RR = 1.5
NY_TZ = pytz.timezone("America/New_York")

# Logging
logs = []
closed_trades = []
signal_history = []


# ============================================================
# 🔹 TELEGRAM
# ============================================================
def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg})
    except:
        print("Telegram error")


# ============================================================
# 🔹 MEXC FUTURES
# ============================================================
def init_mexc():
    return ccxt.mexc({
        "apiKey": MEXC_API_KEY,
        "secret": MEXC_API_SECRET,
        "options": {"defaultType": "swap"},
        "enableRateLimit": True
    })

mexc = init_mexc()

def get_futures_price(symbol):
    try:
        return float(mexc.fetch_ticker(symbol)["last"])
    except:
        return None


# ============================================================
# 🔹 YFINANCE
# ============================================================
def get_15m(symbol):
    try:
        df = yf.download(symbol, interval="15m", period="2d", progress=False)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns=str.lower)
        return df[["open", "high", "low", "close"]]
    except:
        return pd.DataFrame()


# ============================================================
# 🔹 STRATEGY LOGIC (Updated Version)
# ============================================================
def get_three_candle_signal(df):
    if len(df) < 3:
        return None, None, None, None

    C1, C2, C3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

    C2_body_low = min(C2["open"], C2["close"])
    C2_body_high = max(C2["open"], C2["close"])
    entry_ref = float(C3["close"])

    # LONG
    if (
        C1["close"] < C1["open"] and
        C2["close"] > C2["open"] and
        C2["high"] < C1["high"] and
        C3["close"] > C1["high"]
    ):
        sl_ref = float(C2_body_low)
        sl_dist = entry_ref - sl_ref
        if sl_dist <= 0:
            return None, None, None, None
        tp_ref = entry_ref + RR * sl_dist
        return "long", entry_ref, sl_ref, tp_ref

    # SHORT
    if (
        C1["close"] > C1["open"] and
        C2["close"] < C2["open"] and
        C2["low"] > C1["low"] and
        C3["close"] < C1["low"]
    ):
        sl_ref = float(C2_body_high)
        sl_dist = sl_ref - entry_ref
        if sl_dist <= 0:
            return None, None, None, None
        tp_ref = entry_ref - RR * sl_dist
        return "short", entry_ref, sl_ref, tp_ref

    return None, None, None, None


# ============================================================
# 🔹 POSITION HANDLING (with Breakeven + Logging)
# ============================================================
open_positions = {}

def open_position(symbol, side, entry, sl, tp):
    size = FIXED_SIZES.get(symbol)
    if not size:
        return False

    oneR = abs(entry - sl)
    now_ny = datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S")

    # Log signal (executed)
    signal_history.append({
        "time": now_ny,
        "symbol": symbol,
        "side": side.upper(),
        "entry_ref": entry,
        "sl_ref": sl,
        "tp_ref": tp,
        "status": "EXECUTED"
    })

    try:
        mexc.create_order(symbol=symbol, type="market",
                          side="buy" if side == "long" else "sell",
                          amount=size)

        log_idx = len(logs)
        logs.append({
            "time": now_ny,
            "symbol": symbol,
            "side": side.upper(),
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "status": "OPEN",
            "result": ""
        })

        open_positions[symbol] = {
            "side": side,
            "size": size,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "oneR": oneR,
            "moved_to_be": False,
            "log_index": log_idx
        }

        send_telegram(
            f"🟩 {symbol} {side.upper()} EXECUTED\nEntry: {entry}\nSL: {sl}\nTP: {tp}"
        )
        return True

    except Exception as e:
        print("Execution error:", e)
        return False


def close_position(symbol, reason):
    pos = open_positions.get(symbol)
    if not pos:
        return

    entry = pos["entry"]
    oneR = pos["oneR"]
    moved_to_be = pos["moved_to_be"]

    price = get_futures_price(symbol)
    if price is None:
        price = entry

    # Determine status
    if reason == "TP":
        status = "WIN"
        r_mult = (price - entry) / oneR if pos["side"] == "long" else (entry - price) / oneR
    elif reason == "SL":
        if moved_to_be and abs(price - entry) < 1e-8:
            status = "BE"
            r_mult = 0.0
        else:
            status = "LOSS"
            r_mult = (price - entry) / oneR if pos["side"] == "long" else (entry - price) / oneR
    else:
        status = reason
        r_mult = 0.0

    # Close futures position
    try:
        mexc.create_order(symbol=symbol, type="market",
                          side="sell" if pos["side"] == "long" else "buy",
                          amount=pos["size"],
                          params={"reduceOnly": True})
    except:
        pass

    exit_time = datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S")

    # Update log
    idx = pos["log_index"]
    logs[idx]["status"] = status
    logs[idx]["result"] = f"{reason} ({r_mult:+.2f}R)"

    closed_trades.append({
        "entry_time_ny": logs[idx]["time"],
        "symbol": symbol,
        "side": pos["side"].upper(),
        "entry": entry,
        "sl": pos["sl"],
        "tp": pos["tp"],
        "exit_time_ny": exit_time,
        "exit_price": price,
        "status": status,
        "r_multiple": r_mult
    })

    send_telegram(f"📉 {symbol} {status} by {reason} ({r_mult:+.2f}R)")

    open_positions.pop(symbol, None)


def monitor_positions():
    to_close = []

    for sym, pos in list(open_positions.items()):
        price = get_futures_price(sym)
        if price is None:
            continue

        entry = pos["entry"]
        sl = pos["sl"]
        tp = pos["tp"]
        oneR = pos["oneR"]

        # Move SL to BE
        if pos["side"] == "long":
            if not pos["moved_to_be"] and price >= entry + oneR:
                pos["sl"] = entry
                pos["moved_to_be"] = True
                send_telegram(f"🔵 {sym} LONG SL moved to breakeven")
            if price <= pos["sl"]:
                to_close.append((sym, "SL"))
            elif price >= tp:
                to_close.append((sym, "TP"))

        else:  # SHORT
            if not pos["moved_to_be"] and price <= entry - oneR:
                pos["sl"] = entry
                pos["moved_to_be"] = True
                send_telegram(f"🔵 {sym} SHORT SL moved to breakeven")
            if price >= pos["sl"]:
                to_close.append((sym, "SL"))
            elif price <= tp:
                to_close.append((sym, "TP"))

    for sym, reason in to_close:
        close_position(sym, reason)


# ============================================================
# 🔹 NY SESSION + ONE TRADE PER DAY
# ============================================================
last_trade_day = {sym: None for sym in SYMBOLS}

def in_ny_session():
    now = datetime.now(NY_TZ)
    h, m = now.hour, now.minute
    return (h > 9 or (h == 9 and m >= 30)) and (h < 16)

def today_ny():
    return datetime.now(NY_TZ).date()


# ============================================================
# 🔹 DAILY REPORT
# ============================================================
def send_daily_report():
    today = str(today_ny())
    todays = [t for t in closed_trades if t["exit_time_ny"].startswith(today)]

    wins = sum(1 for t in todays if t["status"] == "WIN")
    losses = sum(1 for t in todays if t["status"] == "LOSS")
    be = sum(1 for t in todays if t["status"] == "BE")
    total_r = sum(t["r_multiple"] for t in todays)

    msg = (
        "📊 *Daily Report*\n"
        f"Trades today: {len(todays)}\n"
        f"Wins: {wins}\nLosses: {losses}\nBreakeven: {be}\n"
        f"Total R today: {total_r:+.2f}R\n"
    )

    send_telegram(msg)


# ============================================================
# 🔹 MAIN LOOP
# ============================================================
def bot_loop():
    last_candle_time = {sym: None for sym in SYMBOLS}
    last_report_day = None

    print("Bot running...")

    while True:
        try:
            monitor_positions()

            now_ny = datetime.now(NY_TZ)
            today = today_ny()

            # Daily report
            if now_ny.hour == 16 and now_ny.minute == 1 and last_report_day != today:
                send_daily_report()
                last_report_day = today

            if not in_ny_session():
                time.sleep(30)
                continue

            for yf_symbol in SYMBOLS:

                df = get_15m(yf_symbol)
                if df.empty:
                    continue

                last_candle = df.index[-1]
                if last_candle_time[yf_symbol] == last_candle:
                    continue

                last_candle_time[yf_symbol] = last_candle

                # Evaluate signal
                side, entry_ref, sl_ref, tp_ref = get_three_candle_signal(df)
                if side is None:
                    continue

                mexc_symbol = MEXC_SYMBOL_MAP[yf_symbol]

                # Record signal (executed or skipped)
                now_ny_str = now_ny.strftime("%Y-%m-%d %H:%M:%S")

                live = get_futures_price(mexc_symbol)
                if live is None:
                    continue

                sl_dist = abs(entry_ref - sl_ref)
                if sl_dist <= 0:
                    continue

                # Shift SL/TP around live entry
                if side == "long":
                    sl = live - sl_dist
                    tp = live + RR * sl_dist
                else:
                    sl = live + sl_dist
                    tp = live - RR * sl_dist

                # Skip trade if already taken today
                if last_trade_day[yf_symbol] == today:
                    signal_history.append({
                        "time": now_ny_str,
                        "symbol": yf_symbol,
                        "side": side.upper(),
                        "entry_ref": entry_ref,
                        "sl_ref": sl_ref,
                        "tp_ref": tp_ref,
                        "status": "SKIPPED (limit reached)"
                    })
                    send_telegram(f"⚠️ {yf_symbol} signal detected but trade skipped (daily limit reached)")
                    continue

                # Execute trade
                if open_position(mexc_symbol, side, live, sl, tp):
                    last_trade_day[yf_symbol] = today

        except Exception as e:
            print("Main loop error:", e)

        time.sleep(30)


# ============================================================
# 🔹 FLASK ENDPOINTS
# ============================================================

@app.route("/")
def home():
    return "3-Candle Break Bot Running (with logs + performance + signals)."

@app.route("/healthz")
def health():
    return "ok"

@app.route("/test")
def test_message():
    send_telegram("🚀 Test Message: Bot is online!")
    return "Sent"

@app.route("/signals")
def view_signals():
    if not signal_history:
        return "<h1>No signals yet.</h1>"

    html = """
    <h1>All Signal History</h1>
    <table border=1 cellpadding=6>
        <tr>
            <th>Time</th><th>Symbol</th><th>Side</th>
            <th>EntryRef</th><th>SLRef</th><th>TPRef</th>
            <th>Status</th>
        </tr>
    """
    for s in reversed(signal_history):
        html += f"""
        <tr>
            <td>{s['time']}</td>
            <td>{s['symbol']}</td>
            <td>{s['side']}</td>
            <td>{s['entry_ref']}</td>
            <td>{s['sl_ref']}</td>
            <td>{s['tp_ref']}</td>
            <td>{s['status']}</td>
        </tr>
        """
    html += "</table>"
    return html

@app.route("/logs")
def view_logs():
    if not logs:
        return "<h1>No trades yet.</h1>"

    html = """
    <h1>Trade Logs</h1>
    <table border=1 cellpadding=6>
        <tr>
            <th>Time</th><th>Symbol</th><th>Side</th>
            <th>Entry</th><th>SL</th><th>TP</th>
            <th>Status</th><th>Result</th>
        </tr>
    """
    for log in reversed(logs):
        html += f"""
        <tr>
            <td>{log['time']}</td>
            <td>{log['symbol']}</td>
            <td>{log['side']}</td>
            <td>{log['entry']}</td>
            <td>{log['sl']}</td>
            <td>{log['tp']}</td>
            <td>{log['status']}</td>
            <td>{log['result']}</td>
        </tr>
        """
    html += "</table>"
    return html

@app.route("/performance")
def performance():
    total = len(closed_trades)
    wins = sum(1 for t in closed_trades if t["status"] == "WIN")
    losses = sum(1 for t in closed_trades if t["status"] == "LOSS")
    be = sum(1 for t in closed_trades if t["status"] == "BE")
    total_r = sum(t["r_multiple"] for t in closed_trades)
    win_rate = (wins / total * 100) if total > 0 else 0

    html = "<h1>Performance</h1>"
    html += f"<p>Total closed trades: <b>{total}</b></p>"
    html += f"<p>Wins: <b>{wins}</b> | Losses: <b>{losses}</b> | BE: <b>{be}</b></p>"
    html += f"<p>Win rate: <b>{win_rate:.1f}%</b></p>"
    html += f"<p>Total R: <b>{total_r:+.2f}R</b></p>"

    if closed_trades:
        html += "<h2>Closed Trades</h2>"
        html += """
        <table border=1 cellpadding=6>
            <tr>
                <th>Entry Time</th><th>Symbol</th><th>Side</th>
                <th>Entry</th><th>SL</th><th>TP</th>
                <th>Exit Time</th><th>Exit Price</th>
                <th>Status</th><th>R</th>
            </tr>
        """
        for t in reversed(closed_trades):
            html += f"""
            <tr>
                <td>{t['entry_time_ny']}</td>
                <td>{t['symbol']}</td>
                <td>{t['side']}</td>
                <td>{t['entry']:.2f}</td>
                <td>{t['sl']:.2f}</td>
                <td>{t['tp']:.2f}</td>
                <td>{t['exit_time_ny']}</td>
                <td>{t['exit_price']:.2f}</td>
                <td>{t['status']}</td>
                <td>{t['r_multiple']:+.2f}R</td>
            </tr>
            """
        html += "</table>"

    return html


# ============================================================
# 🔹 START SERVER + BOT THREAD
# ============================================================
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
