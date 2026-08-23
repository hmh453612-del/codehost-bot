#!/usr/bin/env python3
"""
================================================================================
🚀 Telegram Free Code Hosting & Bot Runner Platform (Render & VPS Ready)
================================================================================
"""

import os
import sys
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
BOT_TOKEN = os.getenv("BOT_TOKEN", "8991466338:AAFw1ZDElt-rZcC9VNI8BZcGN5cmIsytIyU")
BOT_USERNAME = os.getenv("BOT_USERNAME", "tiktok_downloadmh_bot")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8855522653"))

BKASH_NUMBER = os.getenv("BKASH_NUMBER", "01781412911")
NAGAD_NUMBER = os.getenv("NAGAD_NUMBER", "01778782993")
ROCKET_NUMBER = os.getenv("ROCKET_NUMBER", "01778782993")

DEFAULT_REFERRAL_POINTS = int(os.getenv("REFERRAL_POINTS", 30))
DEFAULT_BOT_COST = int(os.getenv("BOT_REQUIRED_POINTS", 100))
DATABASE_PATH = os.getenv("DATABASE_PATH", "hosting_bot.db")
BOTS_BASE_DIR = os.getenv("BOTS_BASE_DIR", "/tmp/hosted_bots")
PORT = int(os.getenv("PORT", 8080))

logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("CodeHostBot")

USER_STATES = {}
RUNNING_PROCESSES = {}

# ------------------------------------------------------------------------------
# 2. DATABASE SCHEMA & INITIALIZATION
# ------------------------------------------------------------------------------
async def init_db():
    os.makedirs(BOTS_BASE_DIR, exist_ok=True)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        
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

        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)

        default_settings = [
            ("referral_points", str(DEFAULT_REFERRAL_POINTS)),
            ("bot_cost", str(DEFAULT_BOT_COST)),
            ("bkash_number", BKASH_NUMBER),
            ("nagad_number", NAGAD_NUMBER),
            ("rocket_number", ROCKET_NUMBER),
            ("maintenance_mode", "0"),
        ]
        for key, val in default_settings:
            await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?);", (key, val))

        await db.commit()
    logger.info("Database schema initialized successfully.")

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
# 3. KEYBOARDS & UI BUILDERS
# ------------------------------------------------------------------------------
def get_main_keyboard(user_id: int):
    buttons = [
        [KeyboardButton("🚀 Deploy Bot"), KeyboardButton("🤖 My Bot")],
        [KeyboardButton("👤 My Account"), KeyboardButton("🎁 Referral")],
        [KeyboardButton("💰 Deposit"), KeyboardButton("💎 My Points")],
        [KeyboardButton("📊 Statistics"), KeyboardButton("🆘 Support")],
    ]
    if user_id == ADMIN_ID:
        buttons.append([KeyboardButton("⚙️ Admin Panel")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_back_inline(callback_data: str = "cancel_action"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=callback_data)]])

