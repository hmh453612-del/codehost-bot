#!/usr/bin/env python3
"""
================================================================================
🚀 Telegram Free Code Hosting & Bot Runner Platform
================================================================================
Developer: Expert Telegram Platform Architect
Features:
 - Multi-runtime Support (Python & Node.js Bot Runner)
 - Isolated Process Sandbox & Log Streaming
 - One Active Bot Per User Enforcement
 - Fraud-protected Referral System (+30 Points)
 - Manual Deposit System (bKash / Nagad / Rocket) with Admin Verification
 - Real-time Logs, Process Control (Start/Stop/Restart/Delete)
 - Admin Control Panel (User Manager, Balance Editor, Broadcast, Support Tickets)
 - Built-in Aiohttp Healthcheck Server for 24/7 Render Hosting
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

# Logging Configuration
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("CodeHostBot")

# In-Memory State Tracker for Multi-step Interactions
USER_STATES = {}
# Active subprocess dictionary: { bot_db_id: psutil.Popen }
RUNNING_PROCESSES = {}

# ------------------------------------------------------------------------------
# 2. DATABASE SCHEMA & INITIALIZATION
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

        # System Settings
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)

        # Default Settings Seeding
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

# Helper Settings Get/Set
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

def get_back_inline(callback_data: str = "menu_main"):
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

        # Stop already running process if any
        await BotRunner.stop_bot(bot_id)

        # Build command based on runtime
        if runtime.lower() == "python":
            req_path = os.path.join(user_dir, "requirements.txt")
            if os.path.exists(req_path):
                # Install requirements locally for this user sandbox
                try:
                    proc_install = await asyncio.create_subprocess_exec(
                        sys.executable, "-m", "pip", "install", "-r", req_path,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await proc_install.communicate()
                except Exception as e:
                    logger.error(f"Error installing requirements for user {user_id}: {e}")

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
                    logger.error(f"Error npm install for user {user_id}: {e}")

            cmd = ["node", main_file]
        else:
            return False, "Unsupported runtime selected."

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

        # Check in-memory process map or PID
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
                logger.error(f"Error removing user bot folder {user_dir}: {e}")

        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("DELETE FROM bots WHERE id = ?", (bot_id,))
            await db.commit()

# ------------------------------------------------------------------------------
# 5. USER REGISTRATION & /start HANDLER
# ------------------------------------------------------------------------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    # Check Maintenance
    m_mode = await get_setting("maintenance_mode", "0")
    if m_mode == "1" and user.id != ADMIN_ID:
        await update.message.reply_html(
            "<b>🔧 Maintenance Mode Active</b>\n\n"
            "Server is currently undergoing scheduled maintenance. "
            "Please check back in a few minutes."
        )
        return

    ref_by = None
    args = context.args
    if args and len(args) > 0 and args[0].startswith("ref_"):
        try:
            ref_id_candidate = int(args[0].split("_")[1])
            if ref_id_candidate != user.id:
                ref_by = ref_id_candidate
        except ValueError:
            pass

    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Check if user exists
        async with db.execute("SELECT id, is_banned FROM users WHERE telegram_id = ?", (user.id,)) as cur:
            user_row = await cur.fetchone()

        if user_row:
            if user_row[1] == 1:
                await update.message.reply_html("<b>🚫 Your account has been suspended by Admin.</b>")
                return
        else:
            # Create user
            ref_reward = int(await get_setting("referral_points", str(DEFAULT_REFERRAL_POINTS)))
            await db.execute(
                """
                INSERT INTO users (telegram_id, username, first_name, points, referral_code, referred_by)
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (user.id, user.username or "", user.first_name or "User", str(user.id), ref_by),
            )
            await db.commit()

            # Credit Referrer if valid
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
                            text=f"<b>🎁 New Referral!</b>\n\nUser <b>{user.first_name}</b> joined using your link!\n"
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
        "💻 Run your own Telegram Bots effortlessly\n"
        "🟢 <b>Node.js Support</b>\n"
        "🐍 <b>Python 3 Support</b>\n"
        "⚡ Fast & Isolated Deployment\n"
        "📊 Real-time Console & Status\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎁 Referral Reward: <b>{ref_pts} Points</b>\n"
        f"🤖 Bot Hosting Slot: <b>{bot_pts} Points</b>\n\n"
        "💡 <i>Invite friends, accumulate points, and host your bots 24/7.</i>\n\n"
        "👇 <b>Select an option from the menu below:</b>"
    )
    await update.message.reply_html(welcome_msg, reply_markup=get_main_keyboard(user.id))

