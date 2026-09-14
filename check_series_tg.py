import asyncio
import os
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()
api_id = int(os.getenv("TELEGRAM_API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH")

async def main():
    client = TelegramClient("telethon.session", api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        print("Not authorized")
        return
    
    # Check invite link or channel
    invite_hash = "Y_lBEMjndAc0MmI9"
    try:
        from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
        res = await client(CheckChatInviteRequest(invite_hash))
        print("CheckChatInvite result:", type(res), getattr(res, 'title', None))
    except Exception as e:
        print("Check invite error:", e)

    await client.disconnect()

asyncio.run(main())
