# bot.py
# =====================================================
# 3-Candle Break Strategy (15m, Unlimited Trades, 1 Signal/Candle)
# Candle CLOSE logic (NY Time) + Telegram Alerts + HTML Logs
# + Win-Rate Tracking (auto TP/SL detection)
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

TELEGRAM_TOKEN = "8184326642:AAHOkXm5MaLH1f58YtsRc9xNAN3QbEl_hNs"
CHAT_ID = "1039559105"  # confirmed from getUpdates

SYMBOLS = ["BTC-USD", "ETH-USD"]
NY_TZ = pytz.timezone("America/New_York")

# one-signal-per-candle tracking
last_signal_candle = {sym: None for sym in SYMBOLS}

# trades tracking
open_trades = []   # list of dicts
closed_trades = []  # list of dicts

# logs for /logs page (entries + result)
logs = []


# =====================================================
# 🔹 TELEGRAM
# =====================================================
def send_telegram(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
        }
        res = requests.post(url, json=payload, timeout=10)
        print("Telegram:", res.status_code, res.text)
    except Exception as e:
        print("❌ Telegram Error:", e)


# =====================================================
# 🔹 DATA FETCHING
# =====================================================
def get_latest_klines(symbol):
    """Get last 3 x 15m candles using yfinance."""
    try:
        df = yf.download(
            interval="15m",
            period="1d",
            tickers=symbol,
            progress=False,
        )
        if df is None or df.empty or len(df) < 3:
            return None
        return df.tail(3)
    except Exception as e:
        print(f"❌ yfinance error for {symbol}: {e}")
        return None


def get_recent_klines(symbol, days=2):
    """Get recent candles for TP/SL checking."""
    try:
        df = yf.download(
            interval="15m",
            period=f"{days}d",
            tickers=symbol,
            progress=False,
        )
        if df is None or df.empty:
            return None
        return df
    except Exception as e:
        print(f"❌ yfinance error (recent) for {symbol}: {e}")
        return None


# =====================================================
# 🔹 STRATEGY LOGIC
# =====================================================
def run_strategy_for_symbol(symbol):
    global last_signal_candle, open_trades, logs

    df = get_latest_klines(symbol)
    if df is None:
        return

    c1, c2, c3 = df.iloc[0], df.iloc[1], df.iloc[2]

    # yfinance index is candle CLOSE time (UTC or tz-aware)
    c3_time = df.index[2]
    if c3_time.tzinfo is None:
        c3_time = pytz.utc.localize(c3_time)
    c3_time_ny = c3_time.astimezone(NY_TZ)

    now_ny = datetime.now(NY_TZ)
    if now_ny < c3_time_ny:
        # last candle not closed yet
        return

    # one-signal-per-candle
    if last_signal_candle.get(symbol) == c3_time_ny:
        print(f"{symbol}: already signaled for {c3_time_ny}")
        return

    # OHLC as floats
    o1, h1, l1, cl1 = map(float, [c1["Open"], c1["High"], c1["Low"], c1["Close"]])
    o2, h2, l2, cl2 = map(float, [c2["Open"], c2["High"], c2["Low"], c2["Close"]])
    o3, h3, l3, cl3 = map(float, [c3["Open"], c3["High"], c3["Low"], c3["Close"]])

    # Candle 3 – 70% body rule
    body3 = abs(cl3 - o3)
    range3 = h3 - l3
    body_percent3 = (body3 / range3) if range3 > 0 else 0
    candle3_is_70 = body_percent3 >= 0.70

    # SHORT setup
    s_c1_green = cl1 > o1
    s_c2_red = cl2 < o2
    s_c2_small = abs(cl2 - o2) < abs(cl1 - o1)
    s_breakdown = cl3 < min(o1, cl1)

    short_pattern = (
        s_c1_green and s_c2_red and s_c2_small and
        s_breakdown and candle3_is_70
    )

    # LONG setup
    l_c1_red = cl1 < o1
    l_c2_green = cl2 > o2
    l_c2_small = abs(cl2 - o2) < abs(cl1 - o1)
    l_breakout = cl3 > max(o1, cl1)

    long_pattern = (
        l_c1_red and l_c2_green and l_c2_small and
        l_breakout and candle3_is_70
    )

    # Risk/Reward 1:1.5
    short_sl = max(o2, cl2)
    short_entry = cl3
    short_risk = short_sl - short_entry
    short_tp = short_entry - 1.5 * short_risk if short_risk > 0 else None

    long_sl = min(o2, cl2)
    long_entry = cl3
    long_risk = long_entry - long_sl
    long_tp = long_entry + 1.5 * long_risk if long_risk > 0 else None

    now_str = now_ny.strftime("%Y-%m-%d %H:%M:%S")

    # ---- SHORT ENTRY ----
    if short_pattern and short_risk > 0:
        msg = (
            f"🔻 *3-Candle SHORT*\n"
            f"Symbol: `{symbol}`\n"
            f"Entry: `{short_entry:.2f}`\n"
            f"SL: `{short_sl:.2f}`\n"
            f"TP (1.5R): `{short_tp:.2f}`\n"
            f"Time (NY): {now_str}\n"
            f"Body3: `{body_percent3*100:.1f}%`"
        )
        send_telegram(msg)
        last_signal_candle[symbol] = c3_time_ny

        trade = {
            "id": len(open_trades) + len(closed_trades) + 1,
            "symbol": symbol,
            "side": "SHORT",
            "entry": short_entry,
            "sl": short_sl,
            "tp": short_tp,
            "entry_time_utc": c3_time,     # yfinance index
            "entry_time_ny": now_str,
            "status": "OPEN",
        }
        open_trades.append(trade)
        logs.append({
            "time": now_str,
            "symbol": symbol,
            "side": "SHORT",
            "entry": f"{short_entry:.2f}",
            "sl": f"{short_sl:.2f}",
            "tp": f"{short_tp:.2f}",
            "status": "OPEN",
            "result": "",
        })
        print(f"{symbol}: SHORT signal at {now_str}")
        return

    # ---- LONG ENTRY ----
    if long_pattern and long_risk > 0:
        msg = (
            f"🔺 *3-Candle LONG*\n"
            f"Symbol: `{symbol}`\n"
            f"Entry: `{long_entry:.2f}`\n"
            f"SL: `{long_sl:.2f}`\n"
            f"TP (1.5R): `{long_tp:.2f}`\n"
            f"Time (NY): {now_str}\n"
            f"Body3: `{body_percent3*100:.1f}%`"
        )
        send_telegram(msg)
        last_signal_candle[symbol] = c3_time_ny

        trade = {
            "id": len(open_trades) + len(closed_trades) + 1,
            "symbol": symbol,
            "side": "LONG",
            "entry": long_entry,
            "sl": long_sl,
            "tp": long_tp,
            "entry_time_utc": c3_time,
            "entry_time_ny": now_str,
            "status": "OPEN",
        }
        open_trades.append(trade)
        logs.append({
            "time": now_str,
            "symbol": symbol,
            "side": "LONG",
            "entry": f"{long_entry:.2f}",
            "sl": f"{long_sl:.2f}",
            "tp": f"{long_tp:.2f}",
            "status": "OPEN",
            "result": "",
        })
        print(f"{symbol}: LONG signal at {now_str}")
        return

    print(f"{symbol}: no signal on candle {c3_time_ny}")


