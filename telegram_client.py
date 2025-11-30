from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.tl.functions.channels import CreateChannelRequest
from telethon.tl.types import Channel, ChatPhotoEmpty, UserProfilePhotoEmpty
from telethon.sessions import StringSession
from typing import Optional, List, Dict
import asyncio
from pathlib import Path
import config
import logging

logger = logging.getLogger(__name__)
class TelegramManager:
    """Manages Telegram client sessions and operations."""
    
    def __init__(self, user_id: str, session_string: str = None):
        self.user_id = user_id
        self.session_string = session_string
        self.client: Optional[TelegramClient] = None
        self._is_connected = False
        
    async def initialize(self) -> TelegramClient:
        """Initialize the Telegram client."""
        if not self.client:
            logger.info(f"Initializing TelegramClient for user {self.user_id}")
            # Use StringSession if available, otherwise create new one
            # Note: session_string is passed from outside, which comes from DB via user_manager.get_session(..., instance_id)
            session = StringSession(self.session_string) if self.session_string else StringSession()
            
            self.client = TelegramClient(
                session,
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
        await self.ensure_connected()
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
        await self.ensure_connected()
        try:
            await self.client.sign_in(password=password)
            self._is_connected = True
            return True
        except Exception as e:
            raise Exception(f"Password verification failed: {str(e)}")
    
    async def get_session_string(self) -> str:
        """Get the current session string."""
        if not self.client:
            return None
        return StringSession.save(self.client.session)

    async def is_authenticated(self) -> bool:
        """Check if user is authenticated."""
        if not self.client:
            await self.initialize()
        
        if not self.client.is_connected():
            await self.client.connect()
        
        is_auth = await self.client.is_user_authorized()
        logger.info(f"Checking auth for {self.user_id}. Connected: {self.client.is_connected()}. Authorized: {is_auth}")
        return is_auth
    
    async def get_channels(self) -> List[Dict[str, any]]:
        """Get list of channels/groups the user has joined."""
        if not await self.is_authenticated():
            raise Exception("Not authenticated")
        
        dialogs = await self.client.get_dialogs()
        channels = []
        
        from telethon.tl.types import Channel, Chat
        
        for dialog in dialogs:
            entity = dialog.entity
            # Include channels, supergroups, and groups
            if isinstance(entity, Channel) or isinstance(entity, Chat):
                # Check if photo exists and is not empty
                has_photo = hasattr(entity, 'photo') and entity.photo is not None and not isinstance(entity.photo, ChatPhotoEmpty) and not isinstance(entity.photo, UserProfilePhotoEmpty)
                
                channels.append({
                    "id": entity.id,
                    "title": entity.title,
                    "username": getattr(entity, 'username', None),
                    "type": "channel" if getattr(entity, 'broadcast', False) else "group",
                    "member_count": getattr(entity, 'participants_count', None),
                    "has_photo": has_photo
                })
                # Debug logging
                # logger.info(f"Channel {entity.title} -> ID: {entity.id}")
        
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

    async def get_dialog_filters(self) -> List[Dict[str, any]]:
        """Get list of dialog filters (folders)."""
        if not await self.is_authenticated():
            raise Exception("Not authenticated")
        
        from telethon.tl.functions.messages import GetDialogFiltersRequest
        from telethon.tl.types import DialogFilter, DialogFilterChatlist, DialogFilterDefault, PeerChannel, PeerChat, InputPeerChannel, InputPeerChat
        from telethon import utils
        
        result = await self.client(GetDialogFiltersRequest())
        
        # Handle different return types (Vector vs messages.DialogFilters)
        if hasattr(result, 'filters'):
            filters_list = result.filters
        else:
            filters_list = result
            
        filters = []
        for f in filters_list:
            # Skip default "All Chats" filter
            if isinstance(f, DialogFilterDefault):
                continue
                
            # Helper to get title string
            title = getattr(f, 'title', 'Unknown Folder')
            if hasattr(title, 'text'):
                title = title.text
                
            if isinstance(f, DialogFilter):
                included_peers = []
                for peer in f.include_peers:
                    peer_id = utils.get_peer_id(peer)
                    
                    # Normalize ID: remove -100 prefix for channels if present
                    # We need bare IDs to match get_channels output
                    
                    raw_id = peer_id
                    
                    # Handle InputPeer types which are common in DialogFilters
                    if isinstance(peer, (PeerChannel, InputPeerChannel)):
                         if hasattr(peer, 'channel_id'):
                             raw_id = peer.channel_id
                    elif isinstance(peer, (PeerChat, InputPeerChat)):
                         if hasattr(peer, 'chat_id'):
                             raw_id = peer.chat_id
                    
                    # Fallback: if utils.get_peer_id returned a marked ID (starts with -100), try to strip it
                    # This is a safety net if the above types didn't catch it
                    if isinstance(raw_id, int) and raw_id < 0:
                        # Convert -1001234567890 to 1234567890
                        s_id = str(raw_id)
                        if s_id.startswith("-100"):
                            try:
                                raw_id = int(s_id[4:])
                            except:
                                pass
                        elif s_id.startswith("-"):
                             try:
                                raw_id = int(s_id[1:])
                             except:
                                pass
                    
                    # Debug logging
                    logger.info(f"Folder {title} peer: {peer} -> ID: {peer_id} -> Raw: {raw_id}")
                    included_peers.append(raw_id)
                
                filters.append({
                    "id": f.id,
                    "title": title,
                    "included_peers": included_peers
                })
            elif isinstance(f, DialogFilterChatlist):
                 filters.append({
                    "id": f.id,
                    "title": title,
                    "type": "chatlist",
                    "included_peers": [utils.get_peer_id(p) for p in f.include_peers]
                 })
                 
        return filters
    
    async def disconnect(self):
        """Disconnect the client."""
        if self.client and self.client.is_connected():
            await self.client.disconnect()
            self._is_connected = False


# Global registry of active clients
_active_clients: Dict[str, TelegramManager] = {}

def get_telegram_manager(user_id: str, session_string: str = None) -> TelegramManager:
    """Get or create a TelegramManager for a user."""
    if user_id not in _active_clients:
        _active_clients[user_id] = TelegramManager(user_id, session_string)
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