# ------------------------------------------------------------------------------
# 6. ACCOUNT, REFERRAL & POINTS VIEWS
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
        await update.message.reply_text("Please send /start first.")
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
    await update.message.reply_html(text)

async def referral_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ref_pts = await get_setting("referral_points", str(DEFAULT_REFERRAL_POINTS))
    bot_info = await context.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"

    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT referral_count, points FROM users WHERE telegram_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()

    ref_count = row[0] if row else 0
    earned = ref_count * int(ref_pts)

    text = (
        "<b>🎁 Referral Program</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Total Referrals:</b> <code>{ref_count}</code>\n"
        f"💎 <b>Estimated Earned:</b> <code>{earned}</code> Points\n\n"
        f"🎯 <b>Per Valid Referral:</b> <b>+{ref_pts} Points</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🔗 <b>Your Personal Referral Link:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        "💡 <i>Share this link with your friends to earn points instantly!</i>"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Share Referral Link", url=f"https://t.me/share/url?url={ref_link}&text=Join%20CodeHost%20Bot%20to%20host%20your%20Telegram%20bots%20for%20free!")]
    ])
    await update.message.reply_html(text, reply_markup=kb)

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
        "📌 <i>Ways to get Points:</i>\n"
        "1. 🎁 Invite friends via <b>Referral Link</b>\n"
        "2. 💰 <b>Deposit</b> via bKash / Nagad / Rocket\n"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Deposit Points", callback_data="btn_deposit"),
         InlineKeyboardButton("🎁 Refer Friends", callback_data="btn_referral")]
    ])
    await update.message.reply_html(text, reply_markup=kb)

# ------------------------------------------------------------------------------
# 7. DEPOSIT SYSTEM (BKASH, NAGAD, ROCKET & ADMIN APPROVAL)
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
        "⚠️ <i>Send Money করার পর আপনার Transaction ID সাবমিট করুন।</i>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "👇 <b>Select your payment method below:</b>"
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
        await update.message.reply_html(text, reply_markup=kb)

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
            f"<b>Payment Method:</b> <b>{method}</b>\n\n"
            f"Please select or enter the deposit amount:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )

    elif data.startswith("dep_amt_"):
        amt_str = data.split("_")[2]
        if amt_str == "custom":
            USER_STATES[query.from_user.id]["state"] = "AWAITING_DEP_CUSTOM_AMOUNT"
            await query.edit_message_text(
                "✍️ <b>Enter Custom Amount (in BDT):</b>\n\n"
                "Example: <code>150</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_inline("menu_deposit")
            )
        else:
            amount = float(amt_str)
            USER_STATES[query.from_user.id]["amount"] = amount
            USER_STATES[query.from_user.id]["state"] = "AWAITING_DEP_TRX"
            await query.edit_message_text(
                f"💵 <b>Selected Amount:</b> ৳{amount}\n"
                f"💳 <b>Method:</b> {USER_STATES[query.from_user.id]['method']}\n\n"
                "🧾 <b>Please send your Transaction ID (TrxID):</b>\n"
                "Example: <code>8A7B6C5D</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_inline("menu_deposit")
            )

