from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.tl.functions.channels import CreateChannelRequest
from telethon.tl.types import Channel
from typing import Optional, List, Dict
import asyncio
from pathlib import Path
import config
import logging

logger = logging.getLogger(__name__)
class TelegramManager:
    """Manages Telegram client sessions and operations."""
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.session_file = config.SESSION_DIR / f"session_{user_id}"
        self.client: Optional[TelegramClient] = None
        self._is_connected = False
        
    async def initialize(self) -> TelegramClient:
        """Initialize the Telegram client."""
        if not self.client:
            self.client = TelegramClient(
                str(self.session_file),
                config.TELEGRAM_API_ID,
                config.TELEGRAM_API_HASH
            )
        return self.client
    
    async def ensure_connected(self):
        """Ensure the client is connected."""
        await self.initialize()
        if not self.client.is_connected():
            await self.client.connect()

    async def send_code(self, phone: str) -> Dict[str, any]:
        """Send authentication code to phone number."""
        logger.info(f"Connecting to Telegram for {phone}...")
        await self.ensure_connected()
        
        if await self.is_authenticated():
            logger.info(f"User {phone} is already authenticated.")
            return {
                "phone_code_hash": "ALREADY_AUTHENTICATED",
                "phone": phone,
                "is_authenticated": True
            }
        
        try:
            logger.info(f"Sending code request to {phone}...")
            result = await self.client.send_code_request(phone)
            logger.info(f"Code sent successfully. Hash: {result.phone_code_hash}")
        except ConnectionError:
            logger.warning("Connection error, retrying...")
            # Retry once if connection failed
            await self.client.disconnect()
            await self.client.connect()
            result = await self.client.send_code_request(phone)
            logger.info(f"Code sent successfully after retry. Hash: {result.phone_code_hash}")
            
        return {
            "phone_code_hash": result.phone_code_hash,
            "phone": phone,
            "is_authenticated": False
        }
    
    async def verify_code(self, phone: str, code: str, phone_code_hash: str) -> bool:
        """Verify the authentication code."""
        try:
            await self.client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            self._is_connected = True
            return True
        except SessionPasswordNeededError:
            # 2FA is enabled, need password
            raise Exception("2FA_REQUIRED")
        except Exception as e:
            raise Exception(f"Verification failed: {str(e)}")
    
    async def verify_password(self, password: str) -> bool:
        """Verify 2FA password if required."""
        try:
            await self.client.sign_in(password=password)
            self._is_connected = True
            return True
        except Exception as e:
            raise Exception(f"Password verification failed: {str(e)}")
    
    async def is_authenticated(self) -> bool:
        """Check if user is authenticated."""
        if not self.client:
            await self.initialize()
        
        if not self.client.is_connected():
            await self.client.connect()
        
        return await self.client.is_user_authorized()
    
    async def get_channels(self) -> List[Dict[str, any]]:
        """Get list of channels/groups the user has joined."""
        if not await self.is_authenticated():
            raise Exception("Not authenticated")
        
        dialogs = await self.client.get_dialogs()
        channels = []
        
        for dialog in dialogs:
            entity = dialog.entity
            # Include channels, supergroups, and groups
            if isinstance(entity, Channel) or hasattr(entity, 'megagroup'):
                channels.append({
                    "id": entity.id,
                    "title": entity.title,
                    "username": getattr(entity, 'username', None),
                    "type": "channel" if getattr(entity, 'broadcast', False) else "group",
                    "member_count": getattr(entity, 'participants_count', None)
                })
        
        return channels
    
    async def create_channel(self, title: str, about: str = "") -> Dict[str, any]:
        """Create a new private channel."""
        if not await self.is_authenticated():
            raise Exception("Not authenticated")
        
        result = await self.client(CreateChannelRequest(
            title=title,
            about=about,
            megagroup=False  # False = channel, True = supergroup
        ))
        
        channel = result.chats[0]
        return {
            "id": channel.id,
            "title": channel.title,
            "username": getattr(channel, 'username', None)
        }
    
    async def get_channel_photo(self, channel_id: int) -> Optional[bytes]:
        """Download channel profile photo."""
        if not await self.is_authenticated():
            raise Exception("Not authenticated")
            
        try:
            # Get the entity (channel/group)
            entity = await self.client.get_entity(channel_id)
            
            # Download profile photo into memory
            from io import BytesIO
            out = BytesIO()
            await self.client.download_profile_photo(entity, out, download_big=False)
            return out.getvalue()
        except Exception as e:
            logger.error(f"Error fetching photo for {channel_id}: {e}")
            return None
    
    async def disconnect(self):
        """Disconnect the client."""
        if self.client and self.client.is_connected():
            await self.client.disconnect()
            self._is_connected = False
    
    def delete_session(self):
        """Delete the session file (logout)."""
        if self.session_file.exists():
            self.session_file.unlink()


# Global registry of active clients
_active_clients: Dict[str, TelegramManager] = {}

def get_telegram_manager(user_id: str) -> TelegramManager:
    """Get or create a TelegramManager for a user."""
    if user_id not in _active_clients:
        _active_clients[user_id] = TelegramManager(user_id)
    return _active_clients[user_id]

async def cleanup_client(user_id: str):
    """Cleanup and remove a client from the registry."""
    if user_id in _active_clients:
        await _active_clients[user_id].disconnect()
        del _active_clients[user_id]

# Global bot client instance
_bot_client: Optional[TelegramClient] = None

async def get_bot_client() -> TelegramClient:
    """Get or create the Bot client."""
    global _bot_client
    if not _bot_client:
        if not config.TELEGRAM_BOT_TOKEN:
             raise Exception("TELEGRAM_BOT_TOKEN not set")
             
        _bot_client = TelegramClient(
            str(config.SESSION_DIR / "bot_session"),
            config.TELEGRAM_API_ID,
            config.TELEGRAM_API_HASH
        )
        await _bot_client.start(bot_token=config.TELEGRAM_BOT_TOKEN)
        
    if not _bot_client.is_connected():
        await _bot_client.connect()
        
    return _bot_client
