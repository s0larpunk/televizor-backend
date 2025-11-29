"""
T-Bank (Tinkoff) Payment Integration Module

Handles payment processing using T-Bank Acquiring API for Russian ruble payments.
"""

import logging
import hashlib
import httpx
from typing import Dict, Any, Optional
from datetime import datetime

import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TBankPaymentService:
    """Service for handling T-Bank payments"""
    
    def __init__(self):
        self.terminal_key = getattr(config, 'TBANK_TERMINAL_KEY', None)
        self.password = getattr(config, 'TBANK_PASSWORD', None)
        self.test_mode = getattr(config, 'TBANK_TEST_MODE', True)
        
        # API endpoints
        # T-Bank documentation recommends using securepay.tinkoff.ru for both test (with demo terminal) and production
        self.api_base = "https://securepay.tinkoff.ru/v2"
        
        if not self.terminal_key or not self.password:
            logger.warning("T-Bank credentials not configured")
    
    def generate_token(self, params: Dict[str, Any]) -> str:
        """
        Generate SHA-256 token for T-Bank API request
        
        Args:
            params: Request parameters
            
        Returns:
            str: SHA-256 hash token
        """
        # Add password to params
        token_params = params.copy()
        token_params['Password'] = self.password
        
        # Remove nested objects (dict and list) as they don't participate in token generation
        keys_to_remove = [k for k, v in token_params.items() if isinstance(v, (dict, list))]
        for k in keys_to_remove:
            del token_params[k]
        
        # Sort by key
        sorted_keys = sorted(token_params.keys())
        
        # Concatenate values
        token_string = ""
        for key in sorted_keys:
            val = token_params[key]
            if isinstance(val, bool):
                token_string += str(val).lower()
            else:
                token_string += str(val)
        
        # SHA-256 hash
        return hashlib.sha256(token_string.encode()).hexdigest()
    
    async def create_payment(
        self,
        order_id: str,
        amount: int,
        description: str,
        success_url: str,
        fail_url: str,
        customer_email: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Initialize payment and get payment URL
        
        Args:
            order_id: Unique order identifier
            amount: Amount in kopecks (e.g., 99900 for ₽999.00)
            description: Payment description
            success_url: URL to redirect after successful payment
            fail_url: URL to redirect after failed payment
            customer_email: Customer's email (optional)
            metadata: Additional metadata (optional)
            
        Returns:
            dict: Payment data including PaymentId and PaymentURL
        """
        if not self.terminal_key or not self.password:
            raise Exception("T-Bank not configured. Please set TBANK_TERMINAL_KEY and TBANK_PASSWORD")
        
        url = f"{self.api_base}/Init"
        
        # Prepare request params according to official API documentation
        params = {
            "TerminalKey": self.terminal_key,
            "Amount": amount,
            "OrderId": order_id,
            "Description": description,
        }
        
        # Add optional URLs
        if success_url:
            params["SuccessURL"] = success_url
        if fail_url:
            params["FailURL"] = fail_url
        
        # Add CustomerKey if email provided
        if customer_email:
            params["CustomerKey"] = customer_email
        
        # Prepare DATA object
        data_obj = {}
        
        # Add connection_type for widget integration
        data_obj["connection_type"] = "Widget"
        
        # Add metadata if provided
        if metadata:
            data_obj.update(metadata)
        
        # Add DATA object to params
        if data_obj:
            params["DATA"] = data_obj
        
        # Generate token (must be last step before sending)
        params["Token"] = self.generate_token(params)
        
        try:
            async with httpx.AsyncClient() as client:
                headers = {
                    "User-Agent": "Televizor/1.0",
                    "Content-Type": "application/json"
                }
                response = await client.post(url, json=params, headers=headers)
                
                try:
                    # Log the raw response for debugging
                    logger.info(f"T-Bank Init response: {response.text}")
                    result = response.json()
                except Exception:
                    logger.error(f"Failed to decode T-Bank response: {response.status_code} - {response.text}")
                    raise Exception(f"T-Bank API error ({response.status_code}): {response.text[:200]}")
                
                if not result.get("Success"):
                    error_code = result.get("ErrorCode", "Unknown")
                    error_msg = result.get("Message", "Unknown error")
                    error_details = result.get("Details", "")
                    logger.error(f"T-Bank API error: {error_code} - {error_msg} ({error_details})")
                    raise Exception(f"T-Bank error: {error_msg}")
                
                logger.info(f"T-Bank payment created: {result.get('PaymentId')}")
                
                return {
                    "payment_id": result.get("PaymentId"),
                    "payment_url": result.get("PaymentURL"),
                    "order_id": order_id,
                    "amount": amount
                }
                
        except httpx.HTTPError as e:
            logger.error(f"HTTP error creating T-Bank payment: {e}")
            raise Exception(f"Payment error: {str(e)}")
        except Exception as e:
            logger.error(f"Error creating T-Bank payment: {e}")
            raise
    
    async def check_payment_status(self, payment_id: str) -> Dict[str, Any]:
        """
        Check payment status
        
        Args:
            payment_id: Payment ID from T-Bank
            
        Returns:
            dict: Payment status information
        """
        url = f"{self.api_base}/GetState"
        
        params = {
            "TerminalKey": self.terminal_key,
            "PaymentId": payment_id,
        }
        
        params["Token"] = self.generate_token(params)
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=params)
                # Log the raw response for debugging
                logger.info(f"T-Bank GetState response: {response.text}")
                result = response.json()
                
                if not result.get("Success"):
                    logger.error(f"T-Bank GetState error: {result}")
                    return {"status": "ERROR", "error": result.get("Message")}
                
                return {
                    "status": result.get("Status"),
                    "payment_id": result.get("PaymentId"),
                    "order_id": result.get("OrderId"),
                    "amount": result.get("Amount"),
                }
                
        except Exception as e:
            logger.error(f"Error checking T-Bank payment status: {e}")
            return {"status": "ERROR", "error": str(e)}
    
    def verify_notification(self, notification: Dict[str, Any]) -> bool:
        """
        Verify notification signature from T-Bank webhook
        
        Args:
            notification: Notification data from webhook
            
        Returns:
            bool: True if signature is valid
        """
        # Extract token from notification
        received_token = notification.get("Token")
        
        if not received_token:
            logger.warning("No token in T-Bank notification")
            return False
        
        # Create params without Token for verification
        params = {k: v for k, v in notification.items() if k != "Token"}
        
        # Generate expected token
        expected_token = self.generate_token(params)
        
        is_valid = received_token == expected_token
        
        if not is_valid:
            logger.warning(f"Invalid T-Bank notification signature. Received: {received_token}, Expected: {expected_token}")
            logger.warning(f"Params used for token generation: {params}")
        
        return is_valid
    
    def handle_notification(self, notification: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle payment notification from T-Bank
        
        Args:
            notification: Notification data from webhook
            
        Returns:
            dict: Processed notification data
        """
        status = notification.get("Status")
        payment_id = notification.get("PaymentId")
        order_id = notification.get("OrderId")
        amount = notification.get("Amount")
        
        logger.info(f"T-Bank notification: {status} for payment {payment_id}")
        
        return {
            "status": status,
            "payment_id": payment_id,
            "order_id": order_id,
            "amount": amount,
            "data": notification.get("DATA", {}),
            "is_success": status in ["CONFIRMED", "AUTHORIZED"],
        }


# Global T-Bank service instance
tbank_service = TBankPaymentService()
