import os
import time
import threading
from datetime import datetime

import requests
import pytz
from flask import Flask

# ======================
# CONFIG
# ======================
TELEGRAM_TOKEN = "7265623033:AAFn8y8GO4W3GKbgzkaoFVqyBcpZ0JgGHJg"
CHAT_ID = "1039559105"

SYMBOLS = ["BTCUSDT", "ETHUSDT"]     # Coins to scan
INTERVAL = "15m"                     # TIMEFRAME 15 minutes

NY_TZ = pytz.timezone("America/New_York")


# ======================
# TELEGRAM
# ======================
def send_telegram(text: str) -> None:
    """Send formatted message to Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[ERROR] Telegram send fail: {e}")


# ======================
# BINANCE FAILOVER ENDPOINTS
# ======================
BINANCE_ENDPOINTS = [
    "https://api1.binance.com/api/v3/klines",
    "https://api2.binance.com/api/v3/klines",
    "https://api3.binance.com/api/v3/klines",
    "https://api-gcp.binance.com/api/v3/klines",
    "https://api.binance.com/api/v3/klines",   # last fallback
]


def fetch_last_3_candles(symbol: str):
    """
    Try multiple Binance endpoints until one succeeds.
    Returns list of 3 candles (open_time/open/high/low/close).
    """
    params = {"symbol": symbol, "interval": INTERVAL, "limit": 3}

    for endpoint in BINANCE_ENDPOINTS:
        try:
            r = requests.get(endpoint, params=params, timeout=5)
            if r.status_code == 200:
                data = r.json()
                if len(data) >= 3:
                    def parse(k):
                        return {
                            "open_time": int(k[0]),
                            "open": float(k[1]),
                            "high": float(k[2]),
                            "low": float(k[3]),
                            "close": float(k[4]),
                        }
                    return [parse(k) for k in data]
                else:
                    continue
            else:
                print(f"[WARN] Endpoint failed: {endpoint} → {r.status_code}")
        except Exception as e:
            print(f"[WARN] Endpoint error {endpoint}: {e}")
            continue

    print("[ERROR] All Binance endpoints failed.")
    return None


# ======================
# NY SESSION + DAILY RESET
# ======================
def is_ny_session() -> bool:
    now = datetime.now(NY_TZ)
    return (now.hour > 9 or (now.hour == 9 and now.minute >= 30)) and now.hour < 16


traded_today = {s: False for s in SYMBOLS}
last_signal_open_time = {s: None for s in SYMBOLS}
last_day = None


def reset_daily_flags_if_needed():
    global last_day, traded_today
    now_date = datetime.now(NY_TZ).date()
    if last_day is None or now_date != last_day:
        for s in traded_today.keys():
            traded_today[s] = False
        last_day = now_date
        print(f"[INFO] New NY day: {now_date}. Reset flags.")


# ======================
# STRATEGY — 3 CANDLE BREAKOUT
# ======================
def check_symbol(symbol: str):
    global traded_today, last_signal_open_time

    candles = fetch_last_3_candles(symbol)
    if candles is None:
        return

    c1, c2, c3 = candles

    # Avoid duplicate signals for same candle
    if last_signal_open_time[symbol] == c3["open_time"]:
        return

    # Candle 3 body % >= 70%
    body3 = abs(c3["close"] - c3["open"])
    range3 = c3["high"] - c3["low"]
    if range3 <= 0:
        return
    if body3 / range3 < 0.70:
        return

    # Short setup
    c1_green = c1["close"] > c1["open"]
    c1_body = abs(c1["close"] - c1["open"])

    c2_red = c2["close"] < c2["open"]
    c2_body = abs(c2["close"] - c2["open"])
    c2_small = c2_body < c1_body

    c1_low = min(c1["open"], c1["close"])
    breakdown = c3["close"] < c1_low

    short_pattern = c1_green and c2_red and c2_small and breakdown

    # Long setup
    c1_red = c1["close"] < c1["open"]
    c2_green = c2["close"] > c2["open"]
    c2_small_l = c2_body < c1_body

    c1_high = max(c1["open"], c1["close"])
    breakout = c3["close"] > c1_high

    long_pattern = c1_red and c2_green and c2_small_l and breakout

    if not (short_pattern or long_pattern):
        return

    if not is_ny_session():
        return

    if traded_today[symbol]:
        return

    entry = c3["close"]
    now_str = datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M")

    if short_pattern:
        sl = max(c2["open"], c2["close"])
        risk = sl - entry
        if risk <= 0:
            return
        tp = entry - 1.5 * risk

        msg = (
            f"🔻 <b>SHORT SIGNAL</b>\n"
            f"Symbol: {symbol}\nTime: {now_str}\n\n"
            f"Entry: {entry:.2f}\n"
            f"SL: {sl:.2f}\n"
            f"TP (1:1.5): {tp:.2f}"
        )
        send_telegram(msg)
        traded_today[symbol] = True
        last_signal_open_time[symbol] = c3["open_time"]

    if long_pattern:
        sl = min(c2["open"], c2["close"])
        risk = entry - sl
        if risk <= 0:
            return
        tp = entry + 1.5 * risk

        msg = (
            f"🚀 <b>LONG SIGNAL</b>\n"
            f"Symbol: {symbol}\nTime: {now_str}\n\n"
            f"Entry: {entry:.2f}\n"
            f"SL: {sl:.2f}\n"
            f"TP (1:1.5): {tp:.2f}"
        )
        send_telegram(msg)
        traded_today[symbol] = True
        last_signal_open_time[symbol] = c3["open_time"]


# ======================
# BACKGROUND LOOP
# ======================
def worker_loop():
    print("[INFO] Worker loop started.")
    while True:
        try:
            reset_daily_flags_if_needed()
            for s in SYMBOLS:
                check_symbol(s)
        except Exception as e:
            print(f"[ERROR] Worker loop error: {e}")
        time.sleep(60)  # runs every 1 minute


# ======================
# FLASK SERVER (Render)
# ======================
app = Flask(__name__)


@app.route("/")
def home():
    return "BTC/ETH 3-Candle bot is running.", 200


@app.route("/test")
def test():
    send_telegram("✅ Test message from /test endpoint.")
    return "Sent test message to Telegram.", 200


# ======================
# MAIN ENTRY
# ======================
if __name__ == "__main__":
    threading.Thread(target=worker_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
