import os
import time
import asyncio
import sqlite3
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import jdatetime
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logging.basicConfig(
    format='[%(levelname)s %(asctime)s] %(name)s: %(message)s',
    level=logging.INFO
)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "")
TIMEZONE = "Asia/Tehran"
DB_NAME = "daily_bot.db"

WEEKDAYS_FA = [
    "دوشنبه", "سه‌شنبه", "چهارشنبه",
    "پنجشنبه", "جمعه", "شنبه", "یکشنبه"
]

MONTHS_FA = [
    "فروردین", "اردیبهشت", "خرداد",
    "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر",
    "دی", "بهمن", "اسفند"
]

CITIES = {
    "تهران": "Tehran",
    "کرمانشاه": "Kermanshah",
    "مشهد": "Mashhad",
    "اصفهان": "Isfahan",
    "تبریز": "Tabriz",
    "شیراز": "Shiraz",
}

CITY_COORDS = {
    "تهران": (35.6892, 51.3890),
    "کرمانشاه": (34.3142, 47.0650),
    "مشهد": (36.2605, 59.6168),
    "اصفهان": (32.6546, 51.6680),
    "تبریز": (38.0800, 46.2919),
    "شیراز": (29.5918, 52.5837),
}

CRYPTOS = [
    {"symbol": "BTC", "coingecko_id": "bitcoin", "coinpaprika_id": "btc-bitcoin", "binance": "BTCUSDT"},
    {"symbol": "USDT", "coingecko_id": "tether", "coinpaprika_id": "usdt-tether", "binance": None},
    {"symbol": "ETH", "coingecko_id": "ethereum", "coinpaprika_id": "eth-ethereum", "binance": "ETHUSDT"},
    {"symbol": "GRAM", "coingecko_id": "the-open-network", "coinpaprika_id": "ton-toncoin", "binance": "TONUSDT"},
    {"symbol": "XRP", "coingecko_id": "ripple", "coinpaprika_id": "xrp-xrp", "binance": "XRPUSDT"},
    {"symbol": "TRX", "coingecko_id": "tron", "coinpaprika_id": "trx-tron", "binance": "TRXUSDT"},
]

_cache = {}
CACHE_TTL = 1800
_cache_last_cleanup = [0]

scheduler = None
channel_jobs = {}

app_telegram = None
main_loop = None
_bot_info_cache = [None]

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _cache_get(key):
    if key in _cache:
        value, ts = _cache[key]
        if (time.time() - ts) < CACHE_TTL:
            return value
        del _cache[key]
    return None


def _cache_set(key, value):
    _cache[key] = (value, time.time())


def _cache_cleanup():
    """پاک‌سازی دوره‌ای کش"""
    now = time.time()
    if now - _cache_last_cleanup[0] < 600:
        return
    _cache_last_cleanup[0] = now
    expired = [k for k, (_, ts) in _cache.items() if now - ts > CACHE_TTL]
    for k in expired:
        _cache.pop(k, None)