# ------------------------------------------------------------------------------
# 4. PROCESS RUNNER & SANDBOX ENGINE
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
            return False, f"Main file `{main_file}` missing in project folder."

        await BotRunner.stop_bot(bot_id)

        if runtime.lower() == "python":
            req_path = os.path.join(user_dir, "requirements.txt")
            if os.path.exists(req_path):
                try:
                    proc_install = await asyncio.create_subprocess_exec(
                        sys.executable, "-m", "pip", "install", "-r", req_path,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await proc_install.communicate()
                except Exception as e:
                    logger.error(f"Error installing requirements: {e}")

            cmd = [sys.executable, "-u", main_file]
        elif runtime.lower() == "node":
            pkg_path = os.path.join(user_dir, "package.json")
            if os.path.exists(pkg_path):
                try:
                    proc_install = await asyncio.create_subprocess_exec(
                        "npm", "install",
                        cwd=user_dir,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await proc_install.communicate()
                except Exception as e:
                    logger.error(f"Error npm install: {e}")

            cmd = ["node", main_file]
        else:
            return False, "Unsupported runtime."

        try:
            log_fd = open(log_file_path, "a", encoding="utf-8")
            log_fd.write(f"\n\n--- [ BOT LAUNCHED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ] ---\n")
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
            logger.error(f"Failed to start bot ID {bot_id}: {e}")
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
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
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
                    f.write(f"\n--- [ BOT STOPPED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ] ---\n")
        return True

    @staticmethod
    def get_logs(user_id: int, max_lines: int = 40) -> str:
        user_dir = BotRunner.get_user_dir(user_id)
        log_file = os.path.join(user_dir, "runtime.log")
        if not os.path.exists(log_file):
            return "No logs generated yet."
        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                selected = lines[-max_lines:]
                return "".join(selected) if selected else "Log file is currently empty."
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
# 5. USER /start & REGISTRATION
# ------------------------------------------------------------------------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    m_mode = await get_setting("maintenance_mode", "0")
    if m_mode == "1" and user.id != ADMIN_ID:
        await update.effective_message.reply_html("<b>🔧 Bot is under maintenance. Please try again later.</b>")
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
        "<b>🚀 Welcome to CodeHost Bot</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "💻 Run your own Telegram Bots 24/7\n"
        "🟢 <b>Node.js Support</b>\n"
        "🐍 <b>Python 3 Support</b>\n"
        "⚡ Fast & Isolated Execution\n"
        "📊 Live Logs & Console Status\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎁 Referral Reward: <b>{ref_pts} Points</b>\n"
        f"🤖 Bot Slot Cost: <b>{bot_pts} Points</b>\n\n"
        "👇 <b>Select an option from the menu:</b>"
    )
    await update.effective_message.reply_html(welcome_msg, reply_markup=get_main_keyboard(user.id))

# ------------------------------------------------------------------------------
# 6. ACCOUNT & REFERRAL HANDLERS
# ------------------------------------------------------------------------------
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
        f"🎯 <b>Per Valid Referral:</b> <b>+{ref_pts} Points</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🔗 <b>Your Referral Link:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        "💡 <i>Share this link to earn free points!</i>"
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
        "<b>💎 Points Overview</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 <b>Current Balance:</b> <b>{pts} Points</b>\n"
        f"🤖 <b>Bot Deployment Cost:</b> <b>{bot_cost} Points</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📌 <i>How to get points:</i>\n"
        "1. 🎁 Invite friends (Referral Link)\n"
        "2. 💰 Deposit via bKash / Nagad / Rocket\n"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Deposit Points", callback_data="btn_deposit"),
         InlineKeyboardButton("🎁 Refer Friends", callback_data="btn_referral")]
    ])
    await update.effective_message.reply_html(text, reply_markup=kb)

# ------------------------------------------------------------------------------
# 7. DEPOSIT FLOW
# ------------------------------------------------------------------------------
async def deposit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bkash = await get_setting("bkash_number", BKASH_NUMBER)
    nagad = await get_setting("nagad_number", NAGAD_NUMBER)
    rocket = await get_setting("rocket_number", ROCKET_NUMBER)

    text = (
        "<b>💰 Add Points to Account</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Payment Numbers (Personal / Send Money):</b>\n"
        f"💚 <b>bKash:</b> <code>{bkash}</code>\n"
        f"🟠 <b>Nagad:</b> <code>{nagad}</code>\n"
        f"🔵 <b>Rocket:</b> <code>{rocket}</code>\n\n"
        "💵 <b>Rate:</b> ৳1 = 1 Point\n"
        "⚠️ <i>Send Money করার পর Transaction ID সাবমিট করুন।</i>\n\n"
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
                "✍️ <b>Enter Custom Amount (in BDT):</b>\n\nExample: <code>150</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_inline("btn_deposit")
            )
        else:
            amount = float(amt_str)
            USER_STATES[query.from_user.id]["amount"] = amount
            USER_STATES[query.from_user.id]["state"] = "AWAITING_DEP_TRX"
            await query.edit_message_text(
                f"💵 <b>Selected:</b> ৳{amount}\n"
                f"💳 <b>Method:</b> {USER_STATES[query.from_user.id]['method']}\n\n"
                "🧾 <b>Please send your Transaction ID (TrxID):</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_inline("btn_deposit")
            )