# ------------------------------------------------------------------------------
# 8. BOT DEPLOYMENT & PROJECT MANAGEMENT
# ------------------------------------------------------------------------------
async def deploy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Maintenance Check
    m_mode = await get_setting("maintenance_mode", "0")
    if m_mode == "1" and user_id != ADMIN_ID:
        await update.message.reply_html("<b>🔧 Bot deployment is temporarily paused for maintenance.</b>")
        return

    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT points FROM users WHERE telegram_id = ?", (user_id,)) as cur:
            u_row = await cur.fetchone()
        async with db.execute("SELECT id, status FROM bots WHERE user_id = ?", (user_id,)) as cur:
            b_row = await cur.fetchone()

    points = u_row[0] if u_row else 0
    bot_cost = int(await get_setting("bot_cost", str(DEFAULT_BOT_COST)))

    # Check 1 active bot limit
    if b_row:
        await update.message.reply_html(
            "<b>❌ Active Bot Slot Full</b>\n\n"
            "আপনার একটি Bot ইতিমধ্যে স্লটে যুক্ত আছে।\n"
            "নতুন Bot deploy করতে হলে প্রথমে <b>'🤖 My Bot'</b> মেনু থেকে বর্তমান Bot মুছে ফেলুন।"
        )
        return

    # Check Points
    if points < bot_cost:
        await update.message.reply_html(
            "<b>❌ Insufficient Points</b>\n\n"
            f"Bot Deploy করতে <b>{bot_cost} Points</b> প্রয়োজন।\n"
            f"আপনার বর্তমান Balance: <b>{points} Points</b>\n\n"
            "🎁 Referral করে অথবা 💰 Deposit করে ব্যালেন্স যোগ করুন।"
        )
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🐍 Python 3 Bot (bot.py)", callback_data="runtime_python")],
        [InlineKeyboardButton("🟢 Node.js Bot (index.js)", callback_data="runtime_node")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")]
    ])
    await update.message.reply_html(
        "<b>🤖 Select Bot Runtime</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "আপনার Bot-এর Runtime নির্বাচন করুন:\n\n"
        "• <b>Python 3:</b> <code>bot.py</code> এবং optional <code>requirements.txt</code>\n"
        "• <b>Node.js:</b> <code>index.js</code> এবং optional <code>package.json</code>\n\n"
        f"💎 <b>Cost:</b> {bot_cost} Points (One-time slot setup)",
        reply_markup=kb
    )

async def my_bot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT id, runtime, status, main_file, created_at FROM bots WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()

    if not row:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Deploy Bot Now", callback_data="btn_deploy")]])
        await update.message.reply_html(
            "<b>🤖 No Active Bot Found</b>\n\n"
            "আপনি এখনো কোনো Bot ডিপ্লয় করেননি।\n"
            "নিচের বাটনে ক্লিক করে সহজেই ডিপ্লয় করুন!",
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
        "<b>🤖 My Bot Management</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Status:</b> {status_emoji}\n"
        f"<b>Runtime:</b> <code>{runtime.upper()}</code>\n"
        f"<b>Entry File:</b> <code>{main_file}</code>\n"
        f"<b>Created:</b> {created}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Control your live bot process using the buttons below:"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.message.reply_html(text, reply_markup=kb)

# ------------------------------------------------------------------------------
# 9. GENERAL CALLBACK DISPATCHER (BOT ACTIONS & ADMIN)
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
            f"এখন আপনার বট কোড ফাইল (<code>{main_file}</code>) অথবা সরাসরি টেক্সট মেসেজ আকারে সম্পূর্ণ কোডটি পাঠান।\n\n"
            "💡 <i>আপনি একসাথে requirements.txt বা package.json ফাইলও পাঠাতে পারেন।</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_inline("cancel_action")
        )
        return

    # Bot Action Handlers
    if data.startswith("botact_"):
        parts = data.split("_")
        action = parts[1]
        bot_id = int(parts[2])

        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT id, user_id, runtime, main_file FROM bots WHERE id = ?", (bot_id,)) as cur:
                bot_info = await cur.fetchone()

        if not bot_info or (bot_info[1] != user_id and user_id != ADMIN_ID):
            await query.answer("Bot not found or unauthorized.", show_alert=True)
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
            await query.answer("Restarted successfully!" if ok else f"Failed: {msg}", show_alert=not ok)
            await my_bot_handler(update, context)

        elif action == "logs":
            await query.answer()
            logs = BotRunner.get_logs(u_id, max_lines=35)
            # Truncate to avoid Telegram limits
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
                [InlineKeyboardButton("🗑 Yes, Permanently Delete", callback_data=f"botact_dodelete_{bot_id}")],
                [InlineKeyboardButton("❌ Cancel", callback_data="menu_mybot")]
            ])
            await query.edit_message_text(
                "<b>⚠️ Delete Bot Confirmation</b>\n\n"
                "Are you sure you want to completely delete your bot and its files?\n"
                "<i>This action cannot be undone.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb
            )

        elif action == "dodelete":
            await BotRunner.delete_bot(b_id, u_id)
            await query.answer("Bot deleted successfully.", show_alert=True)
            await query.edit_message_text("<b>🗑 Bot and project files permanently removed.</b>", parse_mode=ParseMode.HTML)

    # Admin Deposit Approval / Rejection
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
            await query.answer("Deposit request not found.", show_alert=True)
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
                         f"💎 <b>Added Points:</b> +{pts} Points\n"
                         f"🧾 <b>TrxID:</b> <code>{trx}</code>\n\n"
                         f"You can now use your points to deploy your bot.",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

        elif decision == "rej":
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("UPDATE deposits SET status = 'rejected', admin_id = ?, rejection_reason = 'Payment could not be verified' WHERE id = ?", (user_id, dep_id))
                await db.commit()

            await query.edit_message_text(f"<b>❌ Deposit #{dep_id} Rejected.</b>", parse_mode=ParseMode.HTML)
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=f"<b>❌ Deposit Rejected</b>\n\n"
                         f"Your deposit request for <b>৳{amount}</b> (Trx: <code>{trx}</code>) was rejected by Admin.\n"
                         f"Please contact 🆘 Support if you believe this is a mistake.",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

    elif data == "menu_mybot":
        await my_bot_handler(update, context)