# ==================== دیتابیس ====================

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            chat_id TEXT PRIMARY KEY,
            title TEXT,
            added_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS channel_times (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT,
            send_hour INTEGER,
            send_minute INTEGER,
            UNIQUE(chat_id, send_hour, send_minute)
        )
    """)

    # مهاجرت از ساختار قدیمی
    try:
        c.execute("PRAGMA table_info(channels)")
        cols = [col[1] for col in c.fetchall()]
        if "send_hour" in cols and "send_minute" in cols:
            c.execute("""
                INSERT OR IGNORE INTO channel_times (chat_id, send_hour, send_minute)
                SELECT chat_id, send_hour, send_minute FROM channels
                WHERE send_hour IS NOT NULL AND send_minute IS NOT NULL
            """)
    except Exception as e:
        print(f"Migration check: {e}")

    c.execute("CREATE INDEX IF NOT EXISTS idx_channel_times_chat ON channel_times(chat_id)")

    conn.commit()
    conn.close()


def add_channel(chat_id, title=""):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO channels (chat_id, title, added_at)
        VALUES (?, ?, ?)
    """, (str(chat_id), title, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_channels():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT chat_id, title FROM channels")
    rows = c.fetchall()
    conn.close()
    return rows


def get_channel_title(chat_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT title FROM channels WHERE chat_id = ?", (str(chat_id),))
    row = c.fetchone()
    conn.close()
    return row[0] if row else ""


def delete_channel(chat_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM channels WHERE chat_id = ?", (str(chat_id),))
    c.execute("DELETE FROM channel_times WHERE chat_id = ?", (str(chat_id),))
    conn.commit()
    conn.close()


def get_channel_times(chat_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        SELECT id, send_hour, send_minute FROM channel_times
        WHERE chat_id = ?
        ORDER BY send_hour, send_minute
    """, (str(chat_id),))
    rows = c.fetchall()
    conn.close()
    return rows


def add_channel_time(chat_id, hour, minute):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO channel_times (chat_id, send_hour, send_minute)
            VALUES (?, ?, ?)
        """, (str(chat_id), int(hour), int(minute)))
        conn.commit()
        time_id = c.lastrowid
        conn.close()
        return time_id
    except sqlite3.IntegrityError:
        conn.close()
        return None


def delete_channel_time(time_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM channel_times WHERE id = ?", (int(time_id),))
    conn.commit()
    conn.close()


def get_time_by_id(time_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT chat_id, send_hour, send_minute FROM channel_times WHERE id = ?",
              (int(time_id),))
    row = c.fetchone()
    conn.close()
    return row


# ==================== بررسی کانال ====================

async def check_channel_status(bot, chat_id):
    try:
        chat = await bot.get_chat(chat_id)
    except Exception as e:
        error_str = str(e).lower()
        if "chat not found" in error_str:
            return "error", (
                "وضعیت: خطا در شناسایی\n\n"
                "موردی با این شناسه یافت نشد.\n\n"
                "دلایل احتمالی:\n"
                "• شناسه وارد شده نامعتبر است\n"
                "• ربات عضو این مقصد نیست\n"
                "• مقصد خصوصی است و ربات به آن دسترسی ندارد"
            )
        return "error", f"وضعیت: خطای غیرمنتظره\n\nجزئیات: {str(e)[:200]}"

    chat_type = chat.type
    chat_title = chat.title or str(chat_id)

    if chat_type == "private":
        return "error", (
            "وضعیت: نوع نامعتبر\n\n"
            "شناسه ارسال شده مربوط به یک چت خصوصی است.\n"
            "لطفاً شناسه یا لینک یک کانال را ارسال نمایید."
        )

    if chat_type in ("group", "supergroup"):
        return "error", (
            f"وضعیت: نوع نامعتبر\n\n"
            f"نوع: گروه\n"
            f"نام: «{chat_title}»\n\n"
            f"شناسه ارسال شده مربوط به یک گروه است، نه کانال.\n"
            f"این ربات تنها از کانال‌ها پشتیبانی می‌کند.\n\n"
            f"لطفاً شناسه یا لینک یک کانال معتبر را ارسال نمایید."
        )

    if _bot_info_cache[0] is None:
        _bot_info_cache[0] = await bot.get_me()
    bot_id = _bot_info_cache[0].id

    try:
        member = await bot.get_chat_member(chat_id, bot_id)
        status = member.status
        is_admin = status in ("administrator", "creator")

        if not is_admin:
            return "not_admin", (
                f"وضعیت: عدم دسترسی مدیریتی\n\n"
                f"کانال: «{chat_title}»\n\n"
                f"ربات در این کانال دارای دسترسی مدیریتی نیست.\n"
                f"لطفاً جهت فعال‌سازی:\n\n"
                f"۱. تنظیمات کانال\n"
                f"۲. بخش مدیریت (Administrators)\n"
                f"۳. افزودن ربات به عنوان مدیر\n"
                f"۴. فعال‌سازی دسترسی «ارسال پیام»"
            )

        can_post = getattr(member, "can_post_messages", False)
        if status == "creator":
            can_post = True

        if not can_post:
            return "not_admin", (
                f"وضعیت: عدم دسترسی ارسال\n\n"
                f"کانال: «{chat_title}»\n\n"
                f"ربات در این کانال دارای دسترسی مدیریتی است، "
                f"اما مجوز ارسال پیام برای آن فعال نشده است.\n"
                f"لطفاً دسترسی «ارسال پیام» را فعال نمایید."
            )

        return "ok", (
            f"وضعیت: تأیید شد\n\n"
            f"کانال: «{chat_title}»\n\n"
            f"ربات با موفقیت به فهرست کانال‌ها افزوده شد."
        )

    except Exception as e:
        error_str = str(e).lower()
        if "member list is inaccessible" in error_str or "chat admin" in error_str:
            return "not_admin", (
                f"وضعیت: عدم دسترسی مدیریتی\n\n"
                f"کانال: «{chat_title}»\n\n"
                f"ربات در این کانال دارای دسترسی مدیریتی نیست.\n"
                f"لطفاً جهت فعال‌سازی:\n\n"
                f"۱. تنظیمات کانال\n"
                f"۲. بخش مدیریت (Administrators)\n"
                f"۳. افزودن ربات به عنوان مدیر\n"
                f"۴. فعال‌سازی دسترسی «ارسال پیام»"
            )
        return "error", f"وضعیت: خطای غیرمنتظره\n\nجزئیات: {str(e)[:200]}"


# ==================== تاریخ و آب‌وهوا ====================

def to_persian_date(dt):
    try:
        return jdatetime.date.fromgregorian(date=dt)
    except Exception:
        return None


def get_date_info():
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)

    weekday_fa = WEEKDAYS_FA[now.weekday()]
    j_date = to_persian_date(now.date())

    if j_date:
        jalali_str = f"{j_date.day} {MONTHS_FA[j_date.month - 1]} {j_date.year}"
    else:
        jalali_str = "—"

    gregorian_str = now.strftime("%d %B %Y")
    gregorian_months_fa = {
        "January": "ژانویه", "February": "فوریه", "March": "مارس",
        "April": "آپریل", "May": "مه", "June": "ژوئن",
        "July": "جولای", "August": "اوت", "September": "سپتامبر",
        "October": "اکتبر", "November": "نوامبر", "December": "دسامبر"
    }
    for en, fa in gregorian_months_fa.items():
        gregorian_str = gregorian_str.replace(en, fa)

    return {
        "weekday": weekday_fa,
        "jalali": jalali_str,
        "gregorian": gregorian_str,
    }


def get_weather():
    results = []
    desc_map = {
        "Sunny": "آفتابی ☀️", "Clear": "صاف ☀️",
        "Partly cloudy": "نیمه‌ابری 🌤️", "Cloudy": "ابری ☁️",
        "Overcast": "ابری ☁️", "Mist": "مه 🌫️", "Fog": "مه 🌫️",
        "Freezing fog": "مه یخ‌زده 🌫️", "Haze": "مه‌آلود 🌫️",
        "Smoky haze": "دودآلود 🌫️",
        "Light rain": "باران سبک 🌦️", "Light drizzle": "نم‌نم 🌦️",
        "Light rain shower": "باران سبک 🌦️",
        "Patchy rain nearby": "باران پراکنده 🌦️",
        "Patchy rain possible": "احتمال باران 🌦️",
        "Patchy light rain": "باران سبک پراکنده 🌦️",
        "Rain": "باران 🌧️", "Moderate rain": "باران 🌧️",
        "Heavy rain": "باران شدید 🌧️",
        "Moderate or heavy rain shower": "باران شدید 🌧️",
        "Snow": "برف 🌨️", "Light snow": "برف سبک 🌨️",
        "Blowing snow": "برف همراه با باد 🌨️",
        "Blizzard": "کولاک ❄️", "Thunderstorm": "رعد و برق ⛈️",
    }

    for city_fa, city_en in CITIES.items():
        try:
            url = f"https://wttr.in/{city_en}?format=j1"
            resp = requests.get(url, timeout=10, headers={"User-Agent": "curl/7.68.0"})
            data = resp.json()
            current = data.get("current_condition", [{}])[0]
            temp = current.get("temp_C", "—")
            desc_en = current.get("weatherDesc", [{}])[0].get("value", "").strip()
            desc = desc_map.get(desc_en, "نامشخص")
            results.append(f"🌡️ {city_fa}: {temp}°C | {desc}")
        except Exception as e:
            print(f"Weather {city_fa}: {e}")
            results.append(f"🌡️ {city_fa}: —")
    return results


def get_prayer_times():
    try:
        lat, lon = CITY_COORDS["تهران"]
        url = (
            f"https://api.aladhan.com/v1/timings"
            f"?latitude={lat}&longitude={lon}"
            f"&method=7&timezone=Asia/Tehran"
        )
        resp = requests.get(url, timeout=8)
        data = resp.json()
        if data.get("code") != 200:
            return None
        t = data["data"]["timings"]
        return {
            "fajr": t.get("Fajr", "—"), "sunrise": t.get("Sunrise", "—"),
            "dhuhr": t.get("Dhuhr", "—"), "maghrib": t.get("Maghrib", "—"),
            "isha": t.get("Isha", "—"),
        }
    except Exception as e:
        print(f"Prayer: {e}")
        return None


# ==================== قیمت‌ها ====================

def get_crypto_prices_usd_batch():
    cache_key = "usd_batch"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    prices = {"tether": 1.0}

    for crypto in CRYPTOS:
        symbol = crypto["symbol"]
        gid = crypto["coingecko_id"]

        if symbol == "USDT":
            prices[gid] = 1.0
            continue

        binance_sym = crypto.get("binance")
        price = None

        if binance_sym:
            for attempt in range(2):
                try:
                    url = f"https://api.binance.com/api/v3/ticker/price?symbol={binance_sym}"
                    resp = requests.get(url, timeout=8)
                    p = float(resp.json().get("price", 0))
                    if p > 0:
                        price = p
                        break
                except Exception as e:
                    print(f"Binance {symbol} try{attempt + 1}: {e}")

        if price is None:
            try:
                url = f"https://api.coinpaprika.com/v1/tickers/{crypto['coinpaprika_id']}"
                resp = requests.get(url, timeout=6)
                price = resp.json().get("quotes", {}).get("USD", {}).get("price")
            except Exception as e:
                print(f"CoinPaprika {symbol}: {e}")

        prices[gid] = price

    _cache_set(cache_key, prices)
    return prices


def _get_usdt_toman():
    cache_key = "toman_usdt"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        url = "https://apiv2.nobitex.ir/market/stats"
        params = {"srcCurrency": "usdt", "dstCurrency": "rls"}
        resp = requests.get(url, params=params, timeout=8)
        data = resp.json()
        if data.get("status") == "ok":
            stats = data.get("stats", {})
            if "usdt-rls" in stats:
                price_rls = stats["usdt-rls"].get("latest")
                if price_rls:
                    price_toman = int(float(price_rls)) // 10
                    _cache_set(cache_key, price_toman)
                    return price_toman
    except Exception as e:
        print(f"USDT Nobitex: {e}")
    return None


def _get_usd_to_toman():
    cache_key = "usd_to_toman"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    usdt_toman = _get_usdt_toman()
    if not usdt_toman:
        return None

    usd_prices = get_crypto_prices_usd_batch()
    usdt_usd = usd_prices.get("tether", 1.0) or 1.0
    if usdt_usd <= 0:
        return None

    rate = int(usdt_toman / usdt_usd)
    _cache_set(cache_key, rate)
    return rate


def get_gold_price_18k():
    cache_key = "gold_18k"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    gold_usd_per_gram = None

    try:
        url = "https://api.goldprice.dev/v1/carat?currency=USD"
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=10)
        data = resp.json()
        if "price_gram_18k" in data:
            gold_usd_per_gram = float(data["price_gram_18k"])
    except Exception as e:
        print(f"GoldPrice.dev: {e}")

    if gold_usd_per_gram is None:
        try:
            url = "https://api.metals.live/v1/spot/gold"
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=10)
            data = resp.json()
            if "price" in data:
                gold_usd_per_ounce = float(data["price"])
                gold_usd_per_gram = (gold_usd_per_ounce / 31.1035) * (18 / 24)
        except Exception as e:
            print(f"Metals.live gold: {e}")

    if gold_usd_per_gram is None:
        return None

    usd_to_toman = _get_usd_to_toman()
    if not usd_to_toman:
        return None

    result = {
        "price_usd": gold_usd_per_gram,
        "price_toman": int(gold_usd_per_gram * usd_to_toman),
    }
    _cache_set(cache_key, result)
    return result


def get_silver_price():
    cache_key = "silver_price"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    silver_usd_per_ounce = None

    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/SI=F"
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=10)
        data = resp.json()
        result = data.get("chart", {}).get("result", [])
        if result:
            price = result[0].get("meta", {}).get("regularMarketPrice")
            if price and float(price) > 0:
                silver_usd_per_ounce = float(price)
    except Exception as e:
        print(f"Yahoo Silver: {e}")

    if silver_usd_per_ounce is None:
        try:
            url = "https://api.metals.live/v1/spot/silver"
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=10)
            data = resp.json()
            if "price" in data:
                silver_usd_per_ounce = float(data["price"])
        except Exception as e:
            print(f"Metals.live silver: {e}")

    if silver_usd_per_ounce is None:
        return None

    silver_usd_per_gram = silver_usd_per_ounce / 31.1035

    usd_to_toman = _get_usd_to_toman()
    if not usd_to_toman:
        return None

    result = {
        "price_usd": silver_usd_per_gram,
        "price_toman": int(silver_usd_per_gram * usd_to_toman),
    }
    _cache_set(cache_key, result)
    return result


