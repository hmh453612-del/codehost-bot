#!/usr/bin/env python3
"""
================================================================================
🚀 Ultra-Pro Telegram Bot Hosting Platform (GitHub & Render Deployment Flow)
================================================================================
Bot Username : @Hcncnv_bot
Bot Token    : 8854070679:AAHPLz-lCQ2PUzkzFvfYqcSv9iKqs9Ywgfw
Admin ID     : 7751147021
================================================================================
"""

import os
import sys
import time
import shutil
import asyncio
import logging
import psutil
import aiosqlite
from datetime import datetime
from aiohttp import web

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ------------------------------------------------------------------------------
# 1. CONFIGURATION & ENVIRONMENT SETUP
# ------------------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "8854070679:AAHPLz-lCQ2PUzkzFvfYqcSv9iKqs9Ywgfw")
BOT_USERNAME = os.getenv("BOT_USERNAME", "Hcncnv_bot")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7751147021"))

DEFAULT_BKASH = os.getenv("BKASH_NUMBER", "01781412911")
DEFAULT_NAGAD = os.getenv("NAGAD_NUMBER", "01778782993")
DEFAULT_ROCKET = os.getenv("ROCKET_NUMBER", "01778782993")

DEFAULT_REFERRAL_POINTS = int(os.getenv("REFERRAL_POINTS", 30))
DEFAULT_BOT_COST = int(os.getenv("BOT_REQUIRED_POINTS", 100))
DATABASE_PATH = os.getenv("DATABASE_PATH", "hosting_bot.db")
BOTS_BASE_DIR = os.getenv("BOTS_BASE_DIR", "/tmp/hosted_bots")
PORT = int(os.getenv("PORT", 8080))

logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("CodeHostPro")

USER_STATES = {}
RUNNING_PROCESSES = {}
START_TIME = time.time()

# ------------------------------------------------------------------------------
# 2. DATABASE INITIALIZATION
# ------------------------------------------------------------------------------
async def init_db():
    os.makedirs(BOTS_BASE_DIR, exist_ok=True)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        
        # Users Table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                points INTEGER DEFAULT 0,
                referral_code TEXT UNIQUE,
                referred_by INTEGER,
                referral_count INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Bots Table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                runtime TEXT NOT NULL,
                status TEXT DEFAULT 'stopped',
                pid INTEGER DEFAULT NULL,
                project_dir TEXT,
                main_file TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            );
        """)

        # Deposits Table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                method TEXT NOT NULL,
                amount REAL NOT NULL,
                points INTEGER NOT NULL,
                transaction_id TEXT UNIQUE NOT NULL,
                status TEXT DEFAULT 'pending',
                rejection_reason TEXT,
                admin_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            );
        """)

        # Support Tickets
        await db.execute("""
            CREATE TABLE IF NOT EXISTS support_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                reply TEXT,
                status TEXT DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Settings
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)

        default_settings = [
            ("referral_points", str(DEFAULT_REFERRAL_POINTS)),
            ("bot_cost", str(DEFAULT_BOT_COST)),
            ("bkash_number", DEFAULT_BKASH),
            ("nagad_number", DEFAULT_NAGAD),
            ("rocket_number", DEFAULT_ROCKET),
            ("maintenance_mode", "0"),
        ]
        for key, val in default_settings:
            await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?);", (key, val))

        await db.commit()
    logger.info("Database initialized successfully.")

async def get_setting(key: str, default: str = "") -> str:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else default

async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()

# ------------------------------------------------------------------------------
# 3. RUNNER ENGINE & PROCESS ISOLATION
# ------------------------------------------------------------------------------
class BotRunner:
    @staticmethod
    def get_user_dir(user_id: int) -> str:
        path = os.path.join(BOTS_BASE_DIR, f"user_{user_id}")
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    async def start_bot(bot_id: int, user_id: int, runtime: str, main_file: str) -> tuple[bool, str]:
        user_dir = BotRunner.get_user_dir(user_id)
        log_file_path = os.path.join(user_dir, "runtime.log")
        file_path = os.path.join(user_dir, main_file)

        if not os.path.exists(file_path):
            return False, f"Entry file `{main_file}` is missing."

        await BotRunner.stop_bot(bot_id)

        # 1. Dependency Installation (Render / GitHub Action Style)
        if runtime.lower() == "python":
            req_path = os.path.join(user_dir, "requirements.txt")
            if os.path.exists(req_path):
                try:
                    with open(log_file_path, "a", encoding="utf-8") as f:
                        f.write(f"\n--- [ 📦 INSTALLING DEPENDENCIES: requirements.txt ] ---\n")
                    proc_install = await asyncio.create_subprocess_exec(
                        sys.executable, "-m", "pip", "install", "-r", req_path,
                        stdout=open(log_file_path, "a", encoding="utf-8"),
                        stderr=open(log_file_path, "a", encoding="utf-8"),
                    )
                    await proc_install.communicate()
                except Exception as e:
                    logger.error(f"Pip install error for user {user_id}: {e}")

            cmd = [sys.executable, "-u", main_file]

        elif runtime.lower() == "node":
            pkg_path = os.path.join(user_dir, "package.json")
            if os.path.exists(pkg_path):
                try:
                    with open(log_file_path, "a", encoding="utf-8") as f:
                        f.write(f"\n--- [ 📦 INSTALLING DEPENDENCIES: npm install ] ---\n")
                    proc_install = await asyncio.create_subprocess_exec(
                        "npm", "install",
                        cwd=user_dir,
                        stdout=open(log_file_path, "a", encoding="utf-8"),
                        stderr=open(log_file_path, "a", encoding="utf-8"),
                    )
                    await proc_install.communicate()
                except Exception as e:
                    logger.error(f"Npm install error for user {user_id}: {e}")

            cmd = ["node", main_file]
        else:
            return False, "Unknown runtime selected."

        # 2. Main Process Execution
        try:
            log_fd = open(log_file_path, "a", encoding="utf-8")
            log_fd.write(f"\n--- [ 🚀 BOT STARTED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ] ---\n")
            log_fd.flush()

            process = psutil.Popen(
                cmd,
                cwd=user_dir,
                stdout=log_fd,
                stderr=log_fd,
                env=dict(os.environ),
            )
            RUNNING_PROCESSES[bot_id] = process

            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute(
                    "UPDATE bots SET status = 'running', pid = ? WHERE id = ?",
                    (process.pid, bot_id)
                )
                await db.commit()

            return True, "Bot started successfully."
        except Exception as e:
            logger.error(f"Process start error: {e}")
            return False, str(e)

    @staticmethod
    async def stop_bot(bot_id: int) -> bool:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT pid, user_id FROM bots WHERE id = ?", (bot_id,)) as cur:
                row = await cur.fetchone()
                if not row:
                    return False
                stored_pid, user_id = row

        proc = RUNNING_PROCESSES.pop(bot_id, None)
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        if stored_pid:
            try:
                p = psutil.Process(stored_pid)
                p.terminate()
                p.wait(timeout=2)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("UPDATE bots SET status = 'stopped', pid = NULL WHERE id = ?", (bot_id,))
            await db.commit()

        if user_id:
            user_dir = BotRunner.get_user_dir(user_id)
            log_file = os.path.join(user_dir, "runtime.log")
            if os.path.exists(log_file):
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"\n--- [ ⏹ BOT STOPPED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ] ---\n")
        return True

    @staticmethod
    def get_logs(user_id: int, max_lines: int = 40) -> str:
        user_dir = BotRunner.get_user_dir(user_id)
        log_file = os.path.join(user_dir, "runtime.log")
        if not os.path.exists(log_file):
            return "No logs available yet."
        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                return "".join(lines[-max_lines:]) if lines else "Log file is currently empty."
        except Exception as e:
            return f"Error reading logs: {str(e)}"

    @staticmethod
    async def delete_bot(bot_id: int, user_id: int):
        await BotRunner.stop_bot(bot_id)
        user_dir = BotRunner.get_user_dir(user_id)
        if os.path.exists(user_dir):
            try:
                shutil.rmtree(user_dir)
            except Exception as e:
                logger.error(f"Error removing user directory: {e}")

        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("DELETE FROM bots WHERE id = ?", (bot_id,))
            await db.commit()

# ------------------------------------------------------------------------------
# 4. KEYBOARD BUILDERS
# ------------------------------------------------------------------------------
def get_main_keyboard(user_id: int):
    buttons = [
        [KeyboardButton("🚀 Deploy Bot"), KeyboardButton("🤖 My Bot")],
        [KeyboardButton("👤 My Account"), KeyboardButton("🎁 Referral")],
        [KeyboardButton("💰 Deposit"), KeyboardButton("💎 My Points")],
        [KeyboardButton("📊 Statistics"), KeyboardButton("🆘 Support")],
    ]
    if user_id == ADMIN_ID:
        buttons.append([KeyboardButton("⚙️ Ultra Admin Panel")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_back_inline(callback_data: str = "cancel_action"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=callback_data)]])

# ------------------------------------------------------------------------------
# 5. USER /start & NAVIGATION
# ------------------------------------------------------------------------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    m_mode = await get_setting("maintenance_mode", "0")
    if m_mode == "1" and user.id != ADMIN_ID:
        await update.effective_message.reply_html("<b>🔧 Maintenance Mode Active.</b>\nPlease check back later.")
        return

    ref_by = None
    args = context.args
    if args and len(args) > 0 and args[0].startswith("ref_"):
        try:
            candidate = int(args[0].split("_")[1])
            if candidate != user.id:
                ref_by = candidate
        except ValueError:
            pass

    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT id, is_banned FROM users WHERE telegram_id = ?", (user.id,)) as cur:
            user_row = await cur.fetchone()

        if user_row:
            if user_row[1] == 1:
                await update.effective_message.reply_html("<b>🚫 Your account is suspended.</b>")
                return
        else:
            ref_reward = int(await get_setting("referral_points", str(DEFAULT_REFERRAL_POINTS)))
            await db.execute(
                """
                INSERT INTO users (telegram_id, username, first_name, points, referral_code, referred_by)
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (user.id, user.username or "", user.first_name or "User", str(user.id), ref_by),
            )
            await db.commit()

            if ref_by:
                async with db.execute("SELECT telegram_id FROM users WHERE telegram_id = ?", (ref_by,)) as cur:
                    referrer = await cur.fetchone()
                if referrer:
                    await db.execute(
                        "UPDATE users SET points = points + ?, referral_count = referral_count + 1 WHERE telegram_id = ?",
                        (ref_reward, ref_by)
                    )
                    await db.commit()
                    try:
                        await context.bot.send_message(
                            chat_id=ref_by,
                            text=f"<b>🎁 New Referral!</b>\n\nUser <b>{user.first_name}</b> joined with your link!\n"
                                 f"💎 You earned <b>+{ref_reward} Points</b>.",
                            parse_mode=ParseMode.HTML,
                        )
                    except Exception:
                        pass

    ref_pts = await get_setting("referral_points", str(DEFAULT_REFERRAL_POINTS))
    bot_pts = await get_setting("bot_cost", str(DEFAULT_BOT_COST))

    welcome_msg = (
        "<b>🚀 Welcome to CodeHost Platform</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "💻 <b>24/7 Cloud Telegram Bot Hosting</b>\n"
        "🟢 <b>Node.js (LTS) Support</b>\n"
        "🐍 <b>Python 3 Support</b>\n"
        "📦 <b>requirements.txt & bot.py Auto-Build</b>\n"
        "⚡ Instant Deployment & Live Console Logs\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎁 Referral Reward: <b>{ref_pts} Points</b>\n"
        f"🤖 Bot Slot Cost: <b>{bot_pts} Points</b>\n\n"
        "👇 <i>Select an option from the menu below:</i>"
    )
    await update.effective_message.reply_html(welcome_msg, reply_markup=get_main_keyboard(user.id))

