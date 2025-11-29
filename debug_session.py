import asyncio
import os
from pathlib import Path
from telethon import TelegramClient
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("TELEGRAM_API_ID"))
API_HASH = os.getenv("TELEGRAM_API_HASH")
SESSION_DIR = Path(os.getenv("SESSION_DIR", "./sessions"))

async def check_session(phone):
    # Telethon appends .session automatically
    session_path = SESSION_DIR / f"session_{phone}"
    print(f"Using session path: {session_path}")
    
    # Check if file exists (with extension)
    real_file = session_path.with_suffix(".session")
    if real_file.exists():
        print(f"Session file found: {real_file}")
    else:
        print(f"Session file NOT found: {real_file}")

    client = TelegramClient(str(session_path), API_ID, API_HASH)
    
    try:
        print("Connecting...")
        await client.connect()
        is_auth = await client.is_user_authorized()
        print(f"Is authorized: {is_auth}")
        
        if is_auth:
            me = await client.get_me()
            print(f"Logged in as: {me.first_name} ({me.id})")
        
        await client.disconnect()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(check_session("+33759863632"))