# ------------------------------------------------------------------------------
# 8. BOT DEPLOYMENT & MANAGEMENT
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
            "আপনার একটি Bot ইতিমধ্যে স্লটে আছে।\n"
            "নতুন Bot চালাতে হলে <b>'🤖 My Bot'</b> থেকে বর্তমান Bot ডিলিট করুন।"
        )
        return

    if points < bot_cost:
        await update.effective_message.reply_html(
            "<b>❌ Insufficient Points</b>\n\n"
            f"Bot Deploy করতে <b>{bot_cost} Points</b> প্রয়োজন।\n"
            f"আপনার Balance: <b>{points} Points</b>\n\n"
            "🎁 Referral করে অথবা 💰 Deposit করে ব্যালেন্স যুক্ত করুন।"
        )
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🐍 Python 3 Bot (bot.py)", callback_data="runtime_python")],
        [InlineKeyboardButton("🟢 Node.js Bot (index.js)", callback_data="runtime_node")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")]
    ])
    await update.effective_message.reply_html(
        "<b>🤖 Select Bot Runtime</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "• <b>Python 3:</b> <code>bot.py</code> (এবং requirements.txt)\n"
        "• <b>Node.js:</b> <code>index.js</code> (এবং package.json)\n\n"
        f"💎 <b>Cost:</b> {bot_cost} Points",
        reply_markup=kb
    )