async def account_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute(
            """
            SELECT points, referral_count, created_at,
                   (SELECT COUNT(*) FROM bots WHERE user_id = ? AND status = 'running'),
                   (SELECT COALESCE(SUM(amount), 0) FROM deposits WHERE user_id = ? AND status = 'approved')
            FROM users WHERE telegram_id = ?
            """,
            (user_id, user_id, user_id)
        ) as cur:
            row = await cur.fetchone()

    if not row:
        await update.effective_message.reply_text("Please send /start first.")
        return

    points, refs, joined, active_bots, total_dep = row
    joined_date = str(joined).split(" ")[0] if joined else "N/A"

    text = (
        "<b>👤 My Account Dashboard</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"👤 <b>Username:</b> @{update.effective_user.username or 'None'}\n\n"
        f"💎 <b>Points Balance:</b> <code>{points}</code> Points\n"
        f"🎁 <b>Total Referrals:</b> <code>{refs}</code>\n"
        f"🤖 <b>Active Bots:</b> <code>{active_bots}</code>\n"
        f"💰 <b>Total Deposited:</b> ৳{total_dep:.2f}\n"
        f"📅 <b>Joined:</b> {joined_date}\n\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    await update.effective_message.reply_html(text)

async def referral_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ref_pts = await get_setting("referral_points", str(DEFAULT_REFERRAL_POINTS))
    bot_info = await context.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"

    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT referral_count FROM users WHERE telegram_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()

    ref_count = row[0] if row else 0
    earned = ref_count * int(ref_pts)

    text = (
        "<b>🎁 Referral Program</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Total Referrals:</b> <code>{ref_count}</code>\n"
        f"💎 <b>Earned Points:</b> <code>{earned}</code> Points\n\n"
        f"🎯 <b>Per Referral Reward:</b> <b>+{ref_pts} Points</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🔗 <b>Your Personal Invite Link:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        "💡 <i>Share this link to earn free hosting points!</i>"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={ref_link}&text=Host%20your%20Telegram%20Bot%20for%20Free!")]
    ])
    await update.effective_message.reply_html(text, reply_markup=kb)

async def points_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT points FROM users WHERE telegram_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
    pts = row[0] if row else 0
    bot_cost = await get_setting("bot_cost", str(DEFAULT_BOT_COST))

    text = (
        "<b>💎 Points Balance & Rates</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 <b>Current Balance:</b> <b>{pts} Points</b>\n"
        f"🤖 <b>Bot Deployment Cost:</b> <b>{bot_cost} Points</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📌 <i>How to get more points:</i>\n"
        "1. 🎁 Invite friends (Referral Link)\n"
        "2. 💰 Deposit via bKash / Nagad / Rocket\n"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Deposit Points", callback_data="btn_deposit"),
         InlineKeyboardButton("🎁 Refer Friends", callback_data="btn_referral")]
    ])
    await update.effective_message.reply_html(text, reply_markup=kb)