def get_oil_price_brent():
    cache_key = "oil_brent"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/BZ=F"
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=10)
        data = resp.json()
        result = data.get("chart", {}).get("result", [])
        if result:
            price = result[0].get("meta", {}).get("regularMarketPrice")
            if price and float(price) > 0:
                _cache_set(cache_key, float(price))
                return float(price)
    except Exception as e:
        print(f"Yahoo Brent: {e}")

    try:
        url = "https://api.oilpriceapi.com/v1/demo/prices/BRENT_CRUDE_USD"
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=10)
        data = resp.json()
        if data.get("status") == "success":
            prices = data.get("data", {}).get("prices", [])
            if prices:
                price = float(prices[0].get("price", 0))
                if price > 0:
                    _cache_set(cache_key, price)
                    return price
    except Exception as e:
        print(f"OilPriceAPI: {e}")

    return None


def format_price(value):
    if value is None:
        return "—"
    if value >= 1000:
        return f"{value:,.0f}"
    if value >= 1:
        return f"{value:,.2f}"
    return f"{value:,.4f}"


def build_crypto_section():
    lines = []
    usd_prices = get_crypto_prices_usd_batch()
    usd_to_toman = _get_usd_to_toman()

    for crypto in CRYPTOS:
        symbol = crypto["symbol"]
        gid = crypto["coingecko_id"]
        usd = usd_prices.get(gid)

        toman = int(usd * usd_to_toman) if (usd and usd_to_toman) else None

        usd_str = f"${format_price(usd)}" if usd else "—"
        toman_str = f"{toman:,} تومان" if toman else "—"

        lines.append(f"{symbol}: {usd_str} / {toman_str}")

    silver = get_silver_price()
    if silver:
        lines.append(f"SILVER: ${format_price(silver['price_usd'])} / {silver['price_toman']:,} تومان")
    else:
        lines.append("SILVER: —")

    gold = get_gold_price_18k()
    if gold:
        lines.append(f"GOLD (18): ${format_price(gold['price_usd'])} / {gold['price_toman']:,} تومان")
    else:
        lines.append("GOLD (18): —")

    oil = get_oil_price_brent()
    if oil:
        lines.append(f"OIL (Brent): ${format_price(oil)}")
    else:
        lines.append("OIL (Brent): —")

    return lines


