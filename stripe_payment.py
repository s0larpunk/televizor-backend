"""
Stripe Payment Integration Module

Handles payment processing using Stripe Checkout for credit/debit card payments.
"""

import logging
import stripe
from typing import Dict, Any, Optional
from datetime import datetime

import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class StripePaymentService:
    """Service for handling Stripe payments"""
    
    def __init__(self):
        self.api_key = getattr(config, 'STRIPE_SECRET_KEY', None)
        self.publishable_key = getattr(config, 'STRIPE_PUBLISHABLE_KEY', None)
        self.price_id = getattr(config, 'STRIPE_PRICE_ID', None)
        self.webhook_secret = getattr(config, 'STRIPE_WEBHOOK_SECRET', None)
        
        if self.api_key:
            stripe.api_key = self.api_key
        else:
            logger.warning("Stripe API key not configured")
    
    async def create_checkout_session(
        self,
        success_url: str,
        cancel_url: str,
        customer_email: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        line_items: Optional[list] = None
    ) -> Dict[str, Any]:
        """
        Create a Stripe Checkout session
        
        Args:
            success_url: URL to redirect after successful payment
            cancel_url: URL to redirect if payment is cancelled
            customer_email: Customer's email address (optional)
            metadata: Additional metadata to attach to the session
            line_items: Custom line items with price_data (optional, overrides price_id)
            
        Returns:
            dict: Checkout session data including session ID and URL
        """
        if not self.api_key:
            raise Exception("Stripe not configured. Please set STRIPE_SECRET_KEY")
        
        # If line_items not provided, use the configured price_id
        if not line_items:
            if not self.price_id:
                raise Exception("Stripe PRICE_ID not configured and no line_items provided")
            line_items = [{
                'price': self.price_id,
                'quantity': 1,
            }]
        
        try:
            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=line_items,
                mode='subscription',
                success_url=success_url,
                cancel_url=cancel_url,
                customer_email=customer_email,
                metadata=metadata or {},
                allow_promotion_codes=True,
                billing_address_collection='auto',
            )
            
            logger.info(f"Created Stripe checkout session: {session.id}")
            
            return {
                'session_id': session.id,
                'url': session.url,
                'customer_email': customer_email
            }
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating checkout session: {e}")
            raise Exception(f"Payment error: {str(e)}")
        except Exception as e:
            logger.error(f"Error creating checkout session: {e}")
            raise
    async def get_checkout_session(self, session_id: str) -> Dict[str, Any]:
        """
        Retrieve a checkout session
        
        Args:
            session_id: The ID of the checkout session
            
        Returns:
            dict: The session object
        """
        if not self.api_key:
            raise Exception("Stripe not configured")
            
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            return session
        except Exception as e:
            logger.error(f"Error retrieving checkout session: {e}")
            raise
    
    def verify_webhook_signature(self, payload: bytes, signature: str) -> Dict[str, Any]:
        """
        Verify webhook signature and parse event
        
        Args:
            payload: Raw request body
            signature: Stripe-Signature header value
            
        Returns:
            dict: Parsed webhook event
        """
        if not self.webhook_secret:
            logger.warning("Stripe webhook secret not configured, skipping verification")
            import json
            return json.loads(payload)
        
        try:
            event = stripe.Webhook.construct_event(
                payload, signature, self.webhook_secret
            )
            return event
        except ValueError as e:
            logger.error(f"Invalid payload: {e}")
            raise Exception("Invalid payload")
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid signature: {e}")
            raise Exception("Invalid signature")
    
    def handle_checkout_completed(self, session: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle successful checkout completion
        
        Args:
            session: Checkout session data from webhook
            
        Returns:
            dict: Processed session data
        """
        customer_email = session.get('customer_email') or session.get('customer_details', {}).get('email')
        metadata = session.get('metadata', {})
        
        logger.info(f"Checkout completed for {customer_email}")
        
        return {
            'customer_email': customer_email,
            'subscription_id': session.get('subscription'),
            'customer_id': session.get('customer'),
            'metadata': metadata,
            'amount_total': session.get('amount_total'),
            'currency': session.get('currency'),
        }
    
    def handle_subscription_deleted(self, subscription: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle subscription cancellation
        
        Args:
            subscription: Subscription data from webhook
            
        Returns:
            dict: Processed subscription data
        """
        customer_id = subscription.get('customer')
        metadata = subscription.get('metadata', {})
        
        logger.info(f"Subscription deleted for customer {customer_id}")
        
        return {
            'customer_id': customer_id,
            'subscription_id': subscription.get('id'),
            'metadata': metadata,
        }


    def modify_subscription(self, subscription_id: str, new_price_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Modify a subscription to a new price with NO proration.
        
        Args:
            subscription_id: The ID of the subscription to modify
            new_price_data: The new price data (amount, interval, product name)
            
        Returns:
            dict: The updated subscription object
        """
        if not self.api_key:
            raise Exception("Stripe not configured")
            
        try:
            # Get the subscription to find the item ID
            sub = stripe.Subscription.retrieve(subscription_id)
            item_id = sub['items']['data'][0]['id']
            
            # Update the subscription
            updated_sub = stripe.Subscription.modify(
                subscription_id,
                items=[{
                    'id': item_id,
                    'price_data': {
                        'currency': 'eur',
                        'product_data': {
                            'name': new_price_data['product_name'],
                        },
                        'unit_amount': new_price_data['unit_amount'],
                        'recurring': {
                            'interval': new_price_data['interval'],
                        },
                    },
                }],
                proration_behavior='none', # CRITICAL: No proration
            )
            
            logger.info(f"Modified subscription {subscription_id} to new price")
            return updated_sub
            
        except Exception as e:
            logger.error(f"Error modifying subscription: {e}")
            raise

# Global Stripe service instance
stripe_service = StripePaymentService()