# ------------------------------------------------------------------------------
# 6. GITHUB & RENDER STYLE MULTI-STEP BOT DEPLOYMENT FLOW
# ------------------------------------------------------------------------------
async def deploy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    m_mode = await get_setting("maintenance_mode", "0")
    if m_mode == "1" and user_id != ADMIN_ID:
        await update.effective_message.reply_html("<b>🔧 Bot deployment is temporarily paused.</b>")
        return

    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT points FROM users WHERE telegram_id = ?", (user_id,)) as cur:
            u_row = await cur.fetchone()
        async with db.execute("SELECT id FROM bots WHERE user_id = ?", (user_id,)) as cur:
            b_row = await cur.fetchone()

    points = u_row[0] if u_row else 0
    bot_cost = int(await get_setting("bot_cost", str(DEFAULT_BOT_COST)))

    if b_row:
        await update.effective_message.reply_html(
            "<b>❌ Active Bot Slot Full</b>\n\n"
            "আপনার একটি Bot ইতিমধ্যে স্লটে হোস্ট করা আছে।\n"
            "নতুন Bot ডিপ্লয় করতে হলে <b>'🤖 My Bot'</b> থেকে বর্তমান Bot ডিলিট করুন।"
        )
        return

    if points < bot_cost:
        await update.effective_message.reply_html(
            "<b>❌ Insufficient Points</b>\n\n"
            f"Bot Deploy করতে <b>{bot_cost} Points</b> প্রয়োজন।\n"
            f"আপনার ব্যালেন্স: <b>{points} Points</b>\n\n"
            "🎁 Referral করে অথবা 💰 Deposit করে ব্যালেন্স যুক্ত করুন।"
        )
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🐍 Python 3 (requirements.txt + bot.py)", callback_data="runtime_python")],
        [InlineKeyboardButton("🟢 Node.js (package.json + index.js)", callback_data="runtime_node")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")]
    ])
    text = (
        "<b>🤖 Select Bot Environment</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Render / GitHub স্টাইলে আপনার বট ডিপ্লয় করুন:\n\n"
        "• <b>Python 3:</b> ধাপ ১: <code>requirements.txt</code> ➔ ধাপ ২: <code>bot.py</code>\n"
        "• <b>Node.js:</b> ধাপ ১: <code>package.json</code> ➔ ধাপ ২: <code>index.js</code>\n\n"
        f"💎 <b>Slot Cost:</b> {bot_cost} Points (Once per active bot)"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.effective_message.reply_html(text, reply_markup=kb)

# ------------------------------------------------------------------------------
# 7. BOT MANAGEMENT DASHBOARD
# ------------------------------------------------------------------------------
async def my_bot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT id, runtime, status, main_file, created_at FROM bots WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()

    if not row:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Deploy Bot Now", callback_data="btn_deploy")]])
        text = "<b>🤖 No Active Bot Found</b>\n\nআপনি এখনো কোনো Bot হোস্ট করেননি।"
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            await update.effective_message.reply_html(text, reply_markup=kb)
        return

    bot_id, runtime, status, main_file, created = row
    status_emoji = "🟢 Running" if status == "running" else "🔴 Stopped"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Start", callback_data=f"botact_start_{bot_id}"),
         InlineKeyboardButton("⏹ Stop", callback_data=f"botact_stop_{bot_id}")],
        [InlineKeyboardButton("🔄 Restart", callback_data=f"botact_restart_{bot_id}"),
         InlineKeyboardButton("📋 Live Logs", callback_data=f"botact_logs_{bot_id}")],
        [InlineKeyboardButton("🗑 Delete Bot", callback_data=f"botact_confirmdel_{bot_id}")],
    ])

    text = (
        "<b>🤖 My Bot Management</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Status:</b> {status_emoji}\n"
        f"<b>Runtime:</b> <code>{runtime.upper()}</code>\n"
        f"<b>Entry File:</b> <code>{main_file}</code>\n"
        f"<b>Deployed At:</b> {created}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Live process control buttons:"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.effective_message.reply_html(text, reply_markup=kb)

# ------------------------------------------------------------------------------
# 8. DEPOSIT SYSTEM
# ------------------------------------------------------------------------------
async def deposit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bkash = await get_setting("bkash_number", DEFAULT_BKASH)
    nagad = await get_setting("nagad_number", DEFAULT_NAGAD)
    rocket = await get_setting("rocket_number", DEFAULT_ROCKET)

    text = (
        "<b>💰 Deposit & Add Points</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Personal Payment Numbers (Send Money):</b>\n"
        f"💚 <b>bKash:</b> <code>{bkash}</code>\n"
        f"🟠 <b>Nagad:</b> <code>{nagad}</code>\n"
        f"🔵 <b>Rocket:</b> <code>{rocket}</code>\n\n"
        "💵 <b>Rate:</b> ৳1 = 1 Point\n"
        "⚠️ <i>টাকা পাঠানোর পর Transaction ID সাবমিট করুন।</i>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "👇 <b>Select payment method:</b>"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💚 bKash", callback_data="dep_method_bKash"),
         InlineKeyboardButton("🟠 Nagad", callback_data="dep_method_Nagad")],
        [InlineKeyboardButton("🔵 Rocket", callback_data="dep_method_Rocket")],
    ])
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.effective_message.reply_html(text, reply_markup=kb)

async def deposit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("dep_method_"):
        method = data.split("_")[2]
        USER_STATES[query.from_user.id] = {"state": "AWAITING_DEP_AMOUNT", "method": method}
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("৳50 (50 Pts)", callback_data="dep_amt_50"),
             InlineKeyboardButton("৳100 (100 Pts)", callback_data="dep_amt_100")],
            [InlineKeyboardButton("৳200 (200 Pts)", callback_data="dep_amt_200"),
             InlineKeyboardButton("৳500 (500 Pts)", callback_data="dep_amt_500")],
            [InlineKeyboardButton("✏️ Custom Amount", callback_data="dep_amt_custom")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")]
        ])
        await query.edit_message_text(
            f"<b>Payment Method:</b> <b>{method}</b>\n\nSelect deposit amount:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )

    elif data.startswith("dep_amt_"):
        amt_str = data.split("_")[2]
        if amt_str == "custom":
            USER_STATES[query.from_user.id]["state"] = "AWAITING_DEP_CUSTOM_AMOUNT"
            await query.edit_message_text(
                "✍️ <b>Enter custom amount (in BDT):</b>\n\nExample: <code>150</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_inline("btn_deposit")
            )
        else:
            amount = float(amt_str)
            USER_STATES[query.from_user.id]["amount"] = amount
            USER_STATES[query.from_user.id]["state"] = "AWAITING_DEP_TRX"
            await query.edit_message_text(
                f"💵 <b>Selected Amount:</b> ৳{amount}\n"
                f"💳 <b>Method:</b> {USER_STATES[query.from_user.id]['method']}\n\n"
                "🧾 <b>Please send your Transaction ID (TrxID):</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_inline("btn_deposit")
            )

# ------------------------------------------------------------------------------
# 9. ULTRA ADMIN PANEL (WORLD-CLASS SUITE)
# ------------------------------------------------------------------------------
async def admin_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            total_users = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM deposits WHERE status = 'pending'") as cur:
            pending_dep = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM bots WHERE status = 'running'") as cur:
            running_bots = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM support_tickets WHERE status = 'open'") as cur:
            open_tickets = (await cur.fetchone())[0]

    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    uptime_sec = int(time.time() - START_TIME)
    uptime_str = f"{uptime_sec // 3600}h {(uptime_sec % 3600) // 60}m {uptime_sec % 60}s"

    text = (
        "<b>⚙️ ULTRA ADMIN CONTROL SUITE</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Total Users:</b> <code>{total_users}</code>\n"
        f"🤖 <b>Running Bots:</b> <code>{running_bots}</code>\n"
        f"🟡 <b>Pending Deposits:</b> <code>{pending_dep}</code>\n"
        f"🎫 <b>Open Support Tickets:</b> <code>{open_tickets}</code>\n\n"
        "📊 <b>Server Metrics:</b>\n"
        f"• CPU Load: <code>{cpu}%</code> | RAM: <code>{ram}%</code>\n"
        f"• Server Uptime: <code>{uptime_str}</code>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "👇 <i>Select an administrative module:</i>"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 User Explorer", callback_data="adm_view_users"),
         InlineKeyboardButton("🤖 Bot Fleet", callback_data="adm_view_bots")],
        [InlineKeyboardButton(f"💰 Pending Deposits ({pending_dep})", callback_data="adm_view_deposits")],
        [InlineKeyboardButton(f"🎫 Support Inbox ({open_tickets})", callback_data="adm_view_tickets"),
         InlineKeyboardButton("📢 Broadcast", callback_data="adm_prompt_broadcast")],
        [InlineKeyboardButton("⚙️ Dynamic Settings", callback_data="adm_view_settings"),
         InlineKeyboardButton("🔄 Refresh", callback_data="adm_refresh_stats")],
    ])

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.effective_message.reply_html(text, reply_markup=kb)

# ------------------------------------------------------------------------------
# 10. ADMIN CALLBACK ROUTING & ACTIONS
# ------------------------------------------------------------------------------
async def admin_callback_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id

    if user_id != ADMIN_ID:
        await query.answer("Access Denied.", show_alert=True)
        return

    if data in ["adm_refresh_stats", "adm_main_menu"]:
        await admin_panel_handler(update, context)
        return

    if data == "adm_view_users":
        USER_STATES[user_id] = {"state": "ADM_SEARCH_USER"}
        await query.edit_message_text(
            "<b>👥 User Explorer & Modifier</b>\n\n"
            "ইউজার খুঁজে পেতে <b>Telegram User ID</b> অথবা <b>@Username</b> লিখে পাঠান:\n\n"
            "Example: <code>123456789</code> or <code>@username</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_inline("adm_main_menu")
        )
        return

    if data == "adm_view_bots":
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT id, user_id, runtime, status FROM bots ORDER BY id DESC LIMIT 15") as cur:
                bots = await cur.fetchall()

        if not bots:
            await query.edit_message_text("<b>🤖 No bots currently deployed.</b>", parse_mode=ParseMode.HTML, reply_markup=get_back_inline("adm_main_menu"))
            return

        kb = []
        for b_id, u_id, rt, st in bots:
            ico = "🟢" if st == "running" else "🔴"
            kb.append([InlineKeyboardButton(f"{ico} #{b_id} | User: {u_id} ({rt.upper()})", callback_data=f"adm_botinfo_{b_id}")])
        kb.append([InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="adm_main_menu")])
        await query.edit_message_text("<b>🤖 Bot Fleet (Latest 15):</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("adm_botinfo_"):
        b_id = int(data.split("_")[2])
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT id, user_id, runtime, status, main_file, created_at, pid FROM bots WHERE id = ?", (b_id,)) as cur:
                bot_info = await cur.fetchone()

        if not bot_info:
            await query.answer("Bot not found.", show_alert=True)
            return

        _, b_uid, b_rt, b_st, b_file, b_created, b_pid = bot_info
        st_ico = "🟢 Running" if b_st == "running" else "🔴 Stopped"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 View Console Logs", callback_data=f"adm_botlog_{b_id}"),
             InlineKeyboardButton("🔄 Force Restart", callback_data=f"adm_botrestart_{b_id}")],
            [InlineKeyboardButton("⏹ Force Stop", callback_data=f"adm_botstop_{b_id}"),
             InlineKeyboardButton("🗑 Force Delete", callback_data=f"adm_botdel_{b_id}")],
            [InlineKeyboardButton("👤 View Owner Profile", callback_data=f"adm_userinfo_{b_uid}")],
            [InlineKeyboardButton("⬅️ Back to Fleet", callback_data="adm_view_bots")]
        ])

        text = (
            f"<b>🤖 Bot Fleet Inspection: #{b_id}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>Owner ID:</b> <code>{b_uid}</code>\n"
            f"⚙️ <b>Runtime:</b> <code>{b_rt.upper()}</code>\n"
            f"📄 <b>Entry File:</b> <code>{b_file}</code>\n"
            f"📊 <b>Status:</b> {st_ico} (PID: {b_pid or 'N/A'})\n"
            f"📅 <b>Created:</b> {b_created}\n\n"
            "━━━━━━━━━━━━━━━━━━"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data.startswith("adm_botrestart_"):
        b_id = int(data.split("_")[2])
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT id, user_id, runtime, main_file FROM bots WHERE id = ?", (b_id,)) as cur:
                row = await cur.fetchone()
        if row:
            await BotRunner.stop_bot(b_id)
            await asyncio.sleep(1)
            ok, msg = await BotRunner.start_bot(row[0], row[1], row[2], row[3])
            await query.answer(f"Bot #{b_id} restarted: {msg}", show_alert=True)
            await admin_callback_dispatcher(update, context)
        return

    if data.startswith("adm_botstop_"):
        b_id = int(data.split("_")[2])
        await BotRunner.stop_bot(b_id)
        await query.answer(f"Bot #{b_id} stopped.", show_alert=True)
        await admin_callback_dispatcher(update, context)
        return

    if data.startswith("adm_botdel_"):
        b_id = int(data.split("_")[2])
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT user_id FROM bots WHERE id = ?", (b_id,)) as cur:
                row = await cur.fetchone()
        if row:
            await BotRunner.delete_bot(b_id, row[0])
            await query.answer(f"Bot #{b_id} deleted permanently.", show_alert=True)
            await admin_panel_handler(update, context)
        return

    if data.startswith("adm_botlog_"):
        b_id = int(data.split("_")[2])
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT user_id FROM bots WHERE id = ?", (b_id,)) as cur:
                row = await cur.fetchone()
        if row:
            logs = BotRunner.get_logs(row[0], max_lines=35)
            if len(logs) > 3500:
                logs = logs[-3500:]
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"adm_botinfo_{b_id}")]])
            await query.edit_message_text(f"<b>📋 Logs for Bot #{b_id}:</b>\n\n<pre>{logs}</pre>", parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "adm_view_deposits":
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT id, user_id, method, amount, transaction_id FROM deposits WHERE status = 'pending' ORDER BY id DESC") as cur:
                deps = await cur.fetchall()

        if not deps:
            await query.edit_message_text("<b>✅ No pending deposits right now.</b>", parse_mode=ParseMode.HTML, reply_markup=get_back_inline("adm_main_menu"))
            return

        kb = []
        for d_id, u_id, meth, amt, trx in deps:
            kb.append([
                InlineKeyboardButton(f"#{d_id} | ৳{amt} ({meth})", callback_data=f"adm_userinfo_{u_id}"),
                InlineKeyboardButton("✅", callback_data=f"adm_dep_app_{d_id}"),
                InlineKeyboardButton("❌", callback_data=f"adm_dep_rej_{d_id}"),
            ])
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="adm_main_menu")])
        await query.edit_message_text("<b>💰 Pending Deposits:</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("adm_dep_"):
        parts = data.split("_")
        decision = parts[2]
        dep_id = int(parts[3])

        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT user_id, amount, points, transaction_id, status FROM deposits WHERE id = ?", (dep_id,)) as cur:
                dep_row = await cur.fetchone()

        if not dep_row:
            await query.answer("Deposit not found.", show_alert=True)
            return

        target_uid, amount, pts, trx, status = dep_row

        if status != "pending":
            await query.answer(f"Already {status}.", show_alert=True)
            return

        if decision == "app":
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("UPDATE deposits SET status = 'approved', admin_id = ? WHERE id = ?", (user_id, dep_id))
                await db.execute("UPDATE users SET points = points + ? WHERE telegram_id = ?", (pts, target_uid))
                await db.commit()

            await query.edit_message_text(f"<b>✅ Deposit #{dep_id} Approved!</b>\nTrx: <code>{trx}</code> | +{pts} Points", parse_mode=ParseMode.HTML)
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=f"<b>✅ Deposit Approved!</b>\n\n"
                         f"💵 <b>Amount:</b> ৳{amount}\n"
                         f"💎 <b>Added Points:</b> +{pts} Points\n"
                         f"🧾 <b>TrxID:</b> <code>{trx}</code>",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

        elif decision == "rej":
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("UPDATE deposits SET status = 'rejected', admin_id = ?, rejection_reason = 'Invalid TrxID' WHERE id = ?", (user_id, dep_id))
                await db.commit()

            await query.edit_message_text(f"<b>❌ Deposit #{dep_id} Rejected.</b>", parse_mode=ParseMode.HTML)
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=f"<b>❌ Deposit Rejected</b>\n\nYour deposit of <b>৳{amount}</b> (Trx: <code>{trx}</code>) was rejected by Admin.",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
        return

    if data == "adm_view_tickets":
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT id, user_id, message FROM support_tickets WHERE status = 'open' ORDER BY id DESC") as cur:
                tickets = await cur.fetchall()

        if not tickets:
            await query.edit_message_text("<b>✅ No open support tickets.</b>", parse_mode=ParseMode.HTML, reply_markup=get_back_inline("adm_main_menu"))
            return

        kb = []
        for t_id, u_id, msg in tickets:
            short_msg = (msg[:20] + '...') if len(msg) > 20 else msg
            kb.append([InlineKeyboardButton(f"🎫 #{t_id} (User: {u_id}) - {short_msg}", callback_data=f"adm_ticket_view_{t_id}")])
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="adm_main_menu")])
        await query.edit_message_text("<b>🎫 Open Support Tickets:</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("adm_ticket_view_"):
        t_id = int(data.split("_")[3])
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT id, user_id, message, created_at FROM support_tickets WHERE id = ?", (t_id,)) as cur:
                ticket = await cur.fetchone()
        if not ticket:
            await query.answer("Ticket not found.")
            return

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✍️ Reply Now", callback_data=f"adm_ticket_prompt_{t_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="adm_view_tickets")]
        ])
        await query.edit_message_text(
            f"<b>🎫 Ticket #{ticket[0]} Details</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>User ID:</b> <code>{ticket[1]}</code>\n"
            f"📅 <b>Created:</b> {ticket[3]}\n\n"
            f"📝 <b>Message:</b>\n{ticket[2]}\n\n"
            "━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
        return

    if data.startswith("adm_ticket_prompt_"):
        t_id = int(data.split("_")[3])
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT user_id FROM support_tickets WHERE id = ?", (t_id,)) as cur:
                row = await cur.fetchone()
        if row:
            USER_STATES[user_id] = {"state": "ADM_REPLY_TICKET", "ticket_id": t_id, "user_id": row[0]}
            await query.edit_message_text(f"✍️ <b>Ticket #{t_id}-এর রিপ্লাই লিখে পাঠান:</b>", parse_mode=ParseMode.HTML, reply_markup=get_back_inline(f"adm_ticket_view_{t_id}"))
        return

    if data == "adm_prompt_broadcast":
        USER_STATES[user_id] = {"state": "ADM_BROADCAST"}
        await query.edit_message_text("<b>📢 ব্রডকাস্ট মেসেজ লিখে পাঠান (HTML Supported):</b>", parse_mode=ParseMode.HTML, reply_markup=get_back_inline("adm_main_menu"))
        return

    if data == "adm_view_settings":
        bkash = await get_setting("bkash_number", DEFAULT_BKASH)
        nagad = await get_setting("nagad_number", DEFAULT_NAGAD)
        rocket = await get_setting("rocket_number", DEFAULT_ROCKET)
        ref_p = await get_setting("referral_points", str(DEFAULT_REFERRAL_POINTS))
        bot_c = await get_setting("bot_cost", str(DEFAULT_BOT_COST))
        m_mode = await get_setting("maintenance_mode", "0")
        m_text = "🟢 OFF" if m_mode == "0" else "🔴 ON"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"💚 bKash: {bkash}", callback_data="adm_set_bkash"),
             InlineKeyboardButton(f"🟠 Nagad: {nagad}", callback_data="adm_set_nagad")],
            [InlineKeyboardButton(f"🔵 Rocket: {rocket}", callback_data="adm_set_rocket")],
            [InlineKeyboardButton(f"🎁 Ref Reward: {ref_p} Pts", callback_data="adm_set_refpts"),
             InlineKeyboardButton(f"🤖 Bot Cost: {bot_c} Pts", callback_data="adm_set_botcost")],
            [InlineKeyboardButton(f"🔧 Maintenance: {m_text} (Toggle)", callback_data="adm_toggle_maintenance")],
            [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="adm_main_menu")]
        ])

        await query.edit_message_text("<b>⚙️ Dynamic Platform Settings:</b>", parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data.startswith("adm_set_"):
        field = data.split("_")[2]
        USER_STATES[user_id] = {"state": "ADM_UPDATE_SETTING", "field": field}
        await query.edit_message_text(f"✍️ <b>Setting <code>{field}</code>-এর নতুন মান লিখে পাঠান:</b>", parse_mode=ParseMode.HTML, reply_markup=get_back_inline("adm_view_settings"))
        return

    if data == "adm_toggle_maintenance":
        cur_m = await get_setting("maintenance_mode", "0")
        new_m = "1" if cur_m == "0" else "0"
        await set_setting("maintenance_mode", new_m)
        await query.answer(f"Maintenance mode {'ON' if new_m == '1' else 'OFF'}", show_alert=True)
        await admin_panel_handler(update, context)
        return

    if data.startswith("adm_userinfo_"):
        target_uid = int(data.split("_")[2])
        await display_admin_user_profile(query, target_uid)
        return

    if data.startswith("adm_uact_"):
        _, _, action, target_uid_str = data.split("_")
        target_uid = int(target_uid_str)

        if action == "addpts":
            USER_STATES[user_id] = {"state": "ADM_ADD_PTS", "target_uid": target_uid}
            await query.edit_message_text(f"✍️ <b>User {target_uid}-এর জন্য কত Points যোগ করতে চান লিখুন:</b>", parse_mode=ParseMode.HTML, reply_markup=get_back_inline(f"adm_userinfo_{target_uid}"))

        elif action == "dedpts":
            USER_STATES[user_id] = {"state": "ADM_DED_PTS", "target_uid": target_uid}
            await query.edit_message_text(f"✍️ <b>User {target_uid}-এর থেকে কত Points কাটতে চান লিখুন:</b>", parse_mode=ParseMode.HTML, reply_markup=get_back_inline(f"adm_userinfo_{target_uid}"))

        elif action == "setpts":
            USER_STATES[user_id] = {"state": "ADM_SET_PTS", "target_uid": target_uid}
            await query.edit_message_text(f"✍️ <b>User {target_uid}-এর ব্যালেন্স সরাসরি কত Points করতে চান লিখুন:</b>", parse_mode=ParseMode.HTML, reply_markup=get_back_inline(f"adm_userinfo_{target_uid}"))

        elif action == "notify":
            USER_STATES[user_id] = {"state": "ADM_NOTIFY_USER", "target_uid": target_uid}
            await query.edit_message_text(f"✉️ <b>User {target_uid}-কে যে নোটিফিকেশন পাঠাতে চান তা লিখুন:</b>", parse_mode=ParseMode.HTML, reply_markup=get_back_inline(f"adm_userinfo_{target_uid}"))

        elif action == "toggleban":
            async with aiosqlite.connect(DATABASE_PATH) as db:
                async with db.execute("SELECT is_banned FROM users WHERE telegram_id = ?", (target_uid,)) as cur:
                    row = await cur.fetchone()
                if row:
                    new_ban = 0 if row[0] == 1 else 1
                    await db.execute("UPDATE users SET is_banned = ? WHERE telegram_id = ?", (new_ban, target_uid))
                    await db.commit()
                    await query.answer(f"User is now {'BANNED' if new_ban == 1 else 'ACTIVE'}", show_alert=True)
                    await display_admin_user_profile(query, target_uid)
        return

async def display_admin_user_profile(query, target_uid: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute(
            """
            SELECT telegram_id, username, first_name, points, referral_count, is_banned, created_at,
                   (SELECT COUNT(*) FROM bots WHERE user_id = ?),
                   (SELECT COALESCE(SUM(amount), 0) FROM deposits WHERE user_id = ? AND status = 'approved')
            FROM users WHERE telegram_id = ?
            """,
            (target_uid, target_uid, target_uid)
        ) as cur:
            user_row = await cur.fetchone()

    if not user_row:
        await query.edit_message_text(f"❌ User <code>{target_uid}</code> not found.", parse_mode=ParseMode.HTML, reply_markup=get_back_inline("adm_view_users"))
        return

    tid, uname, fname, pts, refs, banned, joined, bot_count, total_dep = user_row
    status_str = "🔴 Banned" if banned == 1 else "🟢 Active"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Points", callback_data=f"adm_uact_addpts_{tid}"),
         InlineKeyboardButton("➖ Deduct Points", callback_data=f"adm_uact_dedpts_{tid}")],
        [InlineKeyboardButton("✏️ Set Balance", callback_data=f"adm_uact_setpts_{tid}"),
         InlineKeyboardButton("✉️ Send Alert", callback_data=f"adm_uact_notify_{tid}")],
        [InlineKeyboardButton("🚫 Ban / Unban", callback_data=f"adm_uact_toggleban_{tid}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="adm_view_users")]
    ])

    text = (
        f"<b>👤 User Profile: {fname}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{tid}</code>\n"
        f"👤 <b>Username:</b> @{uname or 'None'}\n"
        f"📊 <b>Status:</b> {status_str}\n\n"
        f"💎 <b>Points:</b> <code>{pts}</code> | 🎁 <b>Refs:</b> <code>{refs}</code>\n"
        f"🤖 <b>Hosted Bots:</b> <code>{bot_count}</code>\n"
        f"💰 <b>Total Deposits:</b> ৳{total_dep:.2f}\n"
        f"📅 <b>Joined:</b> {joined}\n\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

# ------------------------------------------------------------------------------
# 11. GENERAL CALLBACK DISPATCHER (DEPLOY & BOT ACTIONS)
# ------------------------------------------------------------------------------
async def callback_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id

    if data.startswith("adm_"):
        await admin_callback_dispatcher(update, context)
        return

    if data == "cancel_action":
        USER_STATES.pop(user_id, None)
        await query.answer("Cancelled.")
        await query.edit_message_text("❌ Action cancelled.")
        return

    if data == "btn_deploy":
        await query.answer()
        await deploy_menu(update, context)
        return

    if data == "btn_deposit":
        await query.answer()
        await deposit_menu(update, context)
        return

    if data == "btn_referral":
        await query.answer()
        await referral_handler(update, context)
        return

    # STEP 1: Select Runtime -> Prompt for requirements.txt (or package.json)
    if data.startswith("runtime_"):
        runtime = data.split("_")[1]
        USER_STATES[user_id] = {
            "state": "AWAITING_REQUIREMENTS",
            "runtime": runtime,
        }
        user_dir = BotRunner.get_user_dir(user_id)
        shutil.rmtree(user_dir, ignore_errors=True)
        os.makedirs(user_dir, exist_ok=True)

        req_name = "requirements.txt" if runtime == "python" else "package.json"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"⏭ Skip ({req_name} নেই)", callback_data="deploy_skip_requirements")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")]
        ])

        await query.answer()
        await query.edit_message_text(
            f"<b>📦 {runtime.upper()} Deployment (Step 1/2)</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"আপনার প্রজেক্টের ডিপেন্ডেন্সি ফাইল <code>{req_name}</code> আপলোড করুন অথবা টেক্সট আকারে লিখে পাঠান।\n\n"
            f"💡 <i>আপনার যদি কোনো অতিরিক্ত লাইব্রেরি না লাগে, তবে নিচের <b>'⏭ Skip'</b> বাটনে ক্লিক করুন।</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
        return

    # STEP 2: Skip requirements -> Prompt for main bot file (bot.py or index.js)
    if data == "deploy_skip_requirements":
        state_data = USER_STATES.get(user_id)
        if not state_data:
            await query.answer("Session expired.", show_alert=True)
            return

        runtime = state_data["runtime"]
        state_data["state"] = "AWAITING_MAIN_FILE"
        main_file = "bot.py" if runtime == "python" else "index.js"

        await query.answer()
        await query.edit_message_text(
            f"<b>📄 {runtime.upper()} Deployment (Step 2/2)</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"এখন আপনার মূল ফাইল <code>{main_file}</code> আপলোড করুন অথবা সম্পূর্ণ কোডটি মেসেজ আকারে লিখে পাঠান।",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_inline("cancel_action")
        )
        return

    # Bot Process Controller Handlers
    if data.startswith("botact_"):
        parts = data.split("_")
        action = parts[1]
        bot_id = int(parts[2])

        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT id, user_id, runtime, main_file FROM bots WHERE id = ?", (bot_id,)) as cur:
                bot_info = await cur.fetchone()

        if not bot_info or (bot_info[1] != user_id and user_id != ADMIN_ID):
            await query.answer("Unauthorized.", show_alert=True)
            return

        b_id, u_id, runtime, main_file = bot_info

        if action == "start":
            await query.answer("Starting bot...")
            ok, msg = await BotRunner.start_bot(b_id, u_id, runtime, main_file)
            await query.answer(msg, show_alert=not ok)
            await my_bot_handler(update, context)

        elif action == "stop":
            await query.answer("Stopping bot...")
            await BotRunner.stop_bot(b_id)
            await query.answer("Bot stopped.")
            await my_bot_handler(update, context)

        elif action == "restart":
            await query.answer("Restarting bot...")
            await BotRunner.stop_bot(b_id)
            await asyncio.sleep(1)
            ok, msg = await BotRunner.start_bot(b_id, u_id, runtime, main_file)
            await query.answer("Restarted!" if ok else f"Failed: {msg}", show_alert=not ok)
            await my_bot_handler(update, context)

        elif action == "logs":
            await query.answer()
            logs = BotRunner.get_logs(u_id, max_lines=35)
            if len(logs) > 3500:
                logs = logs[-3500:]
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh Logs", callback_data=f"botact_logs_{bot_id}"),
                                         InlineKeyboardButton("⬅️ Back", callback_data="menu_mybot")]])
            await query.edit_message_text(
                f"<b>📋 Live Console Logs:</b>\n\n<pre>{logs}</pre>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb
            )

        elif action == "confirmdel":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Yes, Delete", callback_data=f"botact_dodelete_{bot_id}")],
                [InlineKeyboardButton("❌ Cancel", callback_data="menu_mybot")]
            ])
            await query.edit_message_text(
                "<b>⚠️ Delete Bot Confirmation</b>\n\n"
                "Are you sure you want to permanently delete your bot?",
                parse_mode=ParseMode.HTML,
                reply_markup=kb
            )

        elif action == "dodelete":
            await BotRunner.delete_bot(b_id, u_id)
            await query.answer("Deleted.", show_alert=True)
            await query.edit_message_text("<b>🗑 Bot permanently deleted.</b>", parse_mode=ParseMode.HTML)

    elif data == "menu_mybot":
        await my_bot_handler(update, context)

# ------------------------------------------------------------------------------
# 12. MESSAGE HANDLER & BUILD DISPATCHER
# ------------------------------------------------------------------------------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    text = update.message.text or ""
    state_data = USER_STATES.get(user.id)

    # 1. Main Navigation Keyboard
    if text == "🚀 Deploy Bot":
        await deploy_menu(update, context)
        return
    elif text == "🤖 My Bot":
        await my_bot_handler(update, context)
        return
    elif text == "👤 My Account":
        await account_handler(update, context)
        return
    elif text == "🎁 Referral":
        await referral_handler(update, context)
        return
    elif text == "💰 Deposit":
        await deposit_menu(update, context)
        return
    elif text == "💎 My Points":
        await points_handler(update, context)
        return
    elif text == "📊 Statistics":
        await statistics_handler(update, context)
        return
    elif text == "🆘 Support":
        await support_prompt_handler(update, context)
        return
    elif text == "⚙️ Ultra Admin Panel" and user.id == ADMIN_ID:
        await admin_panel_handler(update, context)
        return

    # 2. Document & File Handling (Step 1: requirements.txt / Step 2: bot.py)
    if update.message.document:
        doc = update.message.document
        filename = doc.file_name or "file.txt"
        user_dir = BotRunner.get_user_dir(user.id)
        file_obj = await context.bot.get_file(doc.file_id)
        dest_path = os.path.join(user_dir, filename)
        await file_obj.download_to_drive(dest_path)

        if state_data:
            state = state_data.get("state")
            runtime = state_data["runtime"]

            # Step 1: requirements.txt received via Document
            if state == "AWAITING_REQUIREMENTS":
                req_standard = "requirements.txt" if runtime == "python" else "package.json"
                if filename != req_standard:
                    os.rename(dest_path, os.path.join(user_dir, req_standard))

                state_data["state"] = "AWAITING_MAIN_FILE"
                main_file = "bot.py" if runtime == "python" else "index.js"
                await update.effective_message.reply_html(
                    f"✅ <code>{req_standard}</code> সংরক্ষিত হয়েছে!\n\n"
                    f"<b>📄 Step 2/2:</b> এখন আপনার মূল ফাইল <code>{main_file}</code> আপলোড করুন অথবা সম্পূর্ণ কোডটি মেসেজ আকারে লিখে পাঠান।"
                )
                return

            # Step 2: bot.py received via Document -> Trigger Deployment
            if state == "AWAITING_MAIN_FILE":
                main_standard = "bot.py" if runtime == "python" else "index.js"
                if filename != main_standard:
                    os.rename(dest_path, os.path.join(user_dir, main_standard))

                await launch_user_deployment(update, context, user.id, runtime, main_standard)
                return

        await update.effective_message.reply_html(f"✅ File <code>{filename}</code> saved to sandbox folder.")
        return

    # 3. Text Message Handling for FSM States
    if state_data:
        state = state_data.get("state")

        # Step 1: requirements.txt received as Text
        if state == "AWAITING_REQUIREMENTS" and text:
            runtime = state_data["runtime"]
            req_standard = "requirements.txt" if runtime == "python" else "package.json"
            user_dir = BotRunner.get_user_dir(user.id)
            with open(os.path.join(user_dir, req_standard), "w", encoding="utf-8") as f:
                f.write(text)

            state_data["state"] = "AWAITING_MAIN_FILE"
            main_file = "bot.py" if runtime == "python" else "index.js"
            await update.effective_message.reply_html(
                f"✅ <code>{req_standard}</code> তৈরি হয়েছে!\n\n"
                f"<b>📄 Step 2/2:</b> এখন আপনার মূল ফাইল <code>{main_file}</code> আপলোড করুন অথবা সম্পূর্ণ কোডটি মেসেজ আকারে লিখে পাঠান。"
            )
            return

        # Step 2: bot.py received as Text -> Trigger Deployment
        if state == "AWAITING_MAIN_FILE" and text:
            runtime = state_data["runtime"]
            main_standard = "bot.py" if runtime == "python" else "index.js"
            user_dir = BotRunner.get_user_dir(user.id)
            with open(os.path.join(user_dir, main_standard), "w", encoding="utf-8") as f:
                f.write(text)

            await launch_user_deployment(update, context, user.id, runtime, main_standard)
            return

        # Deposit: Custom Amount
        if state == "AWAITING_DEP_CUSTOM_AMOUNT":
            try:
                amt = float(text.strip())
                if amt <= 0:
                    raise ValueError
                state_data["amount"] = amt
                state_data["state"] = "AWAITING_DEP_TRX"
                await update.effective_message.reply_html(
                    f"💵 <b>Amount:</b> ৳{amt}\n"
                    f"💳 <b>Method:</b> {state_data['method']}\n\n"
                    "🧾 <b>Please send your Transaction ID:</b>"
                )
            except ValueError:
                await update.effective_message.reply_text("❌ Please enter a valid number.")
            return

        # Deposit: TrxID
        if state == "AWAITING_DEP_TRX":
            trx = text.strip().upper()
            if len(trx) < 4:
                await update.effective_message.reply_text("❌ Invalid Transaction ID.")
                return

            method = state_data["method"]
            amount = state_data["amount"]
            points = int(amount)

            async with aiosqlite.connect(DATABASE_PATH) as db:
                async with db.execute("SELECT id FROM deposits WHERE transaction_id = ?", (trx,)) as cur:
                    dup = await cur.fetchone()
                if dup:
                    await update.effective_message.reply_html("<b>⚠️ This Transaction ID is already submitted.</b>")
                    USER_STATES.pop(user.id, None)
                    return

                await db.execute(
                    "INSERT INTO deposits (user_id, method, amount, points, transaction_id, status) VALUES (?, ?, ?, ?, ?, 'pending')",
                    (user.id, method, amount, points, trx)
                )
                await db.commit()
                async with db.execute("SELECT last_insert_rowid()") as cur:
                    dep_id = (await cur.fetchone())[0]

            USER_STATES.pop(user.id, None)
            await update.effective_message.reply_html(
                "<b>⏳ Deposit Request Submitted!</b>\n\n"
                f"🧾 <b>Transaction ID:</b> <code>{trx}</code>\n"
                f"💵 <b>Amount:</b> ৳{amount} ({points} Points)\n\n"
                "<i>Admin will verify your payment shortly.</i>"
            )

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Approve", callback_data=f"adm_dep_app_{dep_id}"),
                 InlineKeyboardButton("❌ Reject", callback_data=f"adm_dep_rej_{dep_id}")],
                [InlineKeyboardButton("👤 View User", callback_data=f"adm_userinfo_{user.id}")]
            ])
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"<b>💰 New Deposit (#{dep_id})</b>\n"
                         f"👤 @{user.username or 'None'} (<code>{user.id}</code>)\n"
                         f"💳 {method} | ৳{amount} ({points} Pts)\n"
                         f"🧾 Trx: <code>{trx}</code>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb
                )
            except Exception:
                pass
            return

        # Support message
        if state == "AWAITING_SUPPORT_MSG":
            USER_STATES.pop(user.id, None)
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("INSERT INTO support_tickets (user_id, message) VALUES (?, ?)", (user.id, text))
                await db.commit()
                async with db.execute("SELECT last_insert_rowid()") as cur:
                    ticket_id = (await cur.fetchone())[0]

            await update.effective_message.reply_html(f"<b>🎫 Ticket #{ticket_id} Created</b>\nAdmin has been notified.")
            try:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("✍️ Reply", callback_data=f"adm_ticket_prompt_{ticket_id}")]])
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"<b>🆘 New Support Ticket #{ticket_id}</b>\nFrom: @{user.username or 'None'} (<code>{user.id}</code>)\n\n{text}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb
                )
            except Exception:
                pass
            return

        # Admin FSM Actions
        if user.id == ADMIN_ID:
            if state == "ADM_SEARCH_USER":
                query_str = text.strip().replace("@", "")
                async with aiosqlite.connect(DATABASE_PATH) as db:
                    if query_str.isdigit():
                        async with db.execute("SELECT telegram_id FROM users WHERE telegram_id = ?", (int(query_str),)) as cur:
                            row = await cur.fetchone()
                    else:
                        async with db.execute("SELECT telegram_id FROM users WHERE LOWER(username) = LOWER(?)", (query_str,)) as cur:
                            row = await cur.fetchone()

                USER_STATES.pop(user.id, None)
                if row:
                    class MockQuery:
                        async def edit_message_text(self, text, parse_mode=None, reply_markup=None):
                            await update.effective_message.reply_html(text, reply_markup=reply_markup)
                    await display_admin_user_profile(MockQuery(), row[0])
                else:
                    await update.effective_message.reply_html(f"❌ User '<code>{text}</code>' not found.", reply_markup=get_back_inline("adm_view_users"))
                return

            if state in ["ADM_ADD_PTS", "ADM_DED_PTS", "ADM_SET_PTS"]:
                try:
                    val = int(text.strip())
                    target_uid = state_data["target_uid"]
                    async with aiosqlite.connect(DATABASE_PATH) as db:
                        if state == "ADM_ADD_PTS":
                            await db.execute("UPDATE users SET points = points + ? WHERE telegram_id = ?", (val, target_uid))
                            msg = f"💎 Admin credited <b>+{val} Points</b> to your balance!"
                        elif state == "ADM_DED_PTS":
                            await db.execute("UPDATE users SET points = MAX(0, points - ?) WHERE telegram_id = ?", (val, target_uid))
                            msg = f"⚠️ Admin deducted <b>-{val} Points</b> from your balance."
                        elif state == "ADM_SET_PTS":
                            await db.execute("UPDATE users SET points = ? WHERE telegram_id = ?", (val, target_uid))
                            msg = f"ℹ️ Your points balance has been set to <b>{val} Points</b>."
                        await db.commit()

                    USER_STATES.pop(user.id, None)
                    await update.effective_message.reply_html(f"✅ Balance updated for User <code>{target_uid}</code>!")
                    try:
                        await context.bot.send_message(chat_id=target_uid, text=msg, parse_mode=ParseMode.HTML)
                    except Exception:
                        pass
                except ValueError:
                    await update.effective_message.reply_text("❌ Please enter a valid number.")
                return

            if state == "ADM_NOTIFY_USER":
                target_uid = state_data["target_uid"]
                USER_STATES.pop(user.id, None)
                try:
                    await context.bot.send_message(chat_id=target_uid, text=f"<b>📩 Alert from Administrator:</b>\n\n{text}", parse_mode=ParseMode.HTML)
                    await update.effective_message.reply_html(f"✅ Notification sent to <code>{target_uid}</code>.")
                except Exception as e:
                    await update.effective_message.reply_html(f"❌ Failed to deliver: {e}")
                return

            if state == "ADM_BROADCAST":
                USER_STATES.pop(user.id, None)
                async with aiosqlite.connect(DATABASE_PATH) as db:
                    async with db.execute("SELECT telegram_id FROM users WHERE is_banned = 0") as cur:
                        users = await cur.fetchall()

                status_msg = await update.effective_message.reply_text(f"📢 Broadcasting to {len(users)} users...")
                success, fail = 0, 0
                for u in users:
                    try:
                        await context.bot.send_message(chat_id=u[0], text=f"<b>📢 Announcement:</b>\n\n{text}", parse_mode=ParseMode.HTML)
                        success += 1
                        await asyncio.sleep(0.04)
                    except Exception:
                        fail += 1

                await status_msg.edit_text(f"✅ Broadcast Completed!\nDelivered: {success}\nFailed: {fail}")
                return

            if state == "ADM_REPLY_TICKET":
                t_id = state_data["ticket_id"]
                target_uid = state_data["user_id"]
                USER_STATES.pop(user.id, None)
                async with aiosqlite.connect(DATABASE_PATH) as db:
                    await db.execute("UPDATE support_tickets SET reply = ?, status = 'closed' WHERE id = ?", (text, t_id))
                    await db.commit()

                try:
                    await context.bot.send_message(
                        chat_id=target_uid,
                        text=f"<b>🆘 Support Response (Ticket #{t_id})</b>\n\n👨‍💼 <b>Admin:</b> {text}",
                        parse_mode=ParseMode.HTML
                    )
                    await update.effective_message.reply_html(f"✅ Reply sent for Ticket #{t_id}.")
                except Exception as e:
                    await update.effective_message.reply_html(f"❌ Failed to deliver: {e}")
                return

            if state == "ADM_UPDATE_SETTING":
                field = state_data["field"]
                key_map = {
                    "bkash": "bkash_number",
                    "nagad": "nagad_number",
                    "rocket": "rocket_number",
                    "refpts": "referral_points",
                    "botcost": "bot_cost",
                }
                db_key = key_map.get(field, field)
                await set_setting(db_key, text.strip())
                USER_STATES.pop(user.id, None)
                await update.effective_message.reply_html(f"✅ Setting <code>{db_key}</code> updated to: <b>{text.strip()}</b>")
                return

# Helper function to launch user bot (Deducts points, creates DB record, runs pip install and starts process)
async def launch_user_deployment(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, runtime: str, main_file: str):
    bot_cost = int(await get_setting("bot_cost", str(DEFAULT_BOT_COST)))
    user_dir = BotRunner.get_user_dir(user_id)

    progress_msg = await update.effective_message.reply_html("⏳ <b>Building environment & starting bot (Render Style)...</b>")

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET points = points - ? WHERE telegram_id = ?", (bot_cost, user_id))
        await db.execute(
            "INSERT OR REPLACE INTO bots (user_id, runtime, status, project_dir, main_file) VALUES (?, ?, 'stopped', ?, ?)",
            (user_id, runtime, user_dir, main_file)
        )
        await db.commit()
        async with db.execute("SELECT id FROM bots WHERE user_id = ?", (user_id,)) as cur:
            bot_id = (await cur.fetchone())[0]

    USER_STATES.pop(user_id, None)
    ok, msg = await BotRunner.start_bot(bot_id, user_id, runtime, main_file)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 My Bot Dashboard", callback_data="menu_mybot"),
         InlineKeyboardButton("📋 View Live Logs", callback_data=f"botact_logs_{bot_id}")]
    ])

    await progress_msg.edit_text(
        f"<b>🚀 Bot Deployed Successfully!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🟢 <b>Status:</b> {'Running' if ok else 'Stopped (Check logs)'}\n"
        f"⚙️ <b>Runtime:</b> {runtime.upper()}\n"
        f"💎 <b>Points Deducted:</b> {bot_cost} Points\n\n"
        "আপনার বটটি ক্লাউডে ব্যাকগ্রাউন্ডে সফলভাবে চালু হয়েছে!",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )

# ------------------------------------------------------------------------------
# 13. STATS & SUPPORT
# ------------------------------------------------------------------------------
async def statistics_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            total_users = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM bots WHERE status = 'running'") as cur:
            running_bots = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM bots") as cur:
            total_bots = (await cur.fetchone())[0]
        async with db.execute("SELECT COALESCE(SUM(amount), 0) FROM deposits WHERE status = 'approved'") as cur:
            total_deposits = (await cur.fetchone())[0]

    text = (
        "<b>📊 Platform Statistics</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Total Users:</b> <code>{total_users}</code>\n"
        f"🤖 <b>Running Bots:</b> <code>{running_bots}</code>\n"
        f"📦 <b>Total Deployed:</b> <code>{total_bots}</code>\n"
        f"💰 <b>Total Deposits:</b> ৳<code>{total_deposits:.2f}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚡ <i>Cloud Engine Powered by CodeHost Pro.</i>"
    )
    await update.effective_message.reply_html(text)

async def support_prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    USER_STATES[update.effective_user.id] = {"state": "AWAITING_SUPPORT_MSG"}
    await update.effective_message.reply_html(
        "<b>🆘 24/7 Help & Support</b>\n\n"
        "আপনার সমস্যা বা প্রশ্ন লিখে মেসেজ পাঠান, এডমিন দ্রুত রিপ্লাই দেবে।",
        reply_markup=get_back_inline("cancel_action")
    )

# ------------------------------------------------------------------------------
# 14. 24/7 HEALTHCHECK SERVER (RENDER PORT BIND)
# ------------------------------------------------------------------------------
async def health_check_handler(request):
    return web.Response(text="CodeHost Platform is Online 24/7!", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check_handler)
    app.router.add_get("/health", health_check_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Healthcheck Server listening on port {PORT}")

# ------------------------------------------------------------------------------
# 15. MAIN ENTRY POINT
# ------------------------------------------------------------------------------
async def run_platform():
    await init_db()

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("admin", admin_panel_handler))

    application.add_handler(CallbackQueryHandler(deposit_callback, pattern=r"^dep_"))
    application.add_handler(CallbackQueryHandler(callback_dispatcher))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))

    await start_web_server()

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    logger.info(f"🚀 CodeHost Bot successfully started for @{BOT_USERNAME}.")

    stop_signal = asyncio.Event()
    await stop_signal.wait()

def main():
    try:
        asyncio.run(run_platform())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Platform shutting down gracefully.")

if __name__ == "__main__":
    main()