def build_daily_message():
    date_info = get_date_info()
    weather = get_weather()
    prayers = get_prayer_times()
    crypto = build_crypto_section()

    text = ""
    text += f"📅 {date_info['weekday']} {date_info['jalali']}\n"
    text += f"🌍 {date_info['gregorian']}\n\n"
    text += "━━━━━━━━━━━━━━\n\n"

    text += "☁️ وضعیت آب و هوای ایران\n\n"
    for w in weather:
        text += f"{w}\n"

    text += "\n━━━━━━━━━━━━━━\n\n"

    if prayers:
        text += "🕌 اوقات شرعی (تهران)\n\n"
        text += f"🌅 اذان صبح: {prayers['fajr']}\n"
        text += f"☀️ طلوع آفتاب: {prayers['sunrise']}\n"
        text += f"🌞 اذان ظهر: {prayers['dhuhr']}\n"
        text += f"🌇 اذان مغرب: {prayers['maghrib']}\n"
        text += f"🌙 اذان عشا: {prayers['isha']}\n\n"
        text += "━━━━━━━━━━━━━━\n\n"

    text += "💰 قیمت ارز و طلا\n\n"
    for c in crypto:
        text += f"{c}\n"

    return text


# ==================== Scheduler ====================

def schedule_channel_time(chat_id, time_id, hour, minute):
    global scheduler

    if scheduler is None:
        return

    job_id = f"channel_{chat_id}_time_{time_id}"

    if job_id in channel_jobs:
        try:
            channel_jobs[job_id].remove()
        except Exception:
            pass
        del channel_jobs[job_id]

    try:
        job = scheduler.add_job(
            send_to_channel,
            CronTrigger(hour=hour, minute=minute, timezone=TIMEZONE),
            args=[chat_id],
            id=job_id,
            replace_existing=True
        )
        channel_jobs[job_id] = job
        print(f"زمان‌بندی {chat_id} #{time_id} - {hour:02d}:{minute:02d}")
    except Exception as e:
        print(f"خطا در زمان‌بندی {chat_id} #{time_id}: {e}")


