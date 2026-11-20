import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from threading import Thread

from flask import Flask
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    InputMediaPhoto,
)
from pyrogram.errors import UserIsBlocked
from google import genai

# ==========================================
# CONFIGURATION (Set in Render Env Vars)
# ==========================================
API_ID = int(os.environ.get("API_ID", "12345678"))
API_HASH = os.environ.get("API_HASH", "your_api_hash_here")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token_here")
OWNER_ID = int(os.environ.get("OWNER_ID", "123456789"))
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true&w=majority")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "your_gemini_api_key_here")

# Add your start image URL here or via START_IMAGE_URL env var
START_IMAGE_URL = os.environ.get("START_IMAGE_URL", "")
BOT_NAME = os.environ.get("BOT_NAME", "Aero Contact Relay")
BOT_VERSION = os.environ.get("BOT_VERSION", "v3.2")

UPDATES_URL = os.environ.get("UPDATES_CHANNEL_URL", "https://t.me/Aero_Unity")
REPO_URL = os.environ.get("REPO_URL", "https://github.com/MohammedDev-yt")
DEPLOY_STATUS_URL = os.environ.get("DEPLOY_STATUS_URL", "https://render.com")
PYROGRAM_URL = os.environ.get("PYROGRAM_URL", "https://docs.pyrogram.org")
PYTHON_URL = os.environ.get("PYTHON_URL", "https://www.python.org")
PORT = int(os.environ.get("PORT", 8080))

IST = timezone(timedelta(hours=5, minutes=30))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ==========================================
# DATABASE & GEMINI SETUP
# ==========================================
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["ContactRelayBot"]
users_col = db["users"]
msg_map_col = db["message_map"]
blocked_col = db["blocked_users"]
settings_col = db["settings"]

gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY and "your_" not in GEMINI_API_KEY else None

# ==========================================
# FLASK KEEP-ALIVE SERVER (For Render 24/7)
# ==========================================
web_app = Flask(__name__)

@web_app.route("/")
def home():
    return {
        "status": "online",
        "service": "Contact Relay Node",
        "timestamp": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
    }

def run_flask():
    web_app.run(host="0.0.0.0", port=PORT)

# ==========================================
# PYROGRAM BOT CLIENT
# ==========================================
app = Client(
    "contact_relay_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=enums.ParseMode.HTML,
)

def get_ist_time() -> str:
    return datetime.now(IST).strftime("%I:%M %p • %d %b")

def format_user_greeting(user) -> str:
    if user.username:
        return f"@{user.username}"
    return user.first_name or "there"

def has_start_image() -> bool:
    return bool(START_IMAGE_URL and START_IMAGE_URL.strip().startswith(("http://", "https://")))

async def save_user(user):
    await users_col.update_one(
        {"user_id": user.id},
        {
            "$set": {
                "first_name": user.first_name,
                "username": user.username,
                "last_active": datetime.now(IST).isoformat(),
            },
            "$setOnInsert": {
                "joined_at": datetime.now(IST).isoformat(),
            },
        },
        upsert=True,
    )

async def is_ai_enabled() -> bool:
    setting = await settings_col.find_one({"key": "ai_mode"})
    return bool(setting and setting.get("value", False))

def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📢 Updates", url=UPDATES_URL),
            InlineKeyboardButton("❓ Help", callback_data="help_menu"),
        ],
        [
            InlineKeyboardButton("ℹ️ About", callback_data="about_menu"),
        ],
    ])

def panel_nav_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏠 Home", callback_data="home_menu"),
            InlineKeyboardButton("❌ Close", callback_data="close_menu"),
        ]
    ])

def build_start_text(user, owner_meta: str = "") -> str:
    greeting = format_user_greeting(user)
    if owner_meta:
        return (
            f"<blockquote>"
            f"<b>Hey {greeting}, welcome!</b>\n"
            f"<b>{owner_meta}</b>\n"
            f"<b>Commands: /ai on|off • /block • /unblock &lt;id&gt; • /stats • /broadcast</b>"
            f"</blockquote>"
        )
    return (
        f"<blockquote>"
        f"<b>Hey {greeting}, welcome!</b>\n"
        f"<b>Drop your message below and it will be sent directly to the owner.</b>"
        f"</blockquote>"
    )

def build_help_text() -> str:
    return (
        "<blockquote>"
        "<b>How to use this bot</b>\n"
        "<b>• Send any text, photo, video, voice, or document.</b>\n"
        "<b>• Your message is forwarded straight to the owner.</b>\n"
        "<b>• When the owner replies, you will receive their message here.</b>"
        "</blockquote>"
    )

def build_about_text() -> str:
    return (
        "<blockquote>"
        f"<b>Bot name: <a href='{UPDATES_URL}'>{BOT_NAME}</a></b>\n"
        f"<b>Library: <a href='{PYROGRAM_URL}'>Pyrogram</a></b>\n"
        f"<b>Language: <a href='{PYTHON_URL}'>Python</a></b>\n"
        f"<b>Updates: <a href='{UPDATES_URL}'>Updates Channel</a></b>\n"
        f"<b>Build status: <a href='{DEPLOY_STATUS_URL}'>Active (24/7)</a></b>\n"
        f"<b>Version: <a href='{REPO_URL}'>{BOT_VERSION}</a></b>"
        "</blockquote>"
    )

async def send_or_edit_panel(callback_query: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup):
    msg = callback_query.message
    if msg.photo:
        await msg.edit_caption(caption=text, reply_markup=reply_markup)
    else:
        await msg.edit_text(text=text, reply_markup=reply_markup, disable_web_page_preview=True)

# ==========================================
# CALLBACK HANDLERS (Help, About, Home, Close)
# ==========================================
@app.on_callback_query(filters.regex("^help_menu$"))
async def help_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    await send_or_edit_panel(callback_query, build_help_text(), panel_nav_keyboard())

@app.on_callback_query(filters.regex("^about_menu$"))
async def about_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    await send_or_edit_panel(callback_query, build_about_text(), panel_nav_keyboard())

@app.on_callback_query(filters.regex("^home_menu$"))
async def home_callback(client: Client, callback_query: CallbackQuery):
    user = callback_query.from_user
    owner_meta = ""
    if user.id == OWNER_ID:
        total_users, total_blocked, ai_on = await asyncio.gather(
            users_col.count_documents({}),
            blocked_col.count_documents({}),
            is_ai_enabled(),
        )
        ai_status = "🟢 ON" if ai_on else "🔴 OFF"
        owner_meta = f"AI Auto-Reply: {ai_status} • Users: {total_users} • Blocked: {total_blocked}"

    text = build_start_text(user, owner_meta=owner_meta)
    await callback_query.answer()
    await send_or_edit_panel(callback_query, text, start_keyboard())

@app.on_callback_query(filters.regex("^close_menu$"))
async def close_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    try:
        await callback_query.message.delete()
    except Exception:
        pass

# ==========================================
# /START COMMAND
# ==========================================
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    user = message.from_user
    if await blocked_col.find_one({"user_id": user.id}):
        return

    asyncio.create_task(save_user(user))

    owner_meta = ""
    if user.id == OWNER_ID:
        total_users, total_blocked, ai_on = await asyncio.gather(
            users_col.count_documents({}),
            blocked_col.count_documents({}),
            is_ai_enabled(),
        )
        ai_status = "🟢 ON" if ai_on else "🔴 OFF"
        owner_meta = f"AI Auto-Reply: {ai_status} • Users: {total_users} • Blocked: {total_blocked}"

    welcome_text = build_start_text(user, owner_meta=owner_meta)
    keyboard = start_keyboard()

    if has_start_image():
        try:
            await message.reply_photo(
                photo=START_IMAGE_URL.strip(),
                caption=welcome_text,
                reply_markup=keyboard,
            )
            return
        except Exception as e:
            logger.warning(f"Failed to send start image, falling back to text: {e}")

    await message.reply_text(welcome_text, reply_markup=keyboard, disable_web_page_preview=True)

# ==========================================
# ADMIN COMMANDS
# ==========================================
@app.on_message(filters.command("ai") & filters.user(OWNER_ID) & filters.private)
async def toggle_ai(client: Client, message: Message):
    if len(message.command) < 2 or message.command[1].lower() not in ("on", "off"):
        current = "ON" if await is_ai_enabled() else "OFF"
        await message.reply_text(f"<blockquote><b>Usage: <code>/ai on</code> or <code>/ai off</code> (Current: {current})</b></blockquote>")
        return

    state = message.command[1].lower() == "on"
    await settings_col.update_one({"key": "ai_mode"}, {"$set": {"value": state}}, upsert=True)
    await message.reply_text(f"<blockquote><b>AI Auto-Responder is now {'ON' if state else 'OFF'}.</b></blockquote>")

@app.on_message(filters.command("block") & filters.user(OWNER_ID) & filters.private)
async def block_user(client: Client, message: Message):
    target_user_id = None
    if message.reply_to_message:
        record = await msg_map_col.find_one({"owner_msg_id": message.reply_to_message.id})
        if record:
            target_user_id = record["user_id"]

    if not target_user_id and len(message.command) > 1:
        try:
            target_user_id = int(message.command[1])
        except ValueError:
            pass

    if not target_user_id:
        await message.reply_text("<blockquote><b>Reply to a forwarded message with <code>/block</code> or pass a User ID.</b></blockquote>")
        return

    await blocked_col.update_one(
        {"user_id": target_user_id},
        {"$set": {"blocked_at": datetime.now(IST).isoformat()}},
        upsert=True,
    )
    await message.reply_text(f"<blockquote><b>Blocked user <code>{target_user_id}</code>.</b></blockquote>")

@app.on_message(filters.command("unblock") & filters.user(OWNER_ID) & filters.private)
async def unblock_user(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("<blockquote><b>Usage: <code>/unblock &lt;user_id&gt;</code></b></blockquote>")
        return
    try:
        target_user_id = int(message.command[1])
    except ValueError:
        await message.reply_text("<blockquote><b>Invalid User ID.</b></blockquote>")
        return

    res = await blocked_col.delete_one({"user_id": target_user_id})
    msg = f"Unblocked user <code>{target_user_id}</code>." if res.deleted_count else "User was not blocked."
    await message.reply_text(f"<blockquote><b>{msg}</b></blockquote>")

@app.on_message(filters.command("stats") & filters.user(OWNER_ID) & filters.private)
async def stats_handler(client: Client, message: Message):
    total_users, total_blocked, total_msgs, ai_on = await asyncio.gather(
        users_col.count_documents({}),
        blocked_col.count_documents({}),
        msg_map_col.count_documents({}),
        is_ai_enabled(),
    )
    await message.reply_text(
        f"<blockquote>"
        f"<b>Stats</b>\n"
        f"<b>Users: <code>{total_users}</code> | Blocked: <code>{total_blocked}</code></b>\n"
        f"<b>Mapped Msgs: <code>{total_msgs}</code> | AI: <code>{'ON' if ai_on else 'OFF'}</code></b>\n"
        f"<b>Time: <code>{get_ist_time()}</code></b>"
        f"</blockquote>"
    )

@app.on_message(filters.command("broadcast") & filters.user(OWNER_ID) & filters.private)
async def broadcast_handler(client: Client, message: Message):
    if not message.reply_to_message:
        await message.reply_text("<blockquote><b>Reply to a message with <code>/broadcast</code>.</b></blockquote>")
        return

    status_msg = await message.reply_text("<blockquote><b>Broadcasting...</b></blockquote>")
    blocked_ids = {doc["user_id"] async for doc in blocked_col.find({}, {"user_id": 1})}
    sent, failed = 0, 0

    async for u in users_col.find({}, {"user_id": 1}):
        uid = u["user_id"]
        if uid == OWNER_ID or uid in blocked_ids:
            continue
        try:
            await message.reply_to_message.copy(chat_id=uid)
            sent += 1
            await asyncio.sleep(0.04)
        except Exception:
            failed += 1

    await status_msg.edit_text(
        f"<blockquote><b>Broadcast done — Sent: <code>{sent}</code> | Failed: <code>{failed}</code></b></blockquote>"
    )

# ==========================================
# USER -> OWNER RELAY
# ==========================================
@app.on_message(filters.private & ~filters.user(OWNER_ID) & ~filters.command(["start"]))
async def user_to_owner(client: Client, message: Message):
    user = message.from_user
    if await blocked_col.find_one({"user_id": user.id}):
        return

    asyncio.create_task(save_user(user))

    user_tag = f"@{user.username}" if user.username else user.first_name
    user_link = f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"

    header_text = (
        f"<blockquote>"
        f"<b>From:</b> {user_link} ({user_tag})\n"
        f"<b>ID:</b> <code>{user.id}</code> • <code>{get_ist_time()}</code>"
        f"</blockquote>"
    )

    header_msg = await client.send_message(chat_id=OWNER_ID, text=header_text)
    copied_msg = await message.copy(chat_id=OWNER_ID)

    # Map both messages and confirm forwarding to user in parallel
    map_task = msg_map_col.insert_many([
        {"owner_msg_id": header_msg.id, "user_id": user.id, "user_msg_id": message.id},
        {"owner_msg_id": copied_msg.id, "user_id": user.id, "user_msg_id": message.id},
    ])
    ack_task = message.reply_text("Your message was forwarded to the owner.")
    await asyncio.gather(map_task, ack_task)

    # Optional Gemini AI auto-reply when enabled
    if await is_ai_enabled() and message.text and gemini_client:
        try:
            prompt = (
                f"Reply briefly and helpfully in 1-2 sentences to {user.first_name}, who messaged: "
                f"'{message.text}'. Let them know their message is with the owner."
            )
            response = await asyncio.to_thread(
                gemini_client.models.generate_content,
                model="gemini-2.5-flash",
                contents=prompt,
            )
            if response.text:
                await message.reply_text(f"<blockquote><b>{response.text.strip()}</b></blockquote>")
        except Exception as e:
            logger.error(f"Gemini error: {e}")

# ==========================================
# OWNER -> USER REPLY RELAY (Pure Message Only)
# ==========================================
@app.on_message(filters.private & filters.user(OWNER_ID) & filters.reply)
async def owner_to_user(client: Client, message: Message):
    if message.text and message.text.startswith("/"):
        return

    record = await msg_map_col.find_one({"owner_msg_id": message.reply_to_message.id})
    if not record:
        await message.reply_text("<blockquote><b>Reply to a forwarded user message or its header.</b></blockquote>")
        return

    target_user_id = record["user_id"]
    original_user_msg_id = record.get("user_msg_id")

    try:
        if message.text:
            await client.send_message(
                chat_id=target_user_id,
                text=message.text,
                reply_to_message_id=original_user_msg_id,
            )
        else:
            await message.copy(
                chat_id=target_user_id,
                reply_to_message_id=original_user_msg_id,
            )
    except UserIsBlocked:
        await message.reply_text("<blockquote><b>User blocked the bot.</b></blockquote>")
    except Exception as e:
        await message.reply_text(f"<blockquote><b>Error: <code>{e}</code></b></blockquote>")

# ==========================================
# STARTUP ENTRYPOINT
# ==========================================
if __name__ == "__main__":
    Thread(target=run_flask, daemon=True).start()
    logger.info(f"Flask server started on port {PORT}")
    app.run()
