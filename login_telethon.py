"""One-time Telethon session login script.

Run this once to authenticate your Telegram user account for
Telethon MTProto uploads (supports files up to 2GB).

Usage:
    python login_telethon.py
"""

import asyncio
import sys
from pathlib import Path

# Load .env
from dotenv import load_dotenv
load_dotenv()

import os


async def main():
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    session_name = os.getenv("TELETHON_SESSION", "telethon.session")

    if not api_id or not api_hash:
        print("❌ TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env")
        print("   Get them from https://my.telegram.org")
        sys.exit(1)

    print("=" * 50)
    print("  Telethon Session Login")
    print("=" * 50)
    print()
    print(f"  API ID:     {api_id}")
    print(f"  Session:    {session_name}")
    print()
    print("  You will be asked for your phone number")
    print("  and the verification code sent to your Telegram.")
    print()
    print("=" * 50)
    print()

    from telethon import TelegramClient

    client = TelegramClient(session_name, int(api_id), api_hash)

    await client.start()

    me = await client.get_me()
    print()
    print(f"✅ Logged in as: {me.first_name} (@{me.username})")
    print(f"   Session saved to: {session_name}")
    print()
    print("You can now run the bot with Telethon 2GB upload support.")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