async def send_to_channel(chat_id):
    global app_telegram
    if not app_telegram:
        return
    try:
        _cache_cleanup()
        message = await asyncio.to_thread(build_daily_message)
        await app_telegram.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode="Markdown"
        )
        print(f"ارسال موفق به {chat_id}")
    except Exception as e:
        print(f"خطا در ارسال به {chat_id}: {e}")


def start_scheduler():
    global scheduler

    if scheduler:
        try:
            for job_id in list(channel_jobs.keys()):
                try:
                    channel_jobs[job_id].remove()
                except Exception:
                    pass
            channel_jobs.clear()
            scheduler.shutdown(wait=False)
        except Exception as e:
            print(f"Scheduler shutdown error: {e}")

    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.start()

    channels = get_channels()
    total_times = 0
    for chat_id, title in channels:
        times = get_channel_times(chat_id)
        for time_id, hour, minute in times:
            schedule_channel_time(chat_id, time_id, hour, minute)
            total_times += 1

    print(f"Scheduler started - {len(channels)} channels, {total_times} times")


# ==================== UI ====================

def back_button(target="menu_back"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت به منوی قبل", callback_data=target)]
    ])


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ افزودن کانال", callback_data="menu_add")],
        [InlineKeyboardButton("📋 مدیریت کانال‌ها", callback_data="menu_list")],
        [InlineKeyboardButton("📤 ارسال فوری", callback_data="menu_sendnow")],
    ])


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("این ربات تنها برای مدیر سامانه قابل استفاده است.")
        return
    await update.message.reply_text(
        "برای دسترسی به پنل مدیریت، از دستور /admin استفاده نمایید."
    )


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        "مدیریت ربات\n\nلطفاً یکی از گزینه‌های زیر را انتخاب نمایید:",
        reply_markup=main_menu_keyboard()
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    data = query.data

    if data == "menu_back":
        await query.edit_message_text(
            "مدیریت ربات\n\nلطفاً یکی از گزینه‌های زیر را انتخاب نمایید:",
            reply_markup=main_menu_keyboard()
        )

    elif data == "menu_add":
        context.user_data["awaiting"] = "channel_id"
        await query.edit_message_text(
            "افزودن کانال\n\n"
            "لطفاً شناسه یا نشانی کانال مورد نظر را ارسال نمایید.\n\n"
            "نمونه‌ها:\n"
            "• @my_channel\n"
            "• -1001234567890\n"
            "• https://t.me/my_channel\n\n"
            "توجه: ربات باید از قبل در کانال عضو و به عنوان مدیر منصوب شده باشد.",
            reply_markup=back_button("menu_back")
        )

    elif data == "menu_list":
        channels = get_channels()
        if not channels:
            await query.edit_message_text(
                "مدیریت کانال‌ها\n\nدر حال حاضر هیچ کانالی ثبت نشده است.",
                reply_markup=back_button("menu_back")
            )
            return

        keyboard = []
        for cid, title in channels:
            display = title if title else cid
            keyboard.append([InlineKeyboardButton(f"📢 {display}", callback_data=f"ch_{cid}")])
        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu_back")])

        await query.edit_message_text(
            "مدیریت کانال‌ها\n\nلطفاً کانال مورد نظر را انتخاب نمایید:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "menu_sendnow":
        channels = get_channels()
        if not channels:
            await query.edit_message_text(
                "ارسال فوری\n\nدر حال حاضر هیچ کانالی ثبت نشده است.",
                reply_markup=back_button("menu_back")
            )
            return

        keyboard = []
        for cid, title in channels:
            display = title if title else cid
            keyboard.append([InlineKeyboardButton(f"📢 {display}", callback_data=f"sendnow_{cid}")])
        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu_back")])

        await query.edit_message_text(
            "ارسال فوری\n\nلطفاً کانال مورد نظر را انتخاب نمایید:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("ch_"):
        chat_id = data.replace("ch_", "")
        title = get_channel_title(chat_id)
        display = title if title else chat_id
        count = len(get_channel_times(chat_id))

        keyboard = [
            [InlineKeyboardButton("📤 ارسال فوری", callback_data=f"sendnow_{chat_id}")],
            [InlineKeyboardButton(f"⏰ مدیریت زمان‌ها ({count} زمان)", callback_data=f"times_{chat_id}")],
            [InlineKeyboardButton("🗑️ حذف کانال", callback_data=f"del_{chat_id}")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="menu_list")],
        ]

        await query.edit_message_text(
            f"کانال: «{display}»\n\nتعداد زمان‌های ارسال: {count}\n\n"
            f"لطفاً عملیات مورد نظر را انتخاب نمایید:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("times_"):
        chat_id = data.replace("times_", "")
        title = get_channel_title(chat_id)
        display = title if title else chat_id
        times = get_channel_times(chat_id)

        keyboard = []
        for time_id, hour, minute in times:
            keyboard.append([InlineKeyboardButton(
                f"🗑️ حذف {hour:02d}:{minute:02d}",
                callback_data=f"deltime_{time_id}"
            )])

        keyboard.append([InlineKeyboardButton("➕ افزودن زمان جدید", callback_data=f"addtime_{chat_id}")])
        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"ch_{chat_id}")])

        if times:
            time_list = "\n".join([f"• {h:02d}:{m:02d}" for _, h, m in times])
            msg_text = f"مدیریت زمان‌های ارسال\n\nکانال: «{display}»\n\nزمان‌های فعلی:\n{time_list}\n\nبرای حذف، روی دکمه‌ی مربوطه بزنید."
        else:
            msg_text = f"مدیریت زمان‌های ارسال\n\nکانال: «{display}»\n\nدر حال حاضر هیچ زمانی ثبت نشده است.\nبرای افزودن، دکمه‌ی زیر را بزنید."

        await query.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("addtime_"):
        chat_id = data.replace("addtime_", "")
        context.user_data["awaiting"] = "new_channel_time"
        context.user_data["channel_id"] = chat_id
        title = get_channel_title(chat_id)
        display = title if title else chat_id

        await query.edit_message_text(
            f"افزودن زمان جدید\n\nکانال: «{display}»\n\n"
            f"لطفاً زمان جدید را به قالب HH:MM ارسال نمایید.\n"
            f"نمونه: 08:30 یا 00:01",
            reply_markup=back_button(f"times_{chat_id}")
        )

    elif data.startswith("deltime_"):
        time_id = int(data.replace("deltime_", ""))
        time_info = get_time_by_id(time_id)
        if not time_info:
            return
        chat_id, hour, minute = time_info
        delete_channel_time(time_id)

        job_id = f"channel_{chat_id}_time_{time_id}"
        if job_id in channel_jobs:
            try:
                channel_jobs[job_id].remove()
                del channel_jobs[job_id]
            except Exception:
                pass

        title = get_channel_title(chat_id)
        display = title if title else chat_id
        times = get_channel_times(chat_id)

        keyboard = []
        for tid, h, m in times:
            keyboard.append([InlineKeyboardButton(f"🗑️ حذف {h:02d}:{m:02d}", callback_data=f"deltime_{tid}")])
        keyboard.append([InlineKeyboardButton("➕ افزودن زمان جدید", callback_data=f"addtime_{chat_id}")])
        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"ch_{chat_id}")])

        if times:
            time_list = "\n".join([f"• {h:02d}:{m:02d}" for _, h, m in times])
            msg_text = f"مدیریت زمان‌های ارسال\n\nکانال: «{display}»\n\nزمان‌های فعلی:\n{time_list}\n\nبرای حذف، روی دکمه‌ی مربوطه بزنید."
        else:
            msg_text = f"مدیریت زمان‌های ارسال\n\nکانال: «{display}»\n\nدر حال حاضر هیچ زمانی ثبت نشده است.\nبرای افزودن، دکمه‌ی زیر را بزنید."

        await query.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("sendnow_"):
        chat_id = data.replace("sendnow_", "")
        try:
            message = await asyncio.to_thread(build_daily_message)
            await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
            await query.edit_message_text(
                "وضعیت: انجام شد\n\nپیام مورد نظر با موفقیت ارسال گردید.",
                reply_markup=back_button("menu_sendnow")
            )
        except Exception as e:
            await query.edit_message_text(
                f"وضعیت: خطا در ارسال\n\nجزئیات: {str(e)[:200]}",
                reply_markup=back_button("menu_sendnow")
            )

    elif data.startswith("del_"):
        chat_id = data.replace("del_", "")
        delete_channel(chat_id)

        for job_id in list(channel_jobs.keys()):
            if job_id.startswith(f"channel_{chat_id}_time_"):
                try:
                    channel_jobs[job_id].remove()
                except Exception:
                    pass
                del channel_jobs[job_id]

        await query.edit_message_text(
            "وضعیت: انجام شد\n\nکانال مورد نظر از فهرست حذف گردید.",
            reply_markup=back_button("menu_list")
        )


