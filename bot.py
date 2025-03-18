import logging
import time
import threading
import asyncio

import humanreadable as hr
from flask import Flask
from telethon import Button
from telethon.sync import TelegramClient, events
from telethon.tl.custom.message import Message
from telethon.types import UpdateNewMessage

from config import (ADMINS, API_HASH, API_ID, BOT_TOKEN, BOT_USERNAME, FORCE_LINK)
from redis_db import db
from send_media import VideoSender
from tools import generate_shortenedUrl, is_user_on_chat, remove_all_videos

# Logger Configuration
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Use session file to prevent re-authentication issues
bot = TelegramClient("bot_session", API_ID, API_HASH)


async def start_bot():
    """ Start the bot """
    await bot.start(bot_token=BOT_TOKEN)
    log.info("Bot started successfully!")
    await bot.run_until_disconnected()


# ========================= HANDLERS ========================= #

@bot.on(events.NewMessage(pattern="/start$", incoming=True, func=lambda x: x.is_private))
async def start(m: Message):
    """ Handles /start command """
    reply_text = """
Hello there! I'm your friendly video downloader bot specially designed to fetch videos from Terabox. 

Share the **Terabox link** with me, and I'll swiftly get started on downloading it for you.

📌 **Join Our Channel for Updates**: [RoldexVerse](https://t.me/RoldexVerse)

Happy downloading! 🚀
"""
    await m.reply(
        reply_text,
        link_preview=False,
        parse_mode="markdown",
        buttons=[
            [
                Button.url("Website Source Code", url="https://github.com/r0ld3x/terabox-app"),
                Button.url("Bot Source Code", url="https://github.com/r0ld3x/terabox-downloader-bot"),
            ],
            [
                Button.url("Channel", url="https://t.me/RoldexVerse"),
                Button.url("Group", url="https://t.me/RoldexVerseChats"),
            ],
        ],
    )


@bot.on(events.NewMessage(pattern="/gen$", incoming=True, func=lambda x: x.is_private))
async def generate_token(m: Message):
    """ Generates a new session token for users """
    is_user_active = db.get(f"active_{m.sender_id}")
    if is_user_active:
        ttl = db.ttl(f"active_{m.sender_id}")
        t = hr.Time(str(ttl), default_unit=hr.Time.Unit.SECOND)
        return await m.reply(
            f"""✅ You are already active.
⏳ **Session expires in**: {t.to_humanreadable()}"""
        )

    shortenedUrl = generate_shortenedUrl(m.sender_id)
    if not shortenedUrl:
        return await m.reply("⚠️ Something went wrong. Please try again.")

    text = f"""
👋 **Hey {m.sender.first_name or m.sender.username}!**

🔄 Your **session token has expired**. Please refresh your token to continue using the bot.

⏳ **Token Timeout**: 1 hour

👉 **Why do I need this?**  
After viewing an ad, you can use the bot **for 1 hour** without restrictions.

🔄 **Click below to refresh your session:**
"""
    await m.reply(
        text,
        link_preview=False,
        parse_mode="markdown",
        buttons=[Button.url("Refresh Token 🔄", url=shortenedUrl)],
    )


@bot.on(events.NewMessage(pattern="/remove (.*)", incoming=True, from_users=ADMINS))
async def remove(m: UpdateNewMessage):
    """ Admin command to remove a user from active list """
    user_id = m.pattern_match.group(1)
    if db.get(f"check_{user_id}"):
        db.delete(f"check_{user_id}")
        await m.reply(f"✅ Removed {user_id} from the list.")
    else:
        await m.reply(f"⚠️ {user_id} is not in the list.")


@bot.on(events.NewMessage(pattern="/removeall", incoming=True, from_users=ADMINS))
async def removeall(m: UpdateNewMessage):
    """ Admin command to remove all active users """
    remove_all_videos()
    return await m.reply("✅ Removed all videos from the list.")


@bot.on(events.NewMessage(
    pattern=r"/start token_([0-9a-f]{8}-[0-9a-f]{4}-[0-5][0-9a-f]{3}-[089ab][0-9a-f]{3}-[0-9a-f]{12})",
    incoming=True,
    func=lambda x: x.is_private,
))
async def start_token(m: Message):
    """ Handles token authentication """
    uuid = m.pattern_match.group(1).strip()

    # Check if user has joined required channels
    check_if = await is_user_on_chat(bot, FORCE_LINK, m.peer_id)
    if not check_if:
        return await m.reply(
            "⚠️ You haven't joined @RoldexVerse or @RoldexVerseChats yet. **Please join and try again!**",
            buttons=[
                [
                    Button.url("📢 RoldexVerse", url="https://t.me/RoldexVerse"),
                    Button.url("💬 RoldexVerse Chats", url="https://t.me/RoldexVerseChats"),
                ],
                [
                    Button.url(
                        "🔄 ReCheck",
                        url=f"https://{BOT_USERNAME}.t.me?start={uuid}",
                    ),
                ],
            ],
        )

    # Check if user already has an active session
    is_user_active = db.get(f"active_{m.sender_id}")
    if is_user_active:
        ttl = db.ttl(f"active_{m.sender_id}")
        t = hr.Time(str(ttl), default_unit=hr.Time.Unit.SECOND)
        return await m.reply(
            f"""✅ You are already active.
⏳ **Session expires in**: {t.to_humanreadable()}"""
        )

    # Validate token
    if_token_avl = db.get(f"token_{uuid}")
    if not if_token_avl:
        return await generate_token(m)

    sender_id, shortenedUrl = if_token_avl.split("|")
    if m.sender_id != int(sender_id):
        return await m.reply("⚠️ Your token is invalid. **Request a new one using /gen**")

    # Activate user session
    set_user_active = db.set(f"active_{m.sender_id}", time.time(), ex=3600)
    db.delete(f"token_{uuid}")

    if set_user_active:
        return await m.reply("✅ **Your account is now active!**\n⏳ **Expires in 1 hour.**")


# ========================= FLASK HEALTH CHECK ========================= #

app = Flask(__name__)

@app.route('/')
def health_check():
    """ Health check endpoint for Koyeb """
    return "✅ Bot is running!", 200


# ========================= RUNNING BOT & FLASK TOGETHER ========================= #

def run_flask():
    """ Run Flask app in a separate thread """
    app.run(host="0.0.0.0", port=8000)


if __name__ == "__main__":
    # Start Flask server in a separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Start Telegram bot in the main async loop
    asyncio.run(start_bot())
