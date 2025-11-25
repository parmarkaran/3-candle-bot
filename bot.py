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

SYMBOLS = ["BTCUSDT", "ETHUSDT"]   # BTC & ETH
INTERVAL = "5m"                    # Candle timeframe
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

NY_TZ = pytz.timezone("America/New_York")

# One-trade-per-day + de-duplication
traded_today = {s: False for s in SYMBOLS}
last_signal_open_time = {s: None for s in SYMBOLS}
last_day = None

# ======================
# TELEGRAM
# ======================
def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"[ERROR] Failed to send Telegram message: {e}")


# ======================
# MARKET DATA (Binance)
# ======================
def fetch_last_3_candles(symbol: str):
    """
    Returns last 3 closed candles for symbol as list of dicts:
    [{'open_time', 'open', 'high', 'low', 'close'}, ...]
    """
    params = {"symbol": symbol, "interval": INTERVAL, "limit": 3}
    r = requests.get(BINANCE_KLINES_URL, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    if len(data) < 3:
        return None

    def parse(k):
        return {
            "open_time": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
        }

    return [parse(k) for k in data]


# ======================
# TIME / SESSION HELPERS
# ======================
def is_ny_session() -> bool:
    now = datetime.now(NY_TZ)
    return (now.hour > 9 or (now.hour == 9 and now.minute >= 30)) and (now.hour < 16)


def reset_daily_flags_if_needed():
    global last_day, traded_today
    now_date = datetime.now(NY_TZ).date()
    if last_day is None or now_date != last_day:
        # New NY day → reset one-trade-per-day flags
        for s in traded_today.keys():
            traded_today[s] = False
        last_day = now_date
        print(f"[INFO] New NY day: {now_date}. Reset traded_today flags.")


# ======================
# STRATEGY LOGIC (3-Candle Break with 1:1.5 RR)
# ======================
def check_symbol(symbol: str):
    global traded_today, last_signal_open_time

    if traded_today[symbol]:
        return  # already traded this symbol today

    candles = fetch_last_3_candles(symbol)
    if candles is None:
        return

    c1, c2, c3 = candles  # c1 = oldest, c3 = most recent closed

    # Avoid sending multiple alerts for same bar
    if last_signal_open_time[symbol] == c3["open_time"]:
        return

    # ---- Candle #3 body >= 70% of range ----
    body3 = abs(c3["close"] - c3["open"])
    range3 = c3["high"] - c3["low"]
    if range3 <= 0:
        return
    body_pct3 = body3 / range3
    if body_pct3 < 0.70:
        return

    # ----------------- SHORT SETUP -----------------
    c1_green = c1["close"] > c1["open"]
    c1_body = abs(c1["close"] - c1["open"])

    c2_red = c2["close"] < c2["open"]
    c2_body = abs(c2["close"] - c2["open"])
    c2_small_body = c2_body < c1_body

    c1_body_low = min(c1["open"], c1["close"])
    breakdown = c3["close"] < c1_body_low

    short_pattern = c1_green and c2_red and c2_small_body and breakdown

    # ----------------- LONG SETUP ------------------
    c1_red = c1["close"] < c1["open"]
    c2_green = c2["close"] > c2["open"]
    c2_small_body_l = c2_body < c1_body  # reuse c2_body, c1_body above

    c1_body_high = max(c1["open"], c1["close"])
    breakout = c3["close"] > c1_body_high

    long_pattern = c1_red and c2_green and c2_small_body_l and breakout

    if not (short_pattern or long_pattern):
        return

    if not is_ny_session():
        return  # only trade in NY session

    entry = c3["close"]

    ny_now = datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M")

    if short_pattern:
        short_sl = max(c2["open"], c2["close"])
        risk = short_sl - entry
        if risk <= 0:
            return
        short_tp = entry - 1.5 * risk

        msg = (
            f"🔻 <b>SHORT SIGNAL</b>\n"
            f"Symbol: {symbol}\n"
            f"Time (NY): {ny_now}\n\n"
            f"Entry: {entry:.2f}\n"
            f"Stop Loss: {short_sl:.2f}\n"
            f"Take Profit (1:1.5): {short_tp:.2f}\n"
        )
        send_telegram(msg)
        traded_today[symbol] = True
        last_signal_open_time[symbol] = c3["open_time"]
        print(f"[ALERT] SHORT {symbol} @ {entry:.2f}")

    elif long_pattern:
        long_sl = min(c2["open"], c2["close"])
        risk = entry - long_sl
        if risk <= 0:
            return
        long_tp = entry + 1.5 * risk

        msg = (
            f"🚀 <b>LONG SIGNAL</b>\n"
            f"Symbol: {symbol}\n"
            f"Time (NY): {ny_now}\n\n"
            f"Entry: {entry:.2f}\n"
            f"Stop Loss: {long_sl:.2f}\n"
            f"Take Profit (1:1.5): {long_tp:.2f}\n"
        )
        send_telegram(msg)
        traded_today[symbol] = True
        last_signal_open_time[symbol] = c3["open_time"]
        print(f"[ALERT] LONG {symbol} @ {entry:.2f}")


# ======================
# BACKGROUND WORKER LOOP
# ======================
def worker_loop():
    print("[INFO] Worker loop started.")
    while True:
        try:
            reset_daily_flags_if_needed()
            if is_ny_session():
                for symbol in SYMBOLS:
                    check_symbol(symbol)
            else:
                # Outside NY session – just wait
                pass
        except Exception as e:
            print(f"[ERROR] Worker loop error: {e}")
            # Optional: send_telegram(f"Bot error: {e}")
        time.sleep(60)  # check roughly once per minute


# ======================
# FLASK APP (for Render health/test)
# ======================
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "BTC/ETH 3-Candle bot is running.", 200

@app.route("/test", methods=["GET"])
def test():
    send_telegram("✅ Test message from /test endpoint.")
    return "Sent test message to Telegram.", 200


# ======================
# MAIN ENTRYPOINT
# ======================
if __name__ == "__main__":
    # Start background worker in separate thread
    t = threading.Thread(target=worker_loop, daemon=True)
    t.start()

    # Start Flask server (needed for Render)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