async def process_channel_id(update, context, channel_input):
    channel_input = channel_input.strip()

    if "t.me/" in channel_input:
        try:
            channel_input = channel_input.split("t.me/")[1].split("/")[0]
            if not channel_input.startswith("-"):
                channel_input = "@" + channel_input
        except Exception:
            await update.message.reply_text(
                "وضعیت: خطا در پردازش\n\nلینک وارد شده نامعتبر است.",
                reply_markup=back_button("menu_add")
            )
            return

    chat_id = channel_input
    await update.message.reply_text("در حال بررسی کانال...")

    status, message = await check_channel_status(context.bot, chat_id)

    if status == "ok":
        try:
            chat = await context.bot.get_chat(chat_id)
            title = chat.title or getattr(chat, "full_name", None) or getattr(chat, "username", None) or str(chat_id)
            add_channel(chat_id, title=title)

            times = get_channel_times(chat_id)
            if not times:
                time_id = add_channel_time(chat_id, 0, 1)
                if time_id:
                    schedule_channel_time(chat_id, time_id, 0, 1)

            await update.message.reply_text(message, reply_markup=back_button("menu_back"))
        except Exception as e:
            await update.message.reply_text(
                f"وضعیت: خطا در ذخیره‌سازی\n\nجزئیات: {str(e)[:200]}",
                reply_markup=back_button("menu_add")
            )
    else:
        await update.message.reply_text(message, reply_markup=back_button("menu_add"))