async def my_bot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT id, runtime, status, main_file, created_at FROM bots WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()

    if not row:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Deploy Bot Now", callback_data="btn_deploy")]])
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                "<b>🤖 No Active Bot Found</b>\n\n"
                "আপনি এখনো কোনো Bot হোস্ট করেননি।",
                parse_mode=ParseMode.HTML,
                reply_markup=kb
            )
        else:
            await update.effective_message.reply_html(
                "<b>🤖 No Active Bot Found</b>\n\n"
                "আপনি এখনো কোনো Bot হোস্ট করেননি।",
                reply_markup=kb
            )
        return

    bot_id, runtime, status, main_file, created = row
    status_emoji = "🟢 Running" if status == "running" else "🔴 Stopped"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Start", callback_data=f"botact_start_{bot_id}"),
         InlineKeyboardButton("⏹ Stop", callback_data=f"botact_stop_{bot_id}")],
        [InlineKeyboardButton("🔄 Restart", callback_data=f"botact_restart_{bot_id}"),
         InlineKeyboardButton("📋 View Logs", callback_data=f"botact_logs_{bot_id}")],
        [InlineKeyboardButton("🗑 Delete Bot", callback_data=f"botact_confirmdel_{bot_id}")],
    ])

    text = (
        "<b>🤖 My Bot Dashboard</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Status:</b> {status_emoji}\n"
        f"<b>Runtime:</b> <code>{runtime.upper()}</code>\n"
        f"<b>Entry File:</b> <code>{main_file}</code>\n"
        f"<b>Created:</b> {created}\n\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.effective_message.reply_html(text, reply_markup=kb)

# ------------------------------------------------------------------------------
# 9. CALLBACK DISPATCHER
# ------------------------------------------------------------------------------
async def callback_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id

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

    if data.startswith("runtime_"):
        runtime = data.split("_")[1]
        USER_STATES[user_id] = {"state": "AWAITING_CODE_FILE", "runtime": runtime}
        main_file = "bot.py" if runtime == "python" else "index.js"
        await query.answer()
        await query.edit_message_text(
            f"<b>🟢 {runtime.upper()} Selected</b>\n\n"
            f"আপনার বট কোড ফাইল (<code>{main_file}</code>) অথবা সরাসরি টেক্সট মেসেজ আকারে কোডটি পাঠান।\n\n"
            "💡 <i>ডকুমেন্ট আকারে ফাইল আপলোড করলে স্বয়ংক্রিয়ভাবে প্রজেক্টে যুক্ত হবে।</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_inline("cancel_action")
        )
        return

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

    elif data.startswith("adm_dep_"):
        if user_id != ADMIN_ID:
            await query.answer("Unauthorized.", show_alert=True)
            return

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

            await query.edit_message_text(f"<b>✅ Deposit #{dep_id} Approved!</b>\nTrx: <code>{trx}</code>\nPoints: +{pts}", parse_mode=ParseMode.HTML)
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=f"<b>✅ Deposit Approved!</b>\n\n"
                         f"💵 <b>Amount:</b> ৳{amount}\n"
                         f"💎 <b>Points:</b> +{pts} Points\n"
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
                    text=f"<b>❌ Deposit Rejected</b>\n\nYour deposit of <b>৳{amount}</b> was rejected.",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

    elif data == "menu_mybot":
        await my_bot_handler(update, context)

# ------------------------------------------------------------------------------
# 10. MESSAGE HANDLER (FIXED FILE & TEXT DISPATCH)
# ------------------------------------------------------------------------------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    text = update.message.text or ""
    state_data = USER_STATES.get(user.id)

    # 1. Main Keyboard Reply Buttons
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
    elif text == "⚙️ Admin Panel" and user.id == ADMIN_ID:
        await admin_panel_handler(update, context)
        return

    # 2. Document Uploads (Highest priority if file attached)
    if update.message.document:
        doc = update.message.document
        filename = doc.file_name or "bot.py"
        user_dir = BotRunner.get_user_dir(user.id)
        file_obj = await context.bot.get_file(doc.file_id)
        dest_path = os.path.join(user_dir, filename)
        await file_obj.download_to_drive(dest_path)

        if state_data and state_data.get("state") == "AWAITING_CODE_FILE":
            runtime = state_data["runtime"]
            main_file = "bot.py" if runtime == "python" else "index.js"
            if filename in ["bot.py", "index.js", "main.py", "app.js", "server.js"]:
                main_file = filename

            bot_cost = int(await get_setting("bot_cost", str(DEFAULT_BOT_COST)))
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("UPDATE users SET points = points - ? WHERE telegram_id = ?", (bot_cost, user.id))
                await db.execute(
                    "INSERT OR REPLACE INTO bots (user_id, runtime, status, project_dir, main_file) VALUES (?, ?, 'stopped', ?, ?)",
                    (user.id, runtime, user_dir, main_file)
                )
                await db.commit()
                async with db.execute("SELECT id FROM bots WHERE user_id = ?", (user.id,)) as cur:
                    bot_id = (await cur.fetchone())[0]

            USER_STATES.pop(user.id, None)
            ok, msg = await BotRunner.start_bot(bot_id, user.id, runtime, main_file)
            await update.effective_message.reply_html(
                f"<b>📁 File <code>{filename}</code> deployed!</b>\n\n"
                f"💎 <b>Deducted:</b> {bot_cost} Points\n"
                f"🟢 <b>Status:</b> {'Running' if ok else 'Stopped (Check Logs)'}"
            )
            return

        await update.effective_message.reply_html(f"✅ File <code>{filename}</code> saved to your project folder.")
        return

    # 3. FSM State Processing (Text Inputs)
    if state_data:
        state = state_data.get("state")

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
                await update.effective_message.reply_text("❌ Please enter a valid positive number.")
            return

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
                "<b>⏳ Deposit Submitted!</b>\n\n"
                f"🧾 <b>Transaction ID:</b> <code>{trx}</code>\n"
                f"💵 <b>Amount:</b> ৳{amount} ({points} Points)\n\n"
                "<i>Admin will verify your payment shortly.</i>"
            )

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Approve", callback_data=f"adm_dep_app_{dep_id}"),
                 InlineKeyboardButton("❌ Reject", callback_data=f"adm_dep_rej_{dep_id}")]
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

        if state == "AWAITING_SUPPORT_MSG":
            USER_STATES.pop(user.id, None)
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("INSERT INTO support_tickets (user_id, message) VALUES (?, ?)", (user.id, text))
                await db.commit()
                async with db.execute("SELECT last_insert_rowid()") as cur:
                    ticket_id = (await cur.fetchone())[0]

            await update.effective_message.reply_html(f"<b>🎫 Support Ticket #{ticket_id} Created</b>\nAdmin has been notified.")
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"<b>🆘 Ticket #{ticket_id}</b> from @{user.username or 'None'} (<code>{user.id}</code>):\n\n{text}\n\n"
                         f"Reply: <code>/reply {ticket_id} [Your Reply]</code>",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            return

        if state == "AWAITING_CODE_FILE" and text:
            runtime = state_data["runtime"]
            main_file = "bot.py" if runtime == "python" else "index.js"
            user_dir = BotRunner.get_user_dir(user.id)
            code_path = os.path.join(user_dir, main_file)

            with open(code_path, "w", encoding="utf-8") as f:
                f.write(text)

            bot_cost = int(await get_setting("bot_cost", str(DEFAULT_BOT_COST)))
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("UPDATE users SET points = points - ? WHERE telegram_id = ?", (bot_cost, user.id))
                await db.execute(
                    "INSERT OR REPLACE INTO bots (user_id, runtime, status, project_dir, main_file) VALUES (?, ?, 'stopped', ?, ?)",
                    (user.id, runtime, user_dir, main_file)
                )
                await db.commit()
                async with db.execute("SELECT id FROM bots WHERE user_id = ?", (user.id,)) as cur:
                    bot_id = (await cur.fetchone())[0]

            USER_STATES.pop(user.id, None)
            ok, msg = await BotRunner.start_bot(bot_id, user.id, runtime, main_file)
            await update.effective_message.reply_html(
                f"<b>🚀 Bot Deployed!</b>\n\n"
                f"💎 <b>Deducted:</b> {bot_cost} Points\n"
                f"🟢 <b>Status:</b> {'Running' if ok else 'Stopped'}\n\n"
                "Manage using <b>'🤖 My Bot'</b>."
            )
            return