# ------------------------------------------------------------------------------
# 10. TEXT & DOCUMENT MESSAGE HANDLER (CODE UPLOAD & STATES)
# ------------------------------------------------------------------------------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    text = update.message.text or ""
    state_data = USER_STATES.get(user.id)

    # 1. Main Keyboard Reply Buttons Dispatch
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

    # 2. State Management Dispatch
    if state_data:
        state = state_data.get("state")

        # Custom Deposit Amount Input
        if state == "AWAITING_DEP_CUSTOM_AMOUNT":
            try:
                amt = float(text.strip())
                if amt <= 0:
                    raise ValueError
                state_data["amount"] = amt
                state_data["state"] = "AWAITING_DEP_TRX"
                await update.message.reply_html(
                    f"💵 <b>Amount Set:</b> ৳{amt}\n"
                    f"💳 <b>Method:</b> {state_data['method']}\n\n"
                    "🧾 <b>Please enter your Transaction ID (TrxID):</b>"
                )
            except ValueError:
                await update.message.reply_text("❌ Please enter a valid number (e.g. 150).")
            return

        # Deposit Transaction ID Submission
        if state == "AWAITING_DEP_TRX":
            trx = text.strip().upper()
            if len(trx) < 4:
                await update.message.reply_text("❌ Please enter a valid Transaction ID.")
                return

            method = state_data["method"]
            amount = state_data["amount"]
            points = int(amount)  # 1 BDT = 1 Point

            async with aiosqlite.connect(DATABASE_PATH) as db:
                # Check duplicate TxID
                async with db.execute("SELECT id FROM deposits WHERE transaction_id = ?", (trx,)) as cur:
                    dup = await cur.fetchone()
                if dup:
                    await update.message.reply_html("<b>⚠️ This Transaction ID has already been submitted.</b>")
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
            await update.message.reply_html(
                "<b>⏳ Deposit Request Submitted!</b>\n\n"
                f"🧾 <b>Transaction ID:</b> <code>{trx}</code>\n"
                f"💵 <b>Amount:</b> ৳{amount}\n"
                f"💎 <b>Points to receive:</b> {points}\n\n"
                "<i>Your request has been forwarded to the Admin for manual verification.</i>"
            )

            # Notify Admin
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Approve", callback_data=f"adm_dep_app_{dep_id}"),
                 InlineKeyboardButton("❌ Reject", callback_data=f"adm_dep_rej_{dep_id}")]
            ])
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"<b>💰 New Deposit Request (#{dep_id})</b>\n"
                         f"━━━━━━━━━━━━━━━━━━\n"
                         f"👤 <b>User:</b> @{user.username or 'None'} (<code>{user.id}</code>)\n"
                         f"💳 <b>Method:</b> {method}\n"
                         f"💵 <b>Amount:</b> ৳{amount} ({points} Points)\n"
                         f"🧾 <b>TrxID:</b> <code>{trx}</code>\n"
                         f"━━━━━━━━━━━━━━━━━━",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb
                )
            except Exception as e:
                logger.error(f"Failed to notify admin of deposit: {e}")
            return

        # Support Message Input
        if state == "AWAITING_SUPPORT_MSG":
            USER_STATES.pop(user.id, None)
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("INSERT INTO support_tickets (user_id, message) VALUES (?, ?)", (user.id, text))
                await db.commit()
                async with db.execute("SELECT last_insert_rowid()") as cur:
                    ticket_id = (await cur.fetchone())[0]

            await update.message.reply_html(
                f"<b>🎫 Support Ticket #{ticket_id} Created</b>\n\n"
                "Your query has been sent to our Admin team. You will receive a response shortly."
            )
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"<b>🆘 New Support Ticket #{ticket_id}</b>\n\n"
                         f"👤 <b>From:</b> @{user.username or 'None'} (<code>{user.id}</code>)\n"
                         f"📝 <b>Message:</b>\n{text}\n\n"
                         f"<i>To reply send:</i> <code>/reply {ticket_id} Your message</code>",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            return

        # Code Uploading (via Text)
        if state == "AWAITING_CODE_FILE":
            runtime = state_data["runtime"]
            main_file = "bot.py" if runtime == "python" else "index.js"
            user_dir = BotRunner.get_user_dir(user.id)
            code_path = os.path.join(user_dir, main_file)

            with open(code_path, "w", encoding="utf-8") as f:
                f.write(text)

            # Deduct points & create DB record
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
            # Automatically start bot
            ok, msg = await BotRunner.start_bot(bot_id, user.id, runtime, main_file)
            await update.message.reply_html(
                f"<b>🚀 Bot Deployed Successfully!</b>\n\n"
                f"💎 <b>Cost:</b> -{bot_cost} Points\n"
                f"🟢 <b>Status:</b> {'Running' if ok else 'Stopped (Check logs)'}\n\n"
                "Use <b>'🤖 My Bot'</b> to control your bot anytime."
            )
            return

    # Document Upload Handling (e.g. user sends bot.py, requirements.txt, index.js, package.json)
    if update.message.document:
        doc = update.message.document
        filename = doc.file_name
        user_dir = BotRunner.get_user_dir(user.id)
        file_obj = await context.bot.get_file(doc.file_id)
        dest_path = os.path.join(user_dir, filename)
        await file_obj.download_to_drive(dest_path)

        if state_data and state_data.get("state") == "AWAITING_CODE_FILE":
            runtime = state_data["runtime"]
            main_file = "bot.py" if runtime == "python" else "index.js"
            
            # If they uploaded main file
            if filename in ["bot.py", "index.js", "main.py", "app.js"]:
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
                await update.message.reply_html(
                    f"<b>📁 File <code>{filename}</code> received and deployed!</b>\n\n"
                    f"💎 <b>Deducted:</b> {bot_cost} Points\n"
                    f"🟢 <b>Status:</b> {'Running' if ok else 'Stopped'}\n\n"
                    "Manage your bot via <b>'🤖 My Bot'</b>."
                )
                return
        
        await update.message.reply_html(f"✅ File <code>{filename}</code> saved to your project directory.")