async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    awaiting = context.user_data.get("awaiting")
    text = update.message.text.strip()

    if awaiting == "channel_id":
        await process_channel_id(update, context, text)
        context.user_data["awaiting"] = None

    elif awaiting == "new_channel_time":
        chat_id = context.user_data.get("channel_id")
        if not chat_id:
            context.user_data["awaiting"] = None
            return

        try:
            hour, minute = text.split(":")
            hour = int(hour)
            minute = int(minute)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("out of range")

            time_id = add_channel_time(chat_id, hour, minute)
            title = get_channel_title(chat_id)
            display = title if title else chat_id
            context.user_data["awaiting"] = None

            if time_id is None:
                await update.message.reply_text(
                    f"وضعیت: تکراری\n\nزمان {hour:02d}:{minute:02d} قبلاً ثبت شده است.",
                    reply_markup=back_button(f"times_{chat_id}")
                )
                return

            schedule_channel_time(chat_id, time_id, hour, minute)

            await update.message.reply_text(
                f"وضعیت: انجام شد\n\nزمان {hour:02d}:{minute:02d} برای کانال «{display}» اضافه شد.",
                reply_markup=back_button(f"times_{chat_id}")
            )

        except Exception:
            await update.message.reply_text(
                "وضعیت: خطا در پردازش\n\nقالب زمان نامعتبر است. لطفاً به صورت HH:MM ارسال نمایید.\nنمونه: 08:00",
                reply_markup=back_button(f"times_{chat_id}")
            )


