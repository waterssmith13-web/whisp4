import asyncio
import os
import re
import secrets
import time
from pathlib import Path
from typing import Optional

import aiosqlite
from aiogram import Bot, Dispatcher, F, types
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandObject

WHISPER_BOT_TOKEN = os.getenv("WHISPER_BOT_TOKEN")
if not WHISPER_BOT_TOKEN:
    raise RuntimeError("Set the WHISPER_BOT_TOKEN environment variable.")

DB_PATH = os.getenv(
    "WHISPERS_DB_PATH",
    str(Path(__file__).resolve().with_name("whispers.db")),
)
MAX_WHISPER_CHARS = 4000
WHISPER_LIFETIME_SECONDS = 24 * 60 * 60
BOT_USERNAME = ""

bot = Bot(token=WHISPER_BOT_TOKEN)
dp = Dispatcher()


async def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS whispers (
                whisper_id TEXT PRIMARY KEY,
                sender_id INTEGER NOT NULL,
                sender_username TEXT,
                target_username TEXT NOT NULL,
                secret_text TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                is_read INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_whispers (
                user_id INTEGER PRIMARY KEY,
                target_username TEXT NOT NULL
            )
        """)
        await db.commit()


def normalize_username(value: str) -> Optional[str]:
    username = value.strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username):
        return None
    return username


def split_message(text: str, max_units: int = 3900) -> list[str]:
    chunks = []
    current = []
    current_units = 0

    for character in text:
        units = 2 if ord(character) > 0xFFFF else 1
        if current and current_units + units > max_units:
            chunks.append("".join(current))
            current = []
            current_units = 0
        current.append(character)
        current_units += units

    if current:
        chunks.append("".join(current))
    return chunks


def read_whisper_keyboard(whisper_id: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(
            text="Read privately",
            url=f"https://t.me/{BOT_USERNAME}?start=whisper_{whisper_id}",
        )
    ]])


async def get_whisper(whisper_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT sender_id, sender_username, target_username, secret_text, expires_at
               FROM whispers WHERE whisper_id = ?""",
            (whisper_id,),
        ) as cursor:
            return await cursor.fetchone()


async def send_whisper(chat_id: int, whisper_id: str, user: types.User) -> bool:
    row = await get_whisper(whisper_id)
    if not row:
        await bot.send_message(chat_id, "This whisper does not exist.")
        return False

    _, sender_username, target_username, secret_text, expires_at = row
    if int(time.time()) > expires_at:
        await bot.send_message(chat_id, "This whisper has expired.")
        return False

    if not user.username or user.username.lower() != target_username.lower():
        await bot.send_message(
            chat_id,
            f"This whisper is only for @{target_username}.",
        )
        return False

    sender = f"@{sender_username}" if sender_username else "an anonymous sender"
    await bot.send_message(chat_id, f"Private whisper from {sender}:")
    chunks = split_message(secret_text)
    for index, chunk in enumerate(chunks, start=1):
        if len(chunks) == 1:
            await bot.send_message(chat_id, chunk)
        else:
            await bot.send_message(chat_id, f"Part {index}/{len(chunks)}:\n{chunk}")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE whispers SET is_read = 1 WHERE whisper_id = ?",
            (whisper_id,),
        )
        await db.commit()
    return True


@dp.message(Command("start"))
async def handle_start(message: types.Message, command: CommandObject):
    args = command.args or ""
    if args.startswith("whisper_"):
        whisper_id = args.removeprefix("whisper_")
        await send_whisper(message.chat.id, whisper_id, message.from_user)
        return

    await message.answer(
        "Welcome to Psst!\n\n"
        "Create a private whisper:\n"
        "/whisper @username\n"
        "Then send your secret as your next message (up to 4,000 characters).\n\n"
        "The bot will give you a button to share in a group. The recipient can "
        "tap Read privately to open this bot and read the whisper. Whispers are "
        "available for 24 hours and can be reopened during that time.\n\n"
        "Use /help for instructions or /cancel to cancel a draft."
    )


@dp.message(Command("help"))
async def handle_help(message: types.Message):
    await message.answer(
        "How to send a whisper:\n"
        "1. Open this bot's private chat and send /whisper @username.\n"
        "2. Send the secret as your next message (up to 4,000 characters).\n"
        "3. Tap Share to a chat and choose the group where you want to post it.\n"
        "4. The intended recipient taps Read privately. If Telegram asks, they "
        "must press Start in this bot the first time.\n\n"
        "If inline results do not appear, ask the bot owner to enable inline "
        "mode for this bot with /setinline in @BotFather.\n"
        "Only the matching Telegram username can read it. The recipient can "
        "open it again for 24 hours. Use /cancel to discard a draft."
    )