# =====================================================
# 🔹 WIN-RATE TRACKING (TP/SL CHECK)
# =====================================================
def check_open_trades():
    """
    For each open trade:
      - download recent 15m candles
      - check if TP or SL was hit
      - close trade as WIN / LOSS / EXPIRED
    """
    global open_trades, closed_trades, logs

    if not open_trades:
        return

    print(f"🔎 Checking {len(open_trades)} open trade(s) for TP/SL...")

    # group trades by symbol so we fetch data per symbol once
    symbols_with_open = sorted(set(t["symbol"] for t in open_trades))

    recent_data = {}
    for sym in symbols_with_open:
        df = get_recent_klines(sym, days=2)
        if df is not None and not df.empty:
            recent_data[sym] = df

    remaining_open = []
    max_bars_open = 40  # ~10 hours on 15m

    for trade in open_trades:
        sym = trade["symbol"]
        df = recent_data.get(sym)
        if df is None:
            remaining_open.append(trade)
            continue

        entry_time_utc = trade["entry_time_utc"]
        # make sure df index is tz-aware UTC
        idx = df.index
        if idx.tzinfo is None:
            df.index = pytz.utc.localize(idx[0]).tzinfo.localize(idx[0])  # quick fix
        # filter candles after entry
        subset = df[df.index > entry_time_utc]

        if subset.empty:
            remaining_open.append(trade)
            continue

        closed = False
        side = trade["side"]
        entry = trade["entry"]
        sl = trade["sl"]
        tp = trade["tp"]

        bars_checked = 0
        for ts, row in subset.iterrows():
            hi = float(row["High"])
            lo = float(row["Low"])
            bars_checked += 1

            if side == "LONG":
                # conservative: if both hit in same bar, assume SL first
                if lo <= sl:
                    status = "LOSS"
                    exit_price = sl
                    r_mult = -1.0
                    closed = True
                elif hi >= tp:
                    status = "WIN"
                    exit_price = tp
                    r_mult = (tp - entry) / abs(entry - sl) if entry != sl else 1.5
                    closed = True

            else:  # SHORT
                if hi >= sl:
                    status = "LOSS"
                    exit_price = sl
                    r_mult = -1.0
                    closed = True
                elif lo <= tp:
                    status = "WIN"
                    exit_price = tp
                    r_mult = (entry - tp) / abs(sl - entry) if entry != sl else 1.5
                    closed = True

            if closed:
                exit_time_utc = ts
                exit_time_ny = exit_time_utc.tz_convert(NY_TZ)
                exit_str = exit_time_ny.strftime("%Y-%m-%d %H:%M:%S")

                trade["status"] = status
                trade["exit_price"] = exit_price
                trade["exit_time_utc"] = exit_time_utc
                trade["exit_time_ny"] = exit_str
                trade["r_multiple"] = round(r_mult, 2)

                closed_trades.append(trade)

                # update logs entry for this trade (match by id)
                for log in logs:
                    if (log["symbol"] == sym and
                        log["side"] == side and
                        log["time"] == trade["entry_time_ny"]):
                        log["status"] = status
                        log["result"] = f"{r_mult:+.2f}R"
                        break

                print(f"{sym} {side} {status} at {exit_str}, R={r_mult:+.2f}")
                break

            if bars_checked >= max_bars_open:
                # expire trade
                exit_time_utc = ts
                exit_time_ny = exit_time_utc.tz_convert(NY_TZ)
                exit_str = exit_time_ny.strftime("%Y-%m-%d %H:%M:%S")

                trade["status"] = "EXPIRED"
                trade["exit_price"] = float(row["Close"])
                trade["exit_time_utc"] = exit_time_utc
                trade["exit_time_ny"] = exit_str
                trade["r_multiple"] = 0.0

                closed_trades.append(trade)

                for log in logs:
                    if (log["symbol"] == sym and
                        log["side"] == side and
                        log["time"] == trade["entry_time_ny"]):
                        log["status"] = "EXPIRED"
                        log["result"] = "0.00R"
                        break

                print(f"{sym} {side} EXPIRED at {exit_str}")
                closed = True
                break

        if not closed:
            remaining_open.append(trade)

    open_trades = remaining_open


