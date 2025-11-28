import asyncio
import logging
from celery import Task
from celery_app import celery_app
from telegram_client import get_telegram_manager
from telethon.errors import ChatWriteForbiddenError, ChatAdminRequiredError

# Configure logging for worker
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ForwardMessageTask(Task):
    """
    Celery task that manages its own asyncio loop and Telethon clients.
    """
    _loop = None
    
    @property
    def loop(self):
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop

    def run(self, user_id: str, source_chat_id: int, destination_channel_id: int, message_id: int, delay_seconds: int = 0):
        return self.loop.run_until_complete(
            self._forward_message_async(user_id, source_chat_id, destination_channel_id, message_id, delay_seconds)
        )

    async def _forward_message_async(self, user_id: str, source_chat_id: int, destination_channel_id: int, message_id: int, delay_seconds: int):
        """
        Async implementation of forwarding logic.
        """
        try:
            # We need to get a fresh manager/client here because we are in a new process/thread
            manager = get_telegram_manager(user_id)
            
            # Ensure client is connected
            client = await manager.initialize()
            if not client.is_connected():
                await client.connect()
                
            # If delay is requested
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

            await client.forward_messages(
                entity=destination_channel_id,
                messages=message_id,
                from_peer=source_chat_id
            )
            logger.info(f"Successfully forwarded message {message_id} from {source_chat_id} to {destination_channel_id}")
            return True

        except (ChatWriteForbiddenError, ChatAdminRequiredError) as e:
            logger.error(f"Permission denied forwarding to {destination_channel_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"Error forwarding message: {e}")
            # Re-raise to trigger retry if needed (though we handle retries in decorator usually)
            raise e

# Register the task
forward_message_task = celery_app.register_task(ForwardMessageTask())
forward_message_task.name = "tasks.forward_message_task"
