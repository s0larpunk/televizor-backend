"""
Telegram Stars Payment Integration Module

Handles payment processing using Telegram Stars (XTR) currency.
Implements invoice generation, payment webhooks, and subscription upgrades.
"""

import logging
import hmac
import hashlib
import json
from typing import Optional, Dict, Any
from datetime import datetime
import httpx
from pydantic import BaseModel

import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Payment Models
class PaymentInvoice(BaseModel):
    """Invoice details for Telegram Stars payment"""
    title: str
    description: str
    payload: str
    currency: str = "XTR"  # Telegram Stars
    prices: list[Dict[str, int]]  # [{"label": "Premium", "amount": 100}]


class PreCheckoutQuery(BaseModel):
    """Pre-checkout query from Telegram"""
    id: str
    from_user: Dict[str, Any]
    currency: str
    total_amount: int
    invoice_payload: str


class SuccessfulPayment(BaseModel):
    """Successful payment notification"""
    currency: str
    total_amount: int
    invoice_payload: str
    telegram_payment_charge_id: str
    provider_payment_charge_id: Optional[str] = None


class TelegramPaymentService:
    """Service for handling Telegram Stars payments"""
    
    def __init__(self):
        self.bot_token = config.TELEGRAM_BOT_TOKEN
        self.api_base = f"https://api.telegram.org/bot{self.bot_token}"
        self.webhook_secret = getattr(config, 'TELEGRAM_WEBHOOK_SECRET', None)
        
        # Payment configuration
        self.premium_price_stars = int(getattr(config, 'PREMIUM_PRICE_STARS', 150))
        
    def verify_webhook_signature(self, request_data: str, signature: str) -> bool:
        """
        Verify webhook request is from Telegram using secret token
        
        Args:
            request_data: Raw request body as string
            signature: X-Telegram-Bot-Api-Secret-Token header value
            
        Returns:
            bool: True if signature is valid
        """
        if not self.webhook_secret:
            logger.warning("Webhook secret not configured, skipping verification")
            return True
            
        return signature == self.webhook_secret
    
    async def create_invoice(
        self,
        chat_id: int,
        title: str = "Televizor Premium Subscription",
        description: str = "Unlock unlimited feeds and advanced filters",
        payload: str = "premium_monthly"
    ) -> Dict[str, Any]:
        """
        Create and send payment invoice to user
        
        Args:
            chat_id: Telegram user chat ID
            title: Invoice title
            description: Invoice description
            payload: Custom payload to identify purchase
            
        Returns:
            dict: Telegram API response
            
        Reference:
            https://core.telegram.org/bots/api#sendinvoice
        """
        url = f"{self.api_base}/sendInvoice"
        
        # Prepare invoice data
        invoice_data = {
            "chat_id": chat_id,
            "title": title,
            "description": description,
            "payload": payload,
            "currency": "XTR",  # Telegram Stars
            "prices": json.dumps([{
                "label": "Premium Subscription",
                "amount": self.premium_price_stars
            }]),
            # provider_token must be omitted for XTR
            # "provider_token": "", 
            # Optional parameters
            "start_parameter": "premium_upgrade",
            "photo_url": "https://example.com/premium-icon.png",  # Optional: Add your logo
            "photo_width": 512,
            "photo_height": 512,
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=invoice_data)
                
                if response.status_code != 200:
                    logger.error(f"Telegram API Error: {response.text}")
                    
                response.raise_for_status()
                result = response.json()
                
                if result.get("ok"):
                    logger.info(f"Invoice sent successfully to chat {chat_id}")
                    return result
                else:
                    logger.error(f"Failed to send invoice: {result}")
                    raise Exception(f"Telegram API error: {result.get('description')}")
                    
        except httpx.HTTPError as e:
            logger.error(f"HTTP error sending invoice: {e}")
            raise
        except Exception as e:
            logger.error(f"Error sending invoice: {e}")
            raise
    
    async def send_message(self, chat_id: int, text: str) -> bool:
        """Send a text message to a chat."""
        url = f"{self.api_base}/sendMessage"
        data = {"chat_id": chat_id, "text": text}
        
        try:
            async with httpx.AsyncClient() as client:
                await client.post(url, json=data)
                return True
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False

    async def refund_payment(self, user_id: int, charge_id: str) -> bool:
        """Refund a Telegram Stars payment."""
        url = f"{self.api_base}/refundStarPayment"
        
        data = {
            "user_id": user_id,
            "telegram_payment_charge_id": charge_id
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=data)
                result = response.json()
                
                if not result.get("ok"):
                    logger.error(f"Refund failed: {result}")
                    return False
                    
                return True
                
        except Exception as e:
            logger.error(f"Error processing refund: {e}")
            return False

    async def answer_pre_checkout_query(
        self,
        pre_checkout_query_id: str,
        ok: bool = True,
        error_message: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Respond to pre-checkout query
        
        Args:
            pre_checkout_query_id: ID from pre_checkout_query
            ok: Whether to approve payment
            error_message: Error message if ok=False
            
        Returns:
            dict: Telegram API response
            
        Reference:
            https://core.telegram.org/bots/api#answerprecheckoutquery
        """
        url = f"{self.api_base}/answerPreCheckoutQuery"
        
        data = {
            "pre_checkout_query_id": pre_checkout_query_id,
            "ok": ok
        }
        
        if not ok and error_message:
            data["error_message"] = error_message
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=data)
                response.raise_for_status()
                result = response.json()
                
                if result.get("ok"):
                    logger.info(f"Pre-checkout query answered: {ok}")
                    return result
                else:
                    logger.error(f"Failed to answer pre-checkout: {result}")
                    raise Exception(f"Telegram API error: {result.get('description')}")
                    
        except Exception as e:
            logger.error(f"Error answering pre-checkout query: {e}")
            raise
    
    def validate_payment(self, payload: str, expected_payload: str = "premium_monthly") -> bool:
        """
        Validate payment payload matches expected value
        
        Args:
            payload: Payload from payment
            expected_payload: Expected payload value
            
        Returns:
            bool: True if valid
        """
        return payload == expected_payload
    
    def get_user_id_from_payment(self, payment_data: Dict[str, Any]) -> Optional[int]:
        """
        Extract user ID from payment update
        
        Args:
            payment_data: Payment update from webhook
            
        Returns:
            int: Telegram user ID or None
        """
        try:
            if "message" in payment_data:
                return payment_data["message"]["from"]["id"]
            elif "pre_checkout_query" in payment_data:
                return payment_data["pre_checkout_query"]["from"]["id"]
        except KeyError:
            logger.error("Could not extract user ID from payment data")
        return None


# Global payment service instance
payment_service = TelegramPaymentService()