# =====================================================
# 🔹 STRATEGY LOOP — EXACT 15m CANDLE CLOSE
# =====================================================
def strategy_loop():
    print("🚀 Strategy Loop Started (15m close mode + win-rate tracking)")
    while True:
        try:
            now_ny = datetime.now(NY_TZ)
            minute = now_ny.minute

            next_q = (minute // 15 + 1) * 15
            if next_q >= 60:
                next_close = now_ny.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            else:
                next_close = now_ny.replace(minute=next_q, second=0, microsecond=0)

            sleep_s = (next_close - now_ny).total_seconds() + 5
            if sleep_s < 5:
                sleep_s = 5

            print(f"⏳ Sleeping {sleep_s:.1f}s until next candle close at {next_close} NY time")
            time.sleep(sleep_s)

            print("🔔 15m candle closed — checking trades and new signals...")
            # 1) check existing trades
            check_open_trades()
            # 2) look for new entries
            for sym in SYMBOLS:
                run_strategy_for_symbol(sym)
                time.sleep(1)

        except Exception as e:
            print("❌ Strategy Loop Error:", e)
            time.sleep(10)


# =====================================================
# 🔹 FLASK ROUTES (WEB UI)
# =====================================================
@app.route("/")
def home():
    return "3-Candle Break Bot Running (with Win-Rate Tracking)."

@app.route("/healthz")
def health():
    return "ok"

@app.route("/test")
def test_message():
    send_telegram("🚀 *Test Message:* Your bot is working!")
    return "Test message sent!"

@app.route("/logs")
def view_logs():
    if not logs:
        return "<h1>No signals yet.</h1>"

    html = """
    <h1>3-Candle Bot Logs</h1>
    <table border=1 cellpadding=6>
        <tr>
            <th>Time (NY)</th><th>Symbol</th><th>Side</th>
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
            <td>{log.get('status', '')}</td>
            <td>{log.get('result', '')}</td>
        </tr>
        """
    html += "</table>"
    return html


@app.route("/performance")
def performance():
    total = len(closed_trades)
    wins = sum(1 for t in closed_trades if t["status"] == "WIN")
    losses = sum(1 for t in closed_trades if t["status"] == "LOSS")
    expired = sum(1 for t in closed_trades if t["status"] == "EXPIRED")
    total_r = sum(t.get("r_multiple", 0.0) for t in closed_trades)
    win_rate = (wins / total * 100) if total > 0 else 0.0

    html = "<h1>Performance</h1>"
    html += f"<p>Total closed trades: <b>{total}</b></p>"
    html += f"<p>Wins: <b>{wins}</b> | Losses: <b>{losses}</b> | Expired: <b>{expired}</b></p>"
    html += f"<p>Win rate: <b>{win_rate:.1f}%</b></p>"
    html += f"<p>Total R: <b>{total_r:+.2f}R</b></p>"

    if closed_trades:
        html += "<h2>Closed Trades</h2>"
        html += """
        <table border=1 cellpadding=6>
            <tr>
                <th>Time (NY)</th><th>Symbol</th><th>Side</th>
                <th>Entry</th><th>SL</th><th>TP</th>
                <th>Exit Time (NY)</th><th>Exit Price</th><th>Status</th><th>R</th>
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
                <td>{t.get('exit_time_ny', '')}</td>
                <td>{t.get('exit_price', '')}</td>
                <td>{t['status']}</td>
                <td>{t.get('r_multiple', 0.0):+.2f}R</td>
            </tr>
            """
        html += "</table>"

    return html


# =====================================================
# 🔹 START THREAD
# =====================================================
strategy_thread = threading.Thread(target=strategy_loop, daemon=True)
strategy_thread.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