# ------------------------------------------------------------------------------
# 11. STATISTICS, SUPPORT & ADMIN CONTROL PANEL
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
        f"📦 <b>Total Deployed Bots:</b> <code>{total_bots}</code>\n"
        f"💰 <b>Total Verified Deposits:</b> ৳<code>{total_deposits:.2f}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚡ <i>High performance cloud hosting powered by CodeHost.</i>"
    )
    await update.message.reply_html(text)

async def support_prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    USER_STATES[update.effective_user.id] = {"state": "AWAITING_SUPPORT_MSG"}
    await update.message.reply_html(
        "<b>🆘 CodeHost 24/7 Support</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "অনুগ্রহ করে আপনার সমস্যা বা প্রশ্ন বিস্তারিত লিখে মেসেজ পাঠান।\n"
        "আমাদের সাপোর্ট টিম দ্রুত আপনার সাথে যোগাযোগ করবে।",
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
        async with db.execute("SELECT COUNT(*) FROM support_tickets WHERE status = 'open'") as cur:
            o_sup = (await cur.fetchone())[0]

    text = (
        "<b>⚙️ ADMIN CONTROL PANEL</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Total Users:</b> {u_count}\n"
        f"🟡 <b>Pending Deposits:</b> {p_dep}\n"
        f"🎫 <b>Open Support Tickets:</b> {o_sup}\n\n"
        "<b>Admin Commands:</b>\n"
        "• <code>/addpoints [user_id] [points]</code> - Add points\n"
        "• <code>/rempoints [user_id] [points]</code> - Deduct points\n"
        "• <code>/ban [user_id]</code> - Ban user\n"
        "• <code>/unban [user_id]</code> - Unban user\n"
        "• <code>/broadcast [message]</code> - Broadcast to all\n"
        "• <code>/reply [ticket_id] [message]</code> - Reply to support\n"
        "• <code>/maintenance [1 or 0]</code> - Toggle Maintenance\n"
    )
    await update.message.reply_html(text)

# ------------------------------------------------------------------------------
# 12. ADMIN COMMANDS IMPLEMENTATION
# ------------------------------------------------------------------------------
async def admin_add_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        target_id = int(context.args[0])
        pts = int(context.args[1])
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("UPDATE users SET points = points + ? WHERE telegram_id = ?", (pts, target_id))
            await db.commit()
        await update.message.reply_text(f"✅ Added {pts} points to user {target_id}.")
        try:
            await context.bot.send_message(target_id, f"💎 Admin added <b>+{pts} Points</b> to your balance!", parse_mode=ParseMode.HTML)
        except Exception:
            pass
    except Exception as e:
        await update.message.reply_text(f"Usage: /addpoints [user_id] [points]\nError: {e}")

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text("Usage: /broadcast [Your message]")
        return

    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT telegram_id FROM users WHERE is_banned = 0") as cur:
            users = await cur.fetchall()

    success, fail = 0, 0
    await update.message.reply_text(f"📢 Starting broadcast to {len(users)} users...")
    for u in users:
        try:
            await context.bot.send_message(
                chat_id=u[0],
                text=f"<b>📢 Announcement from Admin:</b>\n\n{msg}",
                parse_mode=ParseMode.HTML
            )
            success += 1
            await asyncio.sleep(0.05)  # Telegram rate-limit mitigation
        except Exception:
            fail += 1

    await update.message.reply_text(f"✅ Broadcast Completed!\nSuccess: {success}\nFailed: {fail}")

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
                await update.message.reply_text("Ticket not found.")
                return
            await db.execute("UPDATE support_tickets SET reply = ?, status = 'closed' WHERE id = ?", (reply_msg, ticket_id))
            await db.commit()

        target_uid = row[0]
        await context.bot.send_message(
            chat_id=target_uid,
            text=f"<b>🆘 Support Reply (Ticket #{ticket_id})</b>\n\n"
                 f"📝 <b>Your Issue:</b> {row[1]}\n\n"
                 f"👨‍💼 <b>Admin Response:</b>\n{reply_msg}",
            parse_mode=ParseMode.HTML
        )
        await update.message.reply_text(f"✅ Reply sent for Ticket #{ticket_id}.")
    except Exception as e:
        await update.message.reply_text(f"Usage: /reply [ticket_id] [message]\nError: {e}")

async def admin_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        val = context.args[0]
        if val in ["0", "1"]:
            await set_setting("maintenance_mode", val)
            await update.message.reply_text(f"✅ Maintenance Mode set to: {'ON' if val == '1' else 'OFF'}")
        else:
            await update.message.reply_text("Usage: /maintenance 1 (ON) or /maintenance 0 (OFF)")
    except Exception:
        await update.message.reply_text("Usage: /maintenance 1 or /maintenance 0")

# ------------------------------------------------------------------------------
# 13. RENDER HEALTH CHECK HTTP SERVER
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
    logger.info(f"Healthcheck Web Server running on port {PORT}")

# ------------------------------------------------------------------------------
# 14. MAIN ENTRY POINT & BOT INITIALIZATION
# ------------------------------------------------------------------------------
def main():
    # Run DB init synchronously before launching loop
    asyncio.run(init_db())

    # Build Telegram Bot Application
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("addpoints", admin_add_points))
    application.add_handler(CommandHandler("broadcast", admin_broadcast))
    application.add_handler(CommandHandler("reply", admin_reply_ticket))
    application.add_handler(CommandHandler("maintenance", admin_maintenance))

    application.add_handler(CallbackQueryHandler(deposit_callback, pattern=r"^dep_"))
    application.add_handler(CallbackQueryHandler(callback_dispatcher))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))

    # Background tasks: Start Healthcheck HTTP Server
    async def post_init(app):
        asyncio.create_task(start_web_server())

    application.post_init = post_init

    logger.info(f"🚀 CodeHost Bot started for @{BOT_USERNAME}. Polling...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
