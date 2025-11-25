from flask import Flask
import requests
import time
import threading
from datetime import datetime
import pytz

app = Flask(__name__)

# ==============================
# 🔹 TELEGRAM SETTINGS
# ==============================
TELEGRAM_TOKEN = "7265623033:AAFn8y8GO4W3GKbgzkaoFVqyBcpZ0JgGHJg"
CHAT_ID = "1039559105"

# ==============================
# 🔹 SYMBOLS TO SCAN
# ==============================
SYMBOLS = ["BTCUSDT", "ETHUSDT"]

# Track 1 trade per day (per symbol)
last_trade_day = {sym: None for sym in SYMBOLS}

# NY timezone
ny_tz = pytz.timezone("America/New_York")


# ==============================================================
# 🔹 TELEGRAM SENDER
# ==============================================================
def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print("Telegram error:", e)


# ==============================================================
# 🔹 FETCH BINANCE FUTURES KLINES
# ==============================================================
def get_klines(symbol, limit=3):
    """
    Pine Script logic uses:
    C1 = close[2]
    C2 = close[1]
    C3 = close
    
    So we fetch 3 closed candles.
    """
    url = f"https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": symbol, "interval": "15m", "limit": limit}
    try:
        res = requests.get(url, params=params, timeout=10)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        print(f"Kline error {symbol}: {e}")
        return None


# ==============================================================
# 🔹 NEW YORK SESSION FILTER
# ==============================================================
def in_ny_session():
    now = datetime.now(ny_tz)
    hour = now.hour
    minute = now.minute
    return ((hour > 9 or (hour == 9 and minute >= 30)) and (hour < 16))


# ==============================================================
# 🔹 PROCESS STRATEGY FOR ONE SYMBOL
# ==============================================================
def run_strategy(symbol):
    global last_trade_day

    # One-trade-per-day logic
    today = datetime.now(ny_tz).date()
    if last_trade_day.get(symbol) == today:
        return  # Already traded today

    if not in_ny_session():
        return  # Do nothing outside NY session

    # Get last 3 candles
    k = get_klines(symbol, limit=3)
    if not k or len(k) < 3:
        return

    # Parse candles
    # Pine Script indexing:
    # close[2] = c1 = oldest
    # close[1] = c2
    # close    = c3 = latest closed candle
    c1, c2, c3 = k[0], k[1], k[2]

    o1, h1, l1, cl1 = float(c1[1]), float(c1[2]), float(c1[3]), float(c1[4])
    o2, h2, l2, cl2 = float(c2[1]), float(c2[2]), float(c2[3]), float(c2[4])
    o3, h3, l3, cl3 = float(c3[1]), float(c3[2]), float(c3[3]), float(c3[4])

    # ==========================================================
    # 🔹 CANDLE 3 — 70% BODY FILTER
    # ==========================================================
    body3 = abs(cl3 - o3)
    range3 = h3 - l3
    body_percent = body3 / range3 if range3 > 0 else 0
    candle3_is_70 = body_percent >= 0.70

    # ==========================================================
    # 🔹 SHORT SETUP
    # ==========================================================
    s_c1_green = cl1 > o1
    s_c1_body = abs(cl1 - o1)

    s_c2_red = cl2 < o2
    s_c2_body = abs(cl2 - o2)
    s_c2_small_body = s_c2_body < s_c1_body

    s_c1_body_low = min(o1, cl1)
    s_breakdown = cl3 < s_c1_body_low

    shortPattern = s_c1_green and s_c2_red and s_c2_small_body and s_breakdown and candle3_is_70

    # ==========================================================
    # 🔹 LONG SETUP
    # ==========================================================
    l_c1_red = cl1 < o1
    l_c1_body = abs(cl1 - o1)

    l_c2_green = cl2 > o2
    l_c2_body = abs(cl2 - o2)
    l_c2_small_body = l_c2_body < l_c1_body

    l_c1_body_high = max(o1, cl1)
    l_breakout = cl3 > l_c1_body_high

    longPattern = l_c1_red and l_c2_green and l_c2_small_body and l_breakout and candle3_is_70

    # ==========================================================
    # 🔹 STOP LOSS & TAKE PROFIT (1:1.5)
    # ==========================================================

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

    # ==========================================================
    # 🔹 DECISION LOGIC
    # ==========================================================
    if longPattern and long_risk > 0:
        last_trade_day[symbol] = today

        msg = f"""
📢 <b>LONG ENTRY</b> — 3-Candle Strategy  
<b>Symbol:</b> {symbol}

<b>Entry:</b> {long_entry}
<b>SL:</b> {long_sl}
<b>TP (1:1.5):</b> {long_tp}

<b>Body 3%:</b> {round(body_percent * 100, 1)}%
<b>Session:</b> New York
<b>Time:</b> {datetime.now(ny_tz).strftime("%Y-%m-%d %H:%M")}
"""
        send_telegram(msg)
        print(msg)
        return

    if shortPattern and short_risk > 0:
        last_trade_day[symbol] = today

        msg = f"""
📢 <b>SHORT ENTRY</b> — 3-Candle Strategy  
<b>Symbol:</b> {symbol}

<b>Entry:</b> {short_entry}
<b>SL:</b> {short_sl}
<b>TP (1:1.5):</b> {short_tp}

<b>Body 3%:</b> {round(body_percent * 100, 1)}%
<b>Session:</b> New York
<b>Time:</b> {datetime.now(ny_tz).strftime("%Y-%m-%d %H:%M")}
"""
        send_telegram(msg)
        print(msg)
        return


# ==============================================================
# 🔹 BACKGROUND LOOP
# ==============================================================
def strategy_loop():
    print("Strategy running (NY Session + 1 trade/day + 70% body rule)")

    while True:
        for sym in SYMBOLS:
            run_strategy(sym)

        time.sleep(60)  # check every minute


# ==============================================================
# 🔹 FLASK HEALTH CHECK
# ==============================================================
@app.route("/")
def home():
    return "Python 3-Candle Strategy (NY Session + 1 Trade/Day)"


# ==============================================================
# 🔹 START BOT
# ==============================================================
if __name__ == "__main__":
    t = threading.Thread(target=strategy_loop, daemon=True)
    t.start()

    app.run(host="0.0.0.0", port=10000)
