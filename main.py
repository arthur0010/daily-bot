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
    {"symbol": "BTC", "coingecko_id": "bitcoin", "coinpaprika_id": "btc-bitcoin", "nobitex": "btc", "binance": "BTCUSDT"},
    {"symbol": "USDT", "coingecko_id": "tether", "coinpaprika_id": "usdt-tether", "nobitex": "usdt", "binance": None},
    {"symbol": "ETH", "coingecko_id": "ethereum", "coinpaprika_id": "eth-ethereum", "nobitex": "eth", "binance": "ETHUSDT"},
    {"symbol": "GRAM", "coingecko_id": "the-open-network", "coinpaprika_id": "ton-toncoin", "nobitex": "ton", "binance": "TONUSDT"},
    {"symbol": "XRP", "coingecko_id": "ripple", "coinpaprika_id": "xrp-xrp", "nobitex": "xrp", "binance": "XRPUSDT"},
    {"symbol": "TRX", "coingecko_id": "tron", "coinpaprika_id": "trx-tron", "nobitex": "trx", "binance": "TRXUSDT"},
]

MOTIVATIONAL_MESSAGES = [
    "به خودت ایمان داشته باش\nتو از آن چیزی که فکر می‌کنی قوی‌تری",
    "هر روز یک شروع جدید است\nدیروز تمام شد، امروز از آن توست",
    "هیچ‌وقت دیر نیست\nبهترین زمان برای شروع، همین لحظه است",
    "رؤیاهایت ارزش تلاش دارند\nبرای رسیدن به آن‌ها بکوش",
    "موفقیت یک‌شبه به دست نمی‌آید\nولی هر روز یک قدم به آن نزدیک‌تر می‌شوی",
    "به خودت سخت نگیر\nتو داری بهترین تلاشت را می‌کنی",
    "زندگی کوتاه‌تر از آن است که ناراحت باشی\nلبخند بزن",
    "هر سختی، درسی در خود دارد\nاز آن بیاموز و ادامه بده",
    "تو قوی‌تر از مشکلات هستی\nفقط باید باور کنی",
    "امروز را با انرژی آغاز کن\nفردا از امروزت سپاسگزار خواهد بود",
    "نگران نباش، همه چیز درست می‌شود\nفقط به زمان نیاز داری",
    "خودت را با کسی مقایسه نکن\nهر کس مسیر خودش را دارد",
    "گاهی شکست، بهترین معلم است\nاز آن فرار نکن",
    "بهترین نسخه‌ی خودت باش\nنیازی نیست شبیه کسی باشی",
    "هر روز که بیدار می‌شوی، فرصتی تازه است\nاز آن بهره ببر",
    "آرامش را در درون خود جستجو کن\nنه در بیرون",
    "کارهایی که امروز انجام می‌دهی\nآینده‌ات را می‌سازند",
    "به خودت وقت بده\nمهم‌ترین چیز در زندگی، خودت هستی",
    "هیچ‌کس کامل نیست\nولی همه می‌توانند بهتر شوند",
    "امید داشته باش\nبهترین روزها در راهند",
    "زندگی سرشار از فرصت‌هاست\nفقط باید آن‌ها را ببینی",
    "گاهی باید رها کرد\nتا چیزهای بهتر بیایند",
    "به مسیری که رفته‌ای افتخار کن\nهمه‌ی راه‌ها ارزشمندند",
    "قلبت را دنبال کن\nهمیشه راه درست را نشانت می‌دهد",
    "هیچ طوفانی همیشگی نیست\nخورشید دوباره خواهد درخشید",
    "به خودت اعتماد کن\nتو می‌توانی",
    "یک قدم کوچک هم، یک قدم است\nاز جا برخیز",
    "امروز روز توست\nاز آن نهایت استفاده را ببر",
    "زندگی برای زندگی کردن است\nنه برای نگرانی",
    "هر پایانی، شروعی تازه است\nبه استقبالش برو",
    "شروع کن، حتی اگر کامل نباشی\nکامل شدن در مسیر اتفاق می‌افتد",
    "شکست، پایان راه نیست\nفقط یک پیچ در مسیر است",
    "تو همانی که باید باشی\nبه خودت افتخار کن",
    "قلبِ شکسته، درسِ بزرگی دارد\nاز آن محکم‌تر بیرون می‌آیی",
    "هر روز فرصتی برای تغییر است\nاز همین امروز شروع کن",
    "به جای گریه بر گذشته\nبرای آینده لبخند بزن",
    "خودت را دوست داشته باش\nهمان‌طور که هستی",
    "در میان سختی‌ها، زیبایی را ببین\nاین هنر زندگی است",
    "آرام باش، تو در مسیر درستی هستی\nفقط به خودت ایمان داشته باش",
    "آنچه می‌کاری، برداشت می‌کنی\nامروز بذری از خوبی بکار",
    "هر نفسی، هدیه‌ای است\nشکرگزار باش",
    "از اشتباهاتت درس بگیر\nولی خودت را سرزنش نکن",
    "مسیر مهم‌تر از مقصد است\nاز مسیر لذت ببر",
    "آنچه می‌ترسی از آن، در واقع در حال رشد توست\nپیش برو",
    "لبخندت، سلاح مخفی توست\nاز آن استفاده کن",
    "در اوج تردید، ایمان را انتخاب کن\nو پیش برو",
    "زندگی مجموعی از لحظه‌هاست\nاین لحظه را زندگی کن",
    "خودت را ببخش\nتو هم انسان هستی",
    "رشد، در ناپایداری است\nاز منطقه‌ی امن بیرون بیا",
    "هر روز، بهترین روز زندگی است\nاگر خودت بخواهی",
    "هیچ‌کس نمی‌داند فردا چه می‌شود\nولی امروز در دستان توست",
    "با شکوه زندگی کن، نه با ترس\nزندگی برای زندگی کردن است",
    "به دیگران نیکی کن\nدنیا پر از خوبی است",
    "هر صبح، فرصت دوباره است\nآن را هدر نده",
    "تو نور خاص خودت را داری\nآن را پنهان نکن",
    "باور کن که می‌توانی\nچون واقعاً می‌توانی",
    "زندگی، آینه‌ی افکار توست\nفکرت را زیبا کن",
    "از شکست نترس\nبزرگ‌ترین‌ها بارها شکست خورده‌اند",
    "در سکوت، پاسخ‌ها را خواهی یافت\nآرام باش",
    "هر روز خودت را بهتر کن\nحتی یک قدم کوچک",
    "کاری که می‌توانی، امروز انجام بده\nفردا تضمینی نیست",
    "مهربان باش، به خود و به دیگران\nاین زیبایی است",
    "به هیچ‌کس اجازه نده رویاهایت را کوچک کند\nادامه بده",
    "سرنوشت در دستان توست\nانتخاب کن",
    "آرزو نکن، تلاش کن\nآرزو به تنهایی کافی نیست",
    "در دل سختی، فرصت پنهان است\nآن را پیدا کن",
    "خودت را باور کن\nو جهان با تو هماهنگ می‌شود",
    "از چیزی که می‌خواهی دور نشو\nبه سمتش بدو",
    "شادی، انتخابی است\nپس انتخابش کن",
    "زندگی، سفر است نه مسابقه\nآرام پیش برو",
    "هر روز، چیزی برای یادگیری داری\nذهنت را باز نگه دار",
    "بخشش، آزادی می‌آورد\nخودت را آزاد کن",
    "به خودت فرصت بده\nرشد، زمان می‌خواهد",
    "درون تو، قدرتی نهفته است\nآن را بیدار کن",
    "زندگی، زیبایی در سادگی است\nساده ببین",
    "بهترین سرمایه‌گذاری، خودت هستی\nروی خودت کار کن",
    "با عشق زندگی کن\nعشق، همه چیز را زیبا می‌کند",
    "از هیچ چیز پشیمان نباش\nهمه چیز درسی بوده",
    "با شور و اشتیاق زندگی کن\nشور، زندگی را زیبا می‌کند",
    "امروز، خودت را وقف رویاهایت کن\nآن‌ها ارزشش را دارند",
    "سفر هزار مایل، با یک قدم آغاز می‌شود\nپس شروع کن",
    "به جای حسرت، شکرگزاری کن\nزندگی همین است",
    "سرعتت مهم نیست\nجهت مهم است",
    "در انتظار بهترین‌ها باش\nولی برای آن‌ها تلاش کن",
    "زندگی، یک فرصت است\nآن را قدر بدان",
    "به خودت احترام بگذار\nتو ارزش آن را داری",
    "از مسیر لذت ببر، نه از مقصد\nاین راز شادی است",
    "در مقابل سختی‌ها، تسلیم نشو\nتو قوی‌تر هستی",
    "هر صبح، یک هدیه است\nشکرگزار باش",
    "با اعتماد به نفس پیش برو\nجهان راه را برایت باز می‌کند",
    "هیچ محدودیتی وجود ندارد\nفقط ذهن توست",
    "با توکل پیش برو\nراه هموار می‌شود",
    "تو می‌توانی، چون باور داری\nایمانت قوی است",
    "بهترین روزهای زندگی، هنوز نیامده‌اند\nامیدوار باش",
    "از شکست نترس\nاین پل موفقیت است",
    "روی خودت سرمایه‌گذاری کن\nارزشمندترین کار",
    "در هر شرایطی، زیبایی را ببین\nاین قدرت توست",
    "خودت را همان‌طور که هستی بپذیر\nکامل باش",
    "با آرامش، مسیرت را ادامه بده\nآرامش، قدرت است",
    "تو یک معجزه هستی\nباور کن",
    "هیچ چیز غیرممکن نیست\nفقط ذهن باید باور کند",
    "در هر چالشی، فرصت رشد است\nبه دنبال آن باش",
    "زندگی، ساده‌تر از آن است که فکر می‌کنی\nساده بگیر",
    "با شجاعت قدم بردار\nشجاعت، در انتخاب است",
    "از خودت مراقبت کن\nتو مهم‌ترین شخص زندگی خودت هستی",
    "با امید زندگی کن\nامید، راه را روشن می‌کند",
    "خودت را محدود نکن\nتو بی‌نهایت توانمندی",
    "به آینه نگاه کن و بگو\nمن می‌توانم",
    "در سخت‌ترین لحظات، قوی‌ترین خودت باش\nاین هنر است",
    "با لبخند، جهان را زیباتر کن\nلبخند مسری است",
    "برای رویاهایت بجنگ\nچون ارزشش را دارند",
    "در هر روز، یه معجزه است\nآن را ببین",
    "با انگیزه بیدار شو\nروزت را زیبا کن",
    "از دیروز بهتر باش\nاین معنای پیشرفت است",
    "خودت را وقف چیزی کن\nزندگی هدفمند، زیباست",
    "به مسیری که می‌روی ایمان داشته باش\nادامه بده",
    "در دنیای پرهیاهو، آرام بمان\nآرامش، گنج است",
    "با عشق به خودت، شروع کن\nعشق به خود، پایه‌ی همه چیز است",
    "درون خودت را کشف کن\nگنجینه‌ای در تو نهفته",
    "هر روز، یک فرصت طلایی است\nآن را غنیمت بشمار",
    "با توکل و تلاش، به هدف برس\nترکیب برنده همین است",
    "در هر لحظه، انتخابی داری\nانتخاب‌هایت را آگاهانه بگیر",
    "خودت را بالا ببر\nپرواز کن",
    "با آرامش، مشکلات حل می‌شوند\nآرام باش",
    "از خودت بپرس: بهترین نسخه‌ی من کیست؟\nو آن شو",
    "با لبخند، زندگی را رنگین کن\nرنگین‌کمانی در توست",
    "در تاریکی، نور خودت باش\nبدرخش",
    "به خودت اعتماد کن\nتو قوی‌تر از آنی که فکر می‌کنی",
    "برای خودت وقت بگذار\nارزشمندترین سرمایه‌گذاری",
    "با امید، زندگی را ادامه بده\nامید، سوخت زندگی است",
    "هر روز، یک هدیه است\nشکرگزار باش",
    "از تجربه‌هایت استفاده کن\nآن‌ها گنج هستند",
    "با قلب، تصمیم بگیر\nقلب دروغ نمی‌گوید",
    "به خودت عشق بورز\nعشق به خود، اولین قدم است",
    "با اهداف روشن پیش برو\nهدف، ستاره‌ی راه است",
    "در هر قدم، شکرگزار باش\nشکرگزاری، زندگی را زیبا می‌کند",
    "از زندگیت لذت ببر\nاین لحظه، تنها لحظه‌ای است که داری",
    "با اعتماد، به خودت ایمان داشته باش\nایمان، قدرتمندترین نیروست",
    "هر چالشی، یک فرصت است\nفقط باور کن",
    "با آرامش، به پیش برو\nآرامش، اوج قدرت است",
    "خودت را از هیچ‌کس کمتر ندان\nهمه برابرند",
    "با امید، به فردا لبخند بزن\nفردا، امروزِ دیروز است",
    "از شکست‌هایت، پله بساز\nبه سوی موفقیت",
    "در هر لحظه، بهترین خودت باش\nچون این زندگی توست",
    "با عشق به دیگران، به خودت عشق بورز\nعشق، چرخه‌ای است",
    "از نعمت‌های زندگیت شکرگزار باش\nشکرگزاری، آرامش می‌آورد",
    "با توکل به خدا، آرام باش\nآرامش، بزرگ‌ترین ثروت است",
    "در هر سختی، فرجی هست\nاین وعده‌ی الهی است",
    "خودت را از قفس ذهن آزاد کن\nآزادی، در ذهن است",
    "با شکرگزاری، روزت را آغاز کن\nشکر، درهای خوبی را باز می‌کند",
    "هر روز یک شروع جدید است\nاز امروز بهترین خودت باش",
]

