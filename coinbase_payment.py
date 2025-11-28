"""
Coinbase Commerce Payment Integration Module

Handles payment processing using Coinbase Commerce.
"""

import logging
import requests
import hmac
import hashlib
import json
from typing import Dict, Any, Optional

import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CoinbasePaymentService:
    """Service for handling Coinbase Commerce payments"""
    
    API_URL = "https://api.commerce.coinbase.com"
    
    def __init__(self):
        self.api_key = getattr(config, 'COINBASE_API_KEY', None)
        self.webhook_secret = getattr(config, 'COINBASE_WEBHOOK_SECRET', None)
        
        if not self.api_key:
            logger.warning("Coinbase API key not configured")
            
    def create_charge(
        self,
        name: str,
        description: str,
        pricing_type: str = "fixed_price",
        local_price: Dict[str, str] = None,
        metadata: Dict[str, str] = None,
        redirect_url: str = None,
        cancel_url: str = None
    ) -> Dict[str, Any]:
        """
        Create a Coinbase Commerce charge
        
        Args:
            name: Product name
            description: Product description
            pricing_type: Pricing type (fixed_price or no_price)
            local_price: Dict with 'amount' and 'currency' (e.g. {'amount': '10.00', 'currency': 'USD'})
            metadata: Custom metadata
            redirect_url: URL to redirect after success
            cancel_url: URL to redirect after cancel
            
        Returns:
            dict: Charge data including hosted_url
        """
        if not self.api_key:
            raise Exception("Coinbase API key not configured")
            
        headers = {
            "Content-Type": "application/json",
            "X-CC-Api-Key": self.api_key,
            "X-CC-Version": "2018-03-22"
        }
        
        payload = {
            "name": name,
            "description": description,
            "pricing_type": pricing_type,
            "metadata": metadata or {},
        }
        
        if local_price:
            payload["local_price"] = local_price
            
        if redirect_url:
            payload["redirect_url"] = redirect_url
            
        if cancel_url:
            payload["cancel_url"] = cancel_url
            
        try:
            response = requests.post(
                f"{self.API_URL}/charges",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            data = response.json()
            return data.get("data", {})
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error creating Coinbase charge: {e}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"Response: {e.response.text}")
            raise Exception(f"Coinbase API error: {str(e)}")

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """
        Verify webhook signature
        
        Args:
            payload: Raw request body
            signature: X-CC-Webhook-Signature header value
            
        Returns:
            bool: True if valid
        """
        if not self.webhook_secret:
            logger.warning("Coinbase webhook secret not configured, skipping verification")
            return True
            
        try:
            mac = hmac.new(
                self.webhook_secret.encode('utf-8'),
                payload,
                hashlib.sha256
            )
            expected_signature = mac.hexdigest()
            return hmac.compare_digest(expected_signature, signature)
        except Exception as e:
            logger.error(f"Error verifying signature: {e}")
            return False

# Global instance
coinbase_service = CoinbasePaymentService()