# ==================== Flask ====================

flask_app = Flask(__name__)


@flask_app.route("/", methods=["GET"])
def index():
    return "Daily Bot is running", 200


@flask_app.route("/health", methods=["GET"])
def health():
    return "OK", 200


async def process_update_async(update_data):
    global app_telegram
    if not app_telegram:
        return
    try:
        update = Update.de_json(update_data, app_telegram.bot)
        await app_telegram.process_update(update)
    except Exception as e:
        print(f"Process update error: {e}")


@flask_app.route("/webhook", methods=["POST"])
def webhook():
    global app_telegram, main_loop
    if not app_telegram or main_loop is None:
        return "OK", 200
    try:
        update_data = request.get_json(silent=True)
        if not update_data:
            return "OK", 200
        asyncio.run_coroutine_threadsafe(
            process_update_async(update_data), main_loop
        )
        return "OK", 200
    except Exception as e:
        print(f"Webhook error: {e}")
        return "OK", 200


async def main_async():
    global app_telegram, main_loop

    main_loop = asyncio.get_running_loop()
    init_db()

    # ⚠️ غیرفعال کردن JobQueue داخلی PTB
    app_telegram = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .job_queue(None)
        .build()
    )

    app_telegram.add_handler(CommandHandler("start", start_command))
    app_telegram.add_handler(CommandHandler("admin", admin_panel))
    app_telegram.add_handler(CallbackQueryHandler(button_handler))
    app_telegram.add_handler(MessageHandler(
        filters.User(ADMIN_ID) & filters.TEXT & ~filters.COMMAND,
        handle_admin_text
    ))

    await app_telegram.initialize()
    await app_telegram.start()

    start_scheduler()

    if RENDER_URL:
        webhook_url = f"{RENDER_URL}/webhook"
        try:
            await app_telegram.bot.delete_webhook(drop_pending_updates=True)
            await app_telegram.bot.set_webhook(
                url=webhook_url, drop_pending_updates=True,
                allowed_updates=["message", "callback_query"]
            )
            print(f"Webhook set: {webhook_url}")
        except Exception as e:
            print(f"Webhook error: {e}")

    print("ربات روشن شد...")

    while True:
        await asyncio.sleep(60)


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    asyncio.run(main_async())