# ------------------------------------------------------------------------------
# 11. STATS, SUPPORT & ADMIN
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
        "━━━━━━━━━━━━━━━━━━"
    )
    await update.effective_message.reply_html(text)

async def support_prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    USER_STATES[update.effective_user.id] = {"state": "AWAITING_SUPPORT_MSG"}
    await update.effective_message.reply_html(
        "<b>🆘 CodeHost Support</b>\n\n"
        "আপনার সমস্যা বা মেসেজটি লিখে পাঠান, এডমিন রিপ্লাই দেবে।",
        reply_markup=get_back_inline("cancel_action")
    )

async def admin_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            u_count = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM deposits WHERE status = 'pending'") as cur:
            p_dep = (await cur.fetchone())[0]

    text = (
        "<b>⚙️ ADMIN CONTROL PANEL</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Users:</b> {u_count} | 🟡 <b>Pending Deposits:</b> {p_dep}\n\n"
        "<b>Commands:</b>\n"
        "• <code>/addpoints [user_id] [points]</code>\n"
        "• <code>/broadcast [message]</code>\n"
        "• <code>/reply [ticket_id] [message]</code>\n"
        "• <code>/maintenance [1/0]</code>"
    )
    await update.effective_message.reply_html(text)

async def admin_add_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        target_id = int(context.args[0])
        pts = int(context.args[1])
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("UPDATE users SET points = points + ? WHERE telegram_id = ?", (pts, target_id))
            await db.commit()
        await update.effective_message.reply_text(f"✅ Added {pts} points to user {target_id}.")
        try:
            await context.bot.send_message(target_id, f"💎 Admin added <b>+{pts} Points</b> to your balance!", parse_mode=ParseMode.HTML)
        except Exception:
            pass
    except Exception as e:
        await update.effective_message.reply_text(f"Error: {e}")

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    msg = " ".join(context.args)
    if not msg:
        await update.effective_message.reply_text("Usage: /broadcast [Message]")
        return

    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT telegram_id FROM users WHERE is_banned = 0") as cur:
            users = await cur.fetchall()

    success, fail = 0, 0
    await update.effective_message.reply_text(f"📢 Broadcasting to {len(users)} users...")
    for u in users:
        try:
            await context.bot.send_message(chat_id=u[0], text=f"<b>📢 Announcement:</b>\n\n{msg}", parse_mode=ParseMode.HTML)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail += 1

    await update.effective_message.reply_text(f"✅ Broadcast Completed!\nSuccess: {success}, Failed: {fail}")