_cache = {}
CACHE_TTL = 1800
BATCH_CACHE_TTL = 1800

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


def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            chat_id TEXT PRIMARY KEY,
            title TEXT,
            send_hour INTEGER DEFAULT 0,
            send_minute INTEGER DEFAULT 1,
            added_at TEXT
        )
    """)
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('default_hour', '0')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('default_minute', '1')")
    conn.commit()
    conn.close()


def get_setting(key, default=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else default


def set_setting(key, value):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
              (key, str(value)))
    conn.commit()
    conn.close()


def add_channel(chat_id, title=""):
    default_hour = int(get_setting("default_hour", "0"))
    default_minute = int(get_setting("default_minute", "1"))
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO channels
        (chat_id, title, send_hour, send_minute, added_at)
        VALUES (?, ?, ?, ?, ?)
    """, (str(chat_id), title, default_hour, default_minute,
          datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_channels():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT chat_id, title, send_hour, send_minute FROM channels")
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


def get_channel_time(chat_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT send_hour, send_minute FROM channels WHERE chat_id = ?",
              (str(chat_id),))
    row = c.fetchone()
    conn.close()
    if row:
        return row[0], row[1]
    return 0, 1


def set_channel_time(chat_id, hour, minute):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        UPDATE channels SET send_hour = ?, send_minute = ?
        WHERE chat_id = ?
    """, (int(hour), int(minute), str(chat_id)))
    conn.commit()
    conn.close()


def delete_channel(chat_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM channels WHERE chat_id = ?", (str(chat_id),))
    conn.commit()
    conn.close()


async def check_channel_status(bot, chat_id):
    try:
        chat = await bot.get_chat(chat_id)
    except Exception as e:
        error_str = str(e).lower()
        if "chat not found" in error_str:
            return "error", (
                "وضعیت: خطا در شناسایی\n\n"
                "کانال مورد نظر یافت نشد.\n\n"
                "دلایل احتمالی:\n"
                "• شناسه وارد شده نامعتبر است\n"
                "• ربات عضو کانال نیست\n"
                "• کانال خصوصی است و ربات به آن دسترسی ندارد"
            )
        return "error", (
            f"وضعیت: خطای غیرمنتظره\n\n"
            f"جزئیات: {str(e)[:200]}"
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
                f"کانال: «{chat.title or chat_id}»\n\n"
                f"ربات در این کانال دارای دسترسی مدیریتی نیست.\n"
                f"لطفاً جهت فعال‌سازی، مراحل زیر را انجام دهید:\n\n"
                f"۱. ورود به تنظیمات کانال\n"
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
                f"کانال: «{chat.title or chat_id}»\n\n"
                f"ربات در این کانال دارای دسترسی مدیریتی است، "
                f"اما مجوز ارسال پیام برای آن فعال نشده است.\n"
                f"لطفاً دسترسی «ارسال پیام» را فعال نمایید."
            )

        return "ok", (
            f"وضعیت: تأیید شد\n\n"
            f"کانال: «{chat.title or chat_id}»\n\n"
            f"ربات با موفقیت به فهرست کانال‌ها افزوده شد."
        )

    except Exception as e:
        error_str = str(e).lower()

        if "member list is inaccessible" in error_str or "chat admin" in error_str:
            return "not_admin", (
                f"وضعیت: عدم دسترسی مدیریتی\n\n"
                f"کانال: «{chat.title or chat_id}»\n\n"
                f"ربات در این کانال دارای دسترسی مدیریتی نیست.\n"
                f"لطفاً جهت فعال‌سازی، مراحل زیر را انجام دهید:\n\n"
                f"۱. ورود به تنظیمات کانال\n"
                f"۲. بخش مدیریت (Administrators)\n"
                f"۳. افزودن ربات به عنوان مدیر\n"
                f"۴. فعال‌سازی دسترسی «ارسال پیام»"
            )

        return "error", (
            f"وضعیت: خطای غیرمنتظره\n\n"
            f"جزئیات: {str(e)[:200]}"
        )


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


def get_todays_motivation():
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    day_of_year = now.timetuple().tm_yday
    index = (day_of_year - 1) % len(MOTIVATIONAL_MESSAGES)
    return MOTIVATIONAL_MESSAGES[index]


def get_weather():
    results = []

    desc_map = {
        "Sunny": "آفتابی ☀️",
        "Clear": "صاف ☀️",
        "Partly cloudy": "نیمه‌ابری 🌤️",
        "Cloudy": "ابری ☁️",
        "Overcast": "ابری ☁️",
        "Mist": "مه 🌫️",
        "Fog": "مه 🌫️",
        "Freezing fog": "مه یخ‌زده 🌫️",
        "Haze": "مه‌آلود 🌫️",
        "Smoky haze": "دودآلود 🌫️",
        "Light rain": "باران سبک 🌦️",
        "Light drizzle": "نم‌نم 🌦️",
        "Light rain shower": "باران سبک 🌦️",
        "Patchy rain nearby": "باران پراکنده 🌦️",
        "Patchy rain possible": "احتمال باران 🌦️",
        "Patchy light rain": "باران سبک پراکنده 🌦️",
        "Rain": "باران 🌧️",
        "Moderate rain": "باران 🌧️",
        "Heavy rain": "باران شدید 🌧️",
        "Moderate or heavy rain shower": "باران شدید 🌧️",
        "Snow": "برف 🌨️",
        "Light snow": "برف سبک 🌨️",
        "Blowing snow": "برف همراه با باد 🌨️",
        "Blizzard": "کولاک ❄️",
        "Thunderstorm": "رعد و برق ⛈️",
    }

    for city_fa, city_en in CITIES.items():
        try:
            url = f"https://wttr.in/{city_en}?format=j1"
            resp = requests.get(
                url,
                timeout=10,
                headers={"User-Agent": "curl/7.68.0"}
            )
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
            f"&method=7"
            f"&timezone=Asia/Tehran"
        )
        resp = requests.get(url, timeout=8)
        data = resp.json()

        if data.get("code") != 200:
            return None

        timings = data["data"]["timings"]
        return {
            "fajr": timings.get("Fajr", "—"),
            "sunrise": timings.get("Sunrise", "—"),
            "dhuhr": timings.get("Dhuhr", "—"),
            "maghrib": timings.get("Maghrib", "—"),
            "isha": timings.get("Isha", "—"),
        }
    except Exception as e:
        print(f"Prayer: {e}")
        return None


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
                    data = resp.json()
                    p = float(data.get("price", 0))
                    if p > 0:
                        price = p
                        break
                except Exception as e:
                    print(f"Binance {symbol} attempt {attempt + 1}: {e}")

        if price is None:
            try:
                url = f"https://api.coinpaprika.com/v1/tickers/{crypto['coinpaprika_id']}"
                resp = requests.get(url, timeout=6)
                data = resp.json()
                price = data.get("quotes", {}).get("USD", {}).get("price")
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
            key = "usdt-rls"
            if key in stats:
                price_rls = stats[key].get("latest")
                if price_rls:
                    price_toman = int(float(price_rls)) // 10
                    _cache_set(cache_key, price_toman)
                    return price_toman
    except Exception as e:
        print(f"USDT Nobitex: {e}")

    return None


def get_crypto_price_toman(nobitex_symbol, usd_price=None):
    if not nobitex_symbol:
        return None

    cache_key = f"toman_{nobitex_symbol}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        url = "https://apiv2.nobitex.ir/market/stats"
        params = {"srcCurrency": nobitex_symbol, "dstCurrency": "rls"}
        resp = requests.get(url, params=params, timeout=8)
        data = resp.json()

        if data.get("status") == "ok":
            stats = data.get("stats", {})
            key = f"{nobitex_symbol}-rls"
            if key in stats:
                price_rls = stats[key].get("latest")
                if price_rls:
                    price_toman = int(float(price_rls)) // 10
                    _cache_set(cache_key, price_toman)
                    return price_toman
    except Exception as e:
        print(f"Nobitex {nobitex_symbol}: {e}")

    if usd_price and nobitex_symbol != "usdt":
        usdt_toman = _get_usdt_toman()
        if usdt_toman:
            price_toman = int(usd_price * usdt_toman)
            _cache_set(cache_key, price_toman)
            return price_toman

    return None


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

    usdt_toman = _get_usdt_toman()

    if usdt_toman:
        gold_toman = int(gold_usd_per_gram * usdt_toman)
        result = {
            "price_usd": gold_usd_per_gram,
            "price_toman": gold_toman,
        }
        _cache_set(cache_key, result)
        return result

    return None


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
            meta = result[0].get("meta", {})
            price = meta.get("regularMarketPrice")
            if price:
                price = float(price)
                if price > 0:
                    _cache_set(cache_key, price)
                    return price
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

    try:
        url = "https://api.exchangerate.host/latest?base=USD&symbols=BRENT"
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=10)
        data = resp.json()

        rates = data.get("rates", {})
        if "BRENT" in rates:
            price = float(rates["BRENT"])
            if price > 0:
                _cache_set(cache_key, price)
                return price
    except Exception as e:
        print(f"Exchangerate BRENT: {e}")

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

    for crypto in CRYPTOS:
        symbol = crypto["symbol"]
        gid = crypto["coingecko_id"]

        usd = usd_prices.get(gid)
        toman = get_crypto_price_toman(crypto["nobitex"], usd)

        usd_str = f"${format_price(usd)}" if usd else "—"
        toman_str = f"{toman:,} تومان" if toman else "—"

        lines.append(f"{symbol}: {usd_str} / {toman_str}")

    gold = get_gold_price_18k()
    if gold:
        lines.append(
            f"GOLD (18): ${format_price(gold['price_usd'])} / "
            f"{gold['price_toman']:,} تومان"
        )
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
    motivation = get_todays_motivation()

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

    text += "\n━━━━━━━━━━━━━━\n\n"
    text += f"💫 {motivation}"

    return text


def schedule_channel(chat_id, hour, minute):
    global scheduler

    if scheduler is None:
        return

    job_id = f"channel_{chat_id}"

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
        print(f"زمان‌بندی {chat_id} - {hour:02d}:{minute:02d}")
    except Exception as e:
        print(f"خطا در زمان‌بندی {chat_id}: {e}")


async def send_to_channel(chat_id):
    global app_telegram
    if not app_telegram:
        return
    try:
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
    for chat_id, title, hour, minute in channels:
        schedule_channel(chat_id, hour, minute)

    print(f"Scheduler started - {len(channels)} channels")


def restart_channel_job(chat_id):
    hour, minute = get_channel_time(chat_id)
    schedule_channel(chat_id, hour, minute)


def back_button(target="menu_back"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت به منوی قبل", callback_data=target)]
    ])


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(
            "این ربات تنها برای مدیر سامانه قابل استفاده است."
        )
        return
    await update.message.reply_text(
        "برای دسترسی به پنل مدیریت، از دستور /admin استفاده نمایید."
    )


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ افزودن کانال", callback_data="menu_add")],
        [InlineKeyboardButton("📋 مدیریت کانال‌ها", callback_data="menu_list")],
        [InlineKeyboardButton("📤 ارسال فوری", callback_data="menu_sendnow")],
    ])


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text(
        "مدیریت ربات\n\n"
        "لطفاً یکی از گزینه‌های زیر را انتخاب نمایید:",
        reply_markup=main_menu_keyboard()
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    data = query.data

    if data == "menu_add":
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
                "مدیریت کانال‌ها\n\n"
                "در حال حاضر هیچ کانالی ثبت نشده است.",
                reply_markup=back_button("menu_back")
            )
            return

        keyboard = []
        for cid, title, hour, minute in channels:
            display = title if title else cid
            keyboard.append([
                InlineKeyboardButton(
                    f"📢 {display} ({hour:02d}:{minute:02d})",
                    callback_data=f"ch_{cid}"
                )
            ])
        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu_back")])

        await query.edit_message_text(
            "مدیریت کانال‌ها\n\n"
            "لطفاً کانال مورد نظر را انتخاب نمایید:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "menu_sendnow":
        channels = get_channels()
        if not channels:
            await query.edit_message_text(
                "ارسال فوری\n\n"
                "در حال حاضر هیچ کانالی ثبت نشده است.",
                reply_markup=back_button("menu_back")
            )
            return

        keyboard = []
        for cid, title, hour, minute in channels:
            display = title if title else cid
            keyboard.append([
                InlineKeyboardButton(f"📢 {display}", callback_data=f"sendnow_{cid}")
            ])
        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu_back")])

        await query.edit_message_text(
            "ارسال فوری\n\n"
            "لطفاً کانال مورد نظر را انتخاب نمایید:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("ch_"):
        chat_id = data.replace("ch_", "")
        title = get_channel_title(chat_id)
        display = title if title else chat_id
        hour, minute = get_channel_time(chat_id)
        current_time = f"{hour:02d}:{minute:02d}"

        keyboard = [
            [InlineKeyboardButton("📤 ارسال فوری", callback_data=f"sendnow_{chat_id}")],
            [InlineKeyboardButton(
                f"⏰ تنظیم زمان (فعلی: {current_time})",
                callback_data=f"settime_{chat_id}"
            )],
            [InlineKeyboardButton("🗑️ حذف کانال", callback_data=f"del_{chat_id}")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="menu_list")],
        ]

        await query.edit_message_text(
            f"کانال: «{display}»\n\n"
            f"زمان ارسال: {current_time}\n\n"
            f"لطفاً عملیات مورد نظر را انتخاب نمایید:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("settime_"):
        chat_id = data.replace("settime_", "")
        title = get_channel_title(chat_id)
        display = title if title else chat_id
        hour, minute = get_channel_time(chat_id)
        current_time = f"{hour:02d}:{minute:02d}"

        context.user_data["awaiting"] = "channel_time"
        context.user_data["channel_id"] = chat_id

        await query.edit_message_text(
            f"تنظیم زمان ارسال\n\n"
            f"کانال: «{display}»\n"
            f"زمان فعلی: {current_time}\n\n"
            f"لطفاً زمان جدید را به قالب HH:MM ارسال نمایید.\n"
            f"نمونه: 08:00",
            reply_markup=back_button(f"ch_{chat_id}")
        )

    elif data.startswith("del_"):
        chat_id = data.replace("del_", "")
        delete_channel(chat_id)

        job_id = f"channel_{chat_id}"
        if job_id in channel_jobs:
            try:
                channel_jobs[job_id].remove()
                del channel_jobs[job_id]
            except Exception:
                pass

        await query.edit_message_text(
            "وضعیت: انجام شد\n\n"
            "کانال مورد نظر از فهرست حذف گردید.",
            reply_markup=back_button("menu_list")
        )

    elif data.startswith("sendnow_"):
        chat_id = data.replace("sendnow_", "")
        try:
            message = await asyncio.to_thread(build_daily_message)
            await context.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode="Markdown"
            )
            await query.edit_message_text(
                "وضعیت: انجام شد\n\n"
                "پیام مورد نظر با موفقیت ارسال گردید.",
                reply_markup=back_button("menu_sendnow")
            )
        except Exception as e:
            await query.edit_message_text(
                f"وضعیت: خطا در ارسال\n\n"
                f"جزئیات: {str(e)[:200]}",
                reply_markup=back_button("menu_sendnow")
            )

    elif data == "menu_back":
        await query.edit_message_text(
            "مدیریت ربات\n\n"
            "لطفاً یکی از گزینه‌های زیر را انتخاب نمایید:",
            reply_markup=main_menu_keyboard()
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
                "وضعیت: خطا در پردازش\n\n"
                "لینک وارد شده نامعتبر است.",
                reply_markup=back_button("menu_add")
            )
            return

    chat_id = channel_input

    await update.message.reply_text("در حال بررسی کانال...")

    status, message = await check_channel_status(context.bot, chat_id)

    if status == "ok":
        try:
            chat = await context.bot.get_chat(chat_id)

            title = chat.title
            if not title:
                title = getattr(chat, "full_name", None)
            if not title:
                title = getattr(chat, "username", None)
            if not title:
                title = str(chat_id)

            add_channel(chat_id, title=title)
            await update.message.reply_text(
                message,
                reply_markup=back_button("menu_back")
            )

            hour, minute = get_channel_time(chat_id)
            schedule_channel(chat_id, hour, minute)
        except Exception as e:
            await update.message.reply_text(
                f"وضعیت: خطا در ذخیره‌سازی\n\n"
                f"جزئیات: {str(e)[:200]}",
                reply_markup=back_button("menu_add")
            )
    else:
        await update.message.reply_text(
            message,
            reply_markup=back_button("menu_add")
        )


async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    awaiting = context.user_data.get("awaiting")
    text = update.message.text.strip()

    if awaiting == "channel_id":
        await process_channel_id(update, context, text)
        context.user_data["awaiting"] = None

    elif awaiting == "channel_time":
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

            set_channel_time(chat_id, hour, minute)

            title = get_channel_title(chat_id)
            display = title if title else chat_id

            context.user_data["awaiting"] = None
            context.user_data["channel_id"] = None

            await update.message.reply_text(
                f"وضعیت: انجام شد\n\n"
                f"زمان ارسال کانال «{display}» به {hour:02d}:{minute:02d} تغییر یافت.",
                reply_markup=back_button(f"ch_{chat_id}")
            )

            restart_channel_job(chat_id)

        except Exception:
            await update.message.reply_text(
                "وضعیت: خطا در پردازش\n\n"
                "قالب زمان نامعتبر است. لطفاً به صورت HH:MM ارسال نمایید.\n"
                "نمونه: 08:00",
                reply_markup=back_button(f"ch_{chat_id}")
            )


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
            process_update_async(update_data),
            main_loop
        )

        return "OK", 200
    except Exception as e:
        print(f"Webhook error: {e}")
        return "OK", 200


async def main_async():
    global app_telegram, main_loop

    main_loop = asyncio.get_running_loop()

    init_db()

    app_telegram = ApplicationBuilder().token(BOT_TOKEN).build()

    app_telegram.add_handler(CommandHandler("start", start_command))
    app_telegram.add_handler(CommandHandler("admin", admin_panel))
    app_telegram.add_handler(CallbackQueryHandler(button_handler))
    app_telegram.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_admin_text
    ))

    await app_telegram.initialize()
    await app_telegram.start()

    start_scheduler()

    if RENDER_URL:
        webhook_url = f"{RENDER_URL}/webhook"
        try:
            await app_telegram.bot.delete_webhook(drop_pending_updates=True)
            await app_telegram.bot.set_webhook(
                url=webhook_url,
                drop_pending_updates=True,
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