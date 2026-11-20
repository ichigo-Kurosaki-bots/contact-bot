import os
import time
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
)
from pyrogram.errors import UserIsBlocked, UserNotParticipant, ChatAdminRequired, PeerIdInvalid

# ==========================================
# CONFIGURATION (Set in Render Env Vars)
# ==========================================
API_ID = int(os.environ.get("API_ID", "12345678"))
API_HASH = os.environ.get("API_HASH", "your_api_hash_here")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token_here")
OWNER_ID = int(os.environ.get("OWNER_ID", "123456789"))
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true&w=majority")

# Add your start image URL here or via START_IMAGE_URL env var
START_IMAGE_URL = os.environ.get("START_IMAGE_URL", "")
BOT_NAME = os.environ.get("BOT_NAME", "Aero Contact Relay")
BOT_VERSION = os.environ.get("BOT_VERSION", "v3.3")

UPDATES_URL = os.environ.get("UPDATES_CHANNEL_URL", "https://t.me/Aero_Unity")
REPO_URL = os.environ.get("REPO_URL", "https://t.me/Aero_Unity")
DEPLOY_STATUS_URL = os.environ.get("DEPLOY_STATUS_URL", "https://t.me/Aero_Unity")
PYROGRAM_URL = os.environ.get("PYROGRAM_URL", "https://docs.pyrogram.org")
PYTHON_URL = os.environ.get("PYTHON_URL", "https://www.python.org")
PORT = int(os.environ.get("PORT", 8080))

IST = timezone(timedelta(hours=5, minutes=30))
BOT_START_TIME = time.time()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ==========================================
# DATABASE SETUP
# ==========================================
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["ContactRelayBot"]
users_col = db["users"]
msg_map_col = db["message_map"]
blocked_col = db["blocked_users"]
fsub_col = db["forcesub_channels"]

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

def get_bot_uptime() -> str:
    elapsed = int(time.time() - BOT_START_TIME)
    days, rem = divmod(elapsed, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)

def format_user_greeting(user) -> str:
    if user.username:
        return f"@{user.username}"
    return user.first_name or "there"

def has_start_image() -> bool:
    return bool(START_IMAGE_URL and START_IMAGE_URL.strip().startswith(("http://", "https://")))

def normalize_chat_target(raw_target: str):
    cleaned = raw_target.strip()
    if cleaned.startswith("https://t.me/"):
        cleaned = cleaned.replace("https://t.me/", "").split("/")[0]
    if cleaned.startswith("http://t.me/"):
        cleaned = cleaned.replace("http://t.me/", "").split("/")[0]
    if cleaned.lstrip("-").isdigit():
        return int(cleaned)
    if not cleaned.startswith("@"):
        cleaned = f"@{cleaned}"
    return cleaned

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

async def get_missing_fsub_channels(client: Client, user_id: int) -> list[dict]:
    if user_id == OWNER_ID:
        return []

    channels = [doc async for doc in fsub_col.find({})]
    if not channels:
        return []

    missing = []
    for ch in channels:
        chat_target = ch.get("chat_id")
        try:
            member = await client.get_chat_member(chat_target, user_id)
            if member.status in (
                enums.ChatMemberStatus.BANNED,
                enums.ChatMemberStatus.LEFT,
                enums.ChatMemberStatus.RESTRICTED,
            ):
                missing.append(ch)
        except UserNotParticipant:
            missing.append(ch)
        except (ChatAdminRequired, PeerIdInvalid) as e:
            logger.warning(f"ForceSub check warning for {chat_target}: {e}")
        except Exception as e:
            logger.warning(f"ForceSub error for {chat_target}: {e}")
    return missing

def build_fsub_markup(missing_channels: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for idx, ch in enumerate(missing_channels, start=1):
        title = ch.get("title") or f"Channel {idx}"
        invite_link = ch.get("invite_link") or UPDATES_URL
        row.append(InlineKeyboardButton(f"📢 Join {title}", url=invite_link))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔄 Try Again", callback_data="recheck_fsub")])
    return InlineKeyboardMarkup(rows)

def build_fsub_text(missing_channels: list[dict]) -> str:
    lines = ["<blockquote><b>Please join the required channel(s) below to continue:</b>"]
    for idx, ch in enumerate(missing_channels, start=1):
        title = ch.get("title") or f"Channel {idx}"
        link = ch.get("invite_link") or UPDATES_URL
        lines.append(f"<b>{idx}. <a href='{link}'>{title}</a></b>")
    lines.append("</blockquote>")
    return "\n".join(lines)

def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("• Updates •", url=UPDATES_URL),
            InlineKeyboardButton("• Help •", callback_data="help_menu"),
        ],
        [
            InlineKeyboardButton("• About •", callback_data="about_menu"),
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
            f"<b>Hey {greeting}, welcome!</b>\n\n"
            f"<b>{owner_meta}</b>\n"
            f"<b>Commands: /addforcesub • /removeforcesub • /forcesublist • /botuptime • /block • /unblock • /stats • /broadcast</b>"
            f"</blockquote>"
        )
    return (
        f"<blockquote>"
        f"<b>Hey {greeting}, welcome!</b>\n\n"
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
        f"<b>Updates: <a href='{UPDATES_URL}'>Aero Unity</a></b>\n"
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
# CALLBACK HANDLERS (Help, About, Home, Close, ForceSub)
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
        total_users, total_blocked, total_fsub = await asyncio.gather(
            users_col.count_documents({}),
            blocked_col.count_documents({}),
            fsub_col.count_documents({}),
        )
        owner_meta = f"Uptime: {get_bot_uptime()} • Users: {total_users} • Blocked: {total_blocked} • FSub: {total_fsub}"

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

@app.on_callback_query(filters.regex("^recheck_fsub$"))
async def recheck_fsub_callback(client: Client, callback_query: CallbackQuery):
    user = callback_query.from_user
    if await blocked_col.find_one({"user_id": user.id}):
        await callback_query.answer("Access denied.", show_alert=True)
        return

    missing = await get_missing_fsub_channels(client, user.id)
    if missing:
        await callback_query.answer("You haven't joined all required channels yet.", show_alert=True)
        await send_or_edit_panel(callback_query, build_fsub_text(missing), build_fsub_markup(missing))
        return

    await callback_query.answer("Verified! Welcome.", show_alert=False)
    asyncio.create_task(save_user(user))
    welcome_text = build_start_text(user)
    await send_or_edit_panel(callback_query, welcome_text, start_keyboard())

# ==========================================
# /START COMMAND
# ==========================================
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    user = message.from_user
    if await blocked_col.find_one({"user_id": user.id}):
        return

    missing = await get_missing_fsub_channels(client, user.id)
    if missing:
        await message.reply_text(
            build_fsub_text(missing),
            reply_markup=build_fsub_markup(missing),
            disable_web_page_preview=True,
        )
        return

    asyncio.create_task(save_user(user))

    owner_meta = ""
    if user.id == OWNER_ID:
        total_users, total_blocked, total_fsub = await asyncio.gather(
            users_col.count_documents({}),
            blocked_col.count_documents({}),
            fsub_col.count_documents({}),
        )
        owner_meta = f"Uptime: {get_bot_uptime()} • Users: {total_users} • Blocked: {total_blocked} • FSub: {total_fsub}"

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
# FORCESUB & UPTIME COMMANDS (OWNER ONLY)
# ==========================================
@app.on_message(filters.command("addforcesub") & filters.user(OWNER_ID) & filters.private)
async def add_forcesub_handler(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("<blockquote><b>Usage: <code>/addforcesub &lt;channel_id_or_@username&gt;</code></b></blockquote>")
        return

    target = normalize_chat_target(message.command[1])
    try:
        chat = await client.get_chat(target)
        invite_link = chat.invite_link
        if not invite_link:
            if chat.username:
                invite_link = f"https://t.me/{chat.username}"
            else:
                invite_link = await client.export_chat_invite_link(chat.id)

        title = chat.title or str(chat.id)
        await fsub_col.update_one(
            {"chat_id": chat.id},
            {
                "$set": {
                    "chat_id": chat.id,
                    "title": title,
                    "username": chat.username,
                    "invite_link": invite_link,
                    "added_at": datetime.now(IST).isoformat(),
                }
            },
            upsert=True,
        )
        await message.reply_text(
            f"<blockquote><b>Added ForceSub channel: <a href='{invite_link}'>{title}</a> (<code>{chat.id}</code>)</b></blockquote>",
            disable_web_page_preview=True,
        )
    except Exception as e:
        await message.reply_text(
            f"<blockquote><b>Failed to add channel. Make sure the bot is an admin in the channel.\nError: <code>{e}</code></b></blockquote>"
        )

@app.on_message(filters.command("removeforcesub") & filters.user(OWNER_ID) & filters.private)
async def remove_forcesub_handler(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("<blockquote><b>Usage: <code>/removeforcesub &lt;channel_id_or_@username&gt;</code></b></blockquote>")
        return

    raw = message.command[1].strip()
    target = normalize_chat_target(raw)

    query = {"$or": [{"chat_id": target}, {"username": raw.lstrip("@")}]}
    if isinstance(target, int):
        query = {"chat_id": target}

    res = await fsub_col.delete_one(query)
    if res.deleted_count:
        await message.reply_text(f"<blockquote><b>Removed <code>{raw}</code> from ForceSub list.</b></blockquote>")
    else:
        await message.reply_text(f"<blockquote><b>Channel <code>{raw}</code> was not found in ForceSub list.</b></blockquote>")

@app.on_message(filters.command("forcesublist") & filters.user(OWNER_ID) & filters.private)
async def forcesub_list_handler(client: Client, message: Message):
    channels = [doc async for doc in fsub_col.find({})]
    if not channels:
        await message.reply_text("<blockquote><b>No ForceSub channels configured.</b></blockquote>")
        return

    lines = ["<blockquote><b>ForceSub Channels</b>"]
    for idx, ch in enumerate(channels, start=1):
        title = ch.get("title") or "Channel"
        cid = ch.get("chat_id")
        link = ch.get("invite_link") or UPDATES_URL
        lines.append(f"<b>{idx}. <a href='{link}'>{title}</a> — <code>{cid}</code></b>")
    lines.append("</blockquote>")
    await message.reply_text("\n".join(lines), disable_web_page_preview=True)

@app.on_message(filters.command("botuptime") & filters.user(OWNER_ID) & filters.private)
async def bot_uptime_handler(client: Client, message: Message):
    start_dt = datetime.fromtimestamp(BOT_START_TIME, tz=IST).strftime("%I:%M %p • %d %b %Y")
    await message.reply_text(
        f"<blockquote>"
        f"<b>Bot Uptime: <code>{get_bot_uptime()}</code></b>\n"
        f"<b>Started At: <code>{start_dt} IST</code></b>\n"
        f"<b>Current Time: <code>{get_ist_time()} IST</code></b>"
        f"</blockquote>"
    )

# ==========================================
# OTHER ADMIN COMMANDS
# ==========================================
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
    total_users, total_blocked, total_msgs, total_fsub = await asyncio.gather(
        users_col.count_documents({}),
        blocked_col.count_documents({}),
        msg_map_col.count_documents({}),
        fsub_col.count_documents({}),
    )
    await message.reply_text(
        f"<blockquote>"
        f"<b>Stats</b>\n"
        f"<b>Users: <code>{total_users}</code> | Blocked: <code>{total_blocked}</code></b>\n"
        f"<b>Mapped Msgs: <code>{total_msgs}</code> | FSub: <code>{total_fsub}</code></b>\n"
        f"<b>Uptime: <code>{get_bot_uptime()}</code></b>"
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

    missing = await get_missing_fsub_channels(client, user.id)
    if missing:
        await message.reply_text(
            build_fsub_text(missing),
            reply_markup=build_fsub_markup(missing),
            disable_web_page_preview=True,
        )
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

    map_task = msg_map_col.insert_many([
        {"owner_msg_id": header_msg.id, "user_id": user.id, "user_msg_id": message.id},
        {"owner_msg_id": copied_msg.id, "user_id": user.id, "user_msg_id": message.id},
    ])
    ack_task = message.reply_text("Your message was forwarded to the owner.")
    await asyncio.gather(map_task, ack_task)

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