async def admin_reply_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        ticket_id = int(context.args[0])
        reply_msg = " ".join(context.args[1:])
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT user_id, message FROM support_tickets WHERE id = ?", (ticket_id,)) as cur:
                row = await cur.fetchone()
            if not row:
                await update.effective_message.reply_text("Ticket not found.")
                return
            await db.execute("UPDATE support_tickets SET reply = ?, status = 'closed' WHERE id = ?", (reply_msg, ticket_id))
            await db.commit()

        await context.bot.send_message(
            chat_id=row[0],
            text=f"<b>🆘 Support Reply (Ticket #{ticket_id})</b>\n\n"
                 f"📝 <b>Issue:</b> {row[1]}\n\n"
                 f"👨‍💼 <b>Admin:</b> {reply_msg}",
            parse_mode=ParseMode.HTML
        )
        await update.effective_message.reply_text(f"✅ Reply sent for Ticket #{ticket_id}.")
    except Exception as e:
        await update.effective_message.reply_text(f"Error: {e}")

async def admin_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        val = context.args[0]
        if val in ["0", "1"]:
            await set_setting("maintenance_mode", val)
            await update.effective_message.reply_text(f"✅ Maintenance Mode: {'ON' if val == '1' else 'OFF'}")
    except Exception:
        await update.effective_message.reply_text("Usage: /maintenance 1 or 0")

# ------------------------------------------------------------------------------
# 12. RENDER HEALTH CHECK HTTP SERVER
# ------------------------------------------------------------------------------
async def health_check_handler(request):
    return web.Response(text="CodeHost Telegram Bot Platform is Running 24/7!", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check_handler)
    app.router.add_get("/health", health_check_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Healthcheck Web Server started on port {PORT}")

# ------------------------------------------------------------------------------
# 13. MAIN ASYNC ENTRY POINT
# ------------------------------------------------------------------------------
async def run_platform():
    await init_db()

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("addpoints", admin_add_points))
    application.add_handler(CommandHandler("broadcast", admin_broadcast))
    application.add_handler(CommandHandler("reply", admin_reply_ticket))
    application.add_handler(CommandHandler("maintenance", admin_maintenance))

    application.add_handler(CallbackQueryHandler(deposit_callback, pattern=r"^dep_"))
    application.add_handler(CallbackQueryHandler(callback_dispatcher))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))

    await start_web_server()

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    logger.info(f"🚀 CodeHost Bot successfully started for @{BOT_USERNAME}. Listening for updates...")

    stop_signal = asyncio.Event()
    await stop_signal.wait()

def main():
    try:
        asyncio.run(run_platform())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot shutting down gracefully.")

if __name__ == "__main__":
    main()