@dp.message(Command("whisper"))
async def handle_whisper_command(
    message: types.Message,
    command: CommandObject,
):
    if message.chat.type != "private":
        await message.answer("Please create whispers in this bot's private chat.")
        return

    args = command.args or ""
    target_arg = args.split(maxsplit=1)[0] if args else ""
    target_username = normalize_username(target_arg)
    if not target_username:
        await message.answer(
            "Usage: /whisper @username\n"
            "The recipient must have a Telegram username."
        )
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO pending_whispers (user_id, target_username)
               VALUES (?, ?)""",
            (message.from_user.id, target_username),
        )
        await db.commit()

    await message.answer(
        f"Send the secret for @{target_username} as your next message "
        f"(up to {MAX_WHISPER_CHARS:,} characters). Send /cancel to stop."
    )


@dp.message(Command("cancel"))
async def handle_cancel(message: types.Message):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM pending_whispers WHERE user_id = ?",
            (message.from_user.id,),
        )
        await db.commit()
    await message.answer("Whisper draft cancelled.")


@dp.message(F.chat.type == "private", F.text)
async def receive_whisper_text(message: types.Message):
    if message.text.startswith("/"):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT target_username FROM pending_whispers WHERE user_id = ?",
            (message.from_user.id,),
        ) as cursor:
            pending = await cursor.fetchone()

    if not pending:
        return

    secret_text = message.text
    if not secret_text.strip():
        await message.answer("The whisper cannot be empty. Send the secret text.")
        return
    if len(secret_text) > MAX_WHISPER_CHARS:
        await message.answer(
            f"That message is {len(secret_text):,} characters. "
            f"Please send no more than {MAX_WHISPER_CHARS:,} characters."
        )
        return

    target_username = pending[0]
    whisper_id = secrets.token_urlsafe(8)
    expires_at = int(time.time()) + WHISPER_LIFETIME_SECONDS
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO whispers
               (whisper_id, sender_id, sender_username, target_username,
                secret_text, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                whisper_id,
                message.from_user.id,
                message.from_user.username,
                target_username,
                secret_text,
                expires_at,
            ),
        )
        await db.execute(
            "DELETE FROM pending_whispers WHERE user_id = ?",
            (message.from_user.id,),
        )
        await db.commit()

    share_keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(
            text="Share to a chat",
            switch_inline_query=f"share_{whisper_id}",
        )
    ]])
    await message.answer(
        f"Whisper saved for @{target_username}. It expires in 24 hours. "
        "Tap below and choose a group to share it.",
        reply_markup=share_keyboard,
    )


@dp.inline_query()
async def inline_handler(inline_query: types.InlineQuery):
    query = inline_query.query.strip()
    if not query.startswith("share_"):
        await inline_query.answer([], cache_time=0, is_personal=True)
        return

    whisper_id = query.removeprefix("share_")
    row = await get_whisper(whisper_id)
    if not row:
        await inline_query.answer([], cache_time=0, is_personal=True)
        return

    sender_id, _, target_username, _, expires_at = row
    if sender_id != inline_query.from_user.id or int(time.time()) > expires_at:
        await inline_query.answer([], cache_time=0, is_personal=True)
        return

    result = types.InlineQueryResultArticle(
        id=whisper_id,
        title=f"Private whisper for @{target_username}",
        description="Only the intended recipient can open it.",
        input_message_content=types.InputTextMessageContent(
            message_text=(
                f"A private whisper for @{target_username}.\n"
                "Only the intended recipient can open it."
            ),
        ),
        reply_markup=read_whisper_keyboard(whisper_id),
    )
    await inline_query.answer([result], cache_time=0, is_personal=True)


@dp.callback_query(lambda callback: callback.data and callback.data.startswith("whsp:"))
async def handle_legacy_whisper_click(callback: types.CallbackQuery):
    whisper_id = callback.data.split(":", 1)[1]
    row = await get_whisper(whisper_id)
    if not row:
        await callback.answer("This whisper does not exist.", show_alert=True)
        return

    _, _, target_username, _, expires_at = row
    if int(time.time()) > expires_at:
        await callback.answer("This whisper has expired.", show_alert=True)
        return
    if (
        not callback.from_user.username
        or callback.from_user.username.lower() != target_username.lower()
    ):
        await callback.answer(
            f"This whisper is only for @{target_username}.",
            show_alert=True,
        )
        return

    try:
        await send_whisper(
            callback.from_user.id,
            whisper_id,
            callback.from_user,
        )
    except TelegramForbiddenError:
        await callback.answer(
            "Open this bot and press Start, then tap the whisper button again.",
            show_alert=True,
        )
        return

    await callback.answer("The whisper was sent to your private chat.")


async def main():
    global BOT_USERNAME

    await init_db()
    bot_info = await bot.get_me()
    if not bot_info.username:
        raise RuntimeError("Telegram did not return a username for this bot.")
    BOT_USERNAME = bot_info.username
    print(f"Whisper bot @{BOT_USERNAME} is running.")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
