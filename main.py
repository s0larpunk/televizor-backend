from fastapi import FastAPI, HTTPException, Cookie, Response, Request, Header, Body, Depends
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import uuid
import models
from telegram_client import get_telegram_manager, cleanup_client
import asyncio
from contextlib import asynccontextmanager
import os
from database import engine, Base

# Import rate limiting
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Import feed worker
from feed_worker import start_feed_worker, stop_feed_worker

# Import payment service
from telegram_payment import payment_service
from stripe_payment import stripe_service
from tbank_payment import tbank_service
from tbank_payment import tbank_service
from coinbase_payment import coinbase_service
import json
import logging
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure database tables exist
    Base.metadata.create_all(bind=engine)
    
    # Startup: Start the feed worker in the background
    worker_task = asyncio.create_task(start_feed_worker())
    yield
    # Shutdown: Stop the feed worker
    await stop_feed_worker()
    worker_task.cancel()

app = FastAPI(lifespan=lifespan)

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Configure CORS - read from environment variable
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://televizor.ngrok.io"], # Add your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Apply x402 middleware
# Apply x402 middleware - REMOVED

# Simple session storage (in production, use Redis or similar)
sessions = {}

# Session expiry middleware
@app.middleware("http")
async def check_session_expiry(request: Request, call_next):
    session_id = request.cookies.get("session_id")
    if session_id and session_id in sessions:
        expires_at = sessions[session_id].get("expires_at")
        if expires_at and datetime.fromisoformat(expires_at) < datetime.now():
            del sessions[session_id]
    response = await call_next(request)
    return response

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "telegram-feed-aggregator"}

# ==================== AUTH ENDPOINTS ====================

@app.post("/api/auth/send-code")
@limiter.limit("5/minute")
async def send_code(request: Request, body: models.SendCodeRequest, response: Response):
    """Send authentication code to phone number."""
    try:
        # Create a temporary session ID
        temp_session_id = str(uuid.uuid4())
        
        manager = get_telegram_manager(body.phone)
        result = await manager.send_code(body.phone)
        
        is_authenticated = result.get("is_authenticated", False)
        
        # Store temporary session data
        sessions[temp_session_id] = {
            "phone": result["phone"],
            "phone_code_hash": result["phone_code_hash"],
            "authenticated": is_authenticated,
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(days=30)).isoformat()
        }
        
        # If already authenticated, set the cookie immediately
        if is_authenticated:
            response.set_cookie(
                key="session_id",
                value=temp_session_id,
                httponly=True,
                secure=True,  # HTTPS only in production
                samesite="lax"
            )
        
        return {
            "success": True,
            "session_id": temp_session_id,
            "phone_code_hash": result["phone_code_hash"],
            "message": "Code sent to your Telegram app" if not is_authenticated else "Already authenticated",
            "is_authenticated": is_authenticated
        }
    except Exception as e:
        logger.error(f"Error sending code: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/auth/verify-code")
@limiter.limit("5/minute")
async def verify_code(request: Request, body: models.VerifyCodeRequest, response: Response):
    """Verify the authentication code."""
    # Find session by phone and code hash
    session_id = None
    for sid, data in sessions.items():
        if (data.get("phone") == body.phone and 
            data.get("phone_code_hash") == body.phone_code_hash):
            session_id = sid
            break
    
    if not session_id:
        raise HTTPException(status_code=400, detail="Invalid session")
    
    try:
        phone = sessions[session_id]["phone"]
        manager = get_telegram_manager(phone)
        await manager.verify_code(
            body.phone,
            body.code,
            body.phone_code_hash
        )
        
        # Update session
        sessions[session_id]["authenticated"] = True
        sessions[session_id]["expires_at"] = (datetime.now() + timedelta(days=30)).isoformat()
        
        # Set cookie
        response.set_cookie(
            key="session_id",
            value=session_id,
            httponly=True,
            samesite="lax",
            max_age=30 * 24 * 60 * 60  # 30 days
        )
        
        # Ensure user exists and apply referral bonus if applicable
        try:
            # This ensures user creation
            user_manager.get_subscription_status(phone)
            
            if body.referral_code:
                user_manager.apply_referral_bonus(phone, body.referral_code)
        except Exception as e:
            logger.error(f"Error handling referral for {phone}: {e}")

        return {
            "success": True,
            "message": "Authentication successful"
        }
    except Exception as e:
        if "2FA_REQUIRED" in str(e):
            # Set cookie even for 2FA so the next request can find the session
            response.set_cookie(
                key="session_id",
                value=session_id,
                httponly=True,
                samesite="lax",
                max_age=30 * 24 * 60 * 60  # 30 days
            )
            return {
                "success": False,
                "requires_2fa": True,
                "message": "2FA password required"
            }
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/auth/verify-password")
@limiter.limit("5/minute")
async def verify_password(
    request: Request,
    body: models.VerifyPasswordRequest,
    session_id: Optional[str] = Cookie(None)
):
    """Verify 2FA password."""
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="No active session")
    
    try:
        phone = sessions[session_id]["phone"]
        manager = get_telegram_manager(phone)
        await manager.verify_password(body.password)
        
        sessions[session_id]["authenticated"] = True
        
        return {
            "success": True,
            "message": "Authentication successful"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/auth/status")
async def auth_status(session_id: Optional[str] = Cookie(None)):
    """Check authentication status."""
    if not session_id or session_id not in sessions:
        return {"authenticated": False}
    
    session = sessions[session_id]
    if not session.get("authenticated"):
        return {"authenticated": False}
    
    try:
        phone = sessions[session_id]["phone"]
        manager = get_telegram_manager(phone)
        is_auth = await manager.is_authenticated()
        return {"authenticated": is_auth}
    except:
        return {"authenticated": False}

@app.post("/api/auth/logout")
async def logout(
    response: Response,
    session_id: Optional[str] = Cookie(None)
):
    """Logout and clear session."""
    if session_id:
        phone = sessions[session_id]["phone"]
        if session_id in sessions:
            del sessions[session_id]
        
        manager = get_telegram_manager(phone)
        manager.delete_session()
        await cleanup_client(phone)
        
        response.delete_cookie("session_id")
    
    return {"success": True, "message": "Logged out successfully"}

# ==================== CHANNEL ENDPOINTS ====================

@app.get("/api/channels/list")
async def list_channels(session_id: Optional[str] = Cookie(None)):
    """List all channels/groups the user has joined."""
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not sessions[session_id].get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = sessions[session_id]["phone"]
        manager = get_telegram_manager(phone)
        channels = await manager.get_channels()
        return {"channels": channels}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/channels/{channel_id}/photo")
async def get_channel_photo(
    channel_id: int,
    session_id: Optional[str] = Cookie(None)
):
    """Get channel profile photo."""
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not sessions[session_id].get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = sessions[session_id]["phone"]
        manager = get_telegram_manager(phone)
        photo_data = await manager.get_channel_photo(channel_id)
        
        if photo_data:
            return Response(content=photo_data, media_type="image/jpeg")
        else:
            # Return a default placeholder or 404
            raise HTTPException(status_code=404, detail="Photo not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving photo: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch photo")

@app.post("/api/channels/create")
async def create_channel(
    request: models.CreateChannelRequest,
    session_id: Optional[str] = Cookie(None)
):
    """Create a new private channel for feed aggregation."""
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not sessions[session_id].get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = sessions[session_id]["phone"]
        manager = get_telegram_manager(phone)
        channel = await manager.create_channel(request.title, request.about)
        return {
            "success": True,
            "channel": channel
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== FEED CONFIGURATION ENDPOINTS ====================

import config
from feed_manager import FeedConfigManager
from user_manager import UserManager
from models import SubscriptionTier

# Initialize managers
feed_config_manager = FeedConfigManager()
user_manager = UserManager()

@app.get("/api/feeds/list")
@limiter.limit("60/minute")
async def list_feeds(request: Request, session_id: Optional[str] = Cookie(None)):
    """List all configured feeds for the user."""
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not sessions[session_id].get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = sessions[session_id]["phone"]
        logger.debug(f"list_feeds: Using phone={phone} for session_id={session_id}")
        feeds = feed_config_manager.get_user_feeds(phone)
        logger.debug(f"list_feeds: Found {len(feeds)} feeds for phone={phone}")
        
        # Add requires_premium flag to each feed
        feeds_with_meta = []
        for feed in feeds:
            feed_dict = feed.model_dump()
            
            # Check if feed has any filters
            has_filters = False
            if feed.filters:
                f = feed.filters
                has_filters = (f.keywords_include or f.keywords_exclude or 
                    f.has_image is not None or f.has_video is not None or 
                    f.max_messages_per_hour is not None or f.max_messages_per_day is not None)
            
            if feed.source_filters:
                has_filters = has_filters or len(feed.source_filters) > 0
            
            feed_dict["requires_premium"] = has_filters
            feeds_with_meta.append(feed_dict)
        
        return {"feeds": feeds_with_meta}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/feeds/create")
@limiter.limit("60/minute")
async def create_feed(
    request: Request,
    feed: models.CreateFeedRequest,
    session_id: Optional[str] = Cookie(None)
):
    """Create a new feed configuration."""
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not sessions[session_id].get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        new_feed = models.FeedConfig(
            name=feed.name,
            source_channel_ids=feed.source_channel_ids,
            destination_channel_id=feed.destination_channel_id,
            active=True,
            filters=feed.filters,
            source_filters=feed.source_filters or {}
        )
        
        phone = sessions[session_id]["phone"]
        
        # Check subscription limits
        sub_status = user_manager.get_subscription_status(phone)
        user_feeds = feed_config_manager.get_user_feeds(phone)
        
        # Determine if this feed should be active
        is_premium = sub_status.tier in [SubscriptionTier.PREMIUM, SubscriptionTier.TRIAL]
        is_first_feed = len(user_feeds) == 0
        
        # For free users: first feed is active, subsequent feeds are inactive
        feed_active = is_first_feed if not is_premium else True
        
        # Check filters for Free tier
        has_filters = False
        if feed.filters:
            f = feed.filters
            has_filters = (f.keywords_include or f.keywords_exclude or 
                f.has_image is not None or f.has_video is not None or 
                f.max_messages_per_hour is not None or f.max_messages_per_day is not None)
        
        if feed.source_filters:
            has_filters = has_filters or len(feed.source_filters) > 0
        
        if sub_status.tier == SubscriptionTier.FREE and has_filters:
            raise HTTPException(
                status_code=403,
                detail="Filters are a Premium feature. Start your free trial or upgrade to Premium to use filters."
            )
        
        new_feed.active = feed_active
        if not feed_active and not is_premium:
            new_feed.error = "Free tier limit - Upgrade to activate"
            
        logger.debug(f"create_feed: Using phone={phone} for session_id={session_id}")
        created_feed = feed_config_manager.create_feed(phone, new_feed)
        logger.debug(f"create_feed: Created feed {created_feed.id} for phone={phone}, active={feed_active}")
        
        return {
            "success": True,
            "feed": created_feed.model_dump()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/feeds/{feed_id}")
@limiter.limit("60/minute")
async def update_feed(
    request: Request,
    feed_id: str,
    update_request: models.UpdateFeedRequest,
    session_id: Optional[str] = Cookie(None)
):
    """Update a feed configuration."""
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not sessions[session_id].get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = sessions[session_id]["phone"]
        sub_status = user_manager.get_subscription_status(phone)
        is_premium = sub_status.tier in [SubscriptionTier.PREMIUM, SubscriptionTier.TRIAL]
        
        updates = update_request.model_dump(exclude_unset=True)
        
        # Check if activating a feed
        if "active" in updates and updates["active"] == True:
            # Get the feed being updated
            user_feeds = feed_config_manager.get_user_feeds(phone)
            current_feed = next((f for f in user_feeds if f.id == feed_id), None)
            
            if not current_feed:
                raise HTTPException(status_code=404, detail="Feed not found")
            
            # Check if feed has filters
            has_filters = False
            if current_feed.filters:
                f = current_feed.filters
                has_filters = (f.keywords_include or f.keywords_exclude or 
                    f.has_image is not None or f.has_video is not None or 
                    f.max_messages_per_hour is not None or f.max_messages_per_day is not None)
            
            if current_feed.source_filters:
                has_filters = has_filters or len(current_feed.source_filters) > 0
            
            # Prevent activating feeds with filters for free users
            if sub_status.tier == SubscriptionTier.FREE and has_filters:
                raise HTTPException(
                    status_code=403,
                    detail="This feed uses Premium features (filters). Start your free trial or upgrade to Premium to activate it."
                )
            
            # For free users: deactivate other feeds when activating one
            if not is_premium:
                for feed in user_feeds:
                    if feed.id != feed_id and feed.active:
                        feed_config_manager.update_feed(phone, feed.id, {
                            "active": False,
                            "error": "Paused - Active feed limit reached"
                        })
                        logger.debug(f"Deactivated feed {feed.id} for free user {phone}")
        
        # If destination channel is changing, clear error
        if "destination_channel_id" in updates:
            updates["error"] = None
            
        updated_feed = feed_config_manager.update_feed(phone, feed_id, updates)
        
        if not updated_feed:
            raise HTTPException(status_code=404, detail="Feed not found")
        
        return {
            "success": True,
            "feed": updated_feed.model_dump()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/feeds/{feed_id}")
@limiter.limit("60/minute")
async def delete_feed(
    request: Request,
    feed_id: str,
    session_id: Optional[str] = Cookie(None)
):
    """Delete a feed configuration."""
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not sessions[session_id].get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = sessions[session_id]["phone"]
        success = feed_config_manager.delete_feed(phone, feed_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Feed not found")
        
        return {"success": True, "message": "Feed deleted"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/feeds/{feed_id}/toggle")
@limiter.limit("60/minute")
async def toggle_feed(
    request: Request,
    feed_id: str,
    session_id: Optional[str] = Cookie(None)
):
    """Start or stop a feed."""
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not sessions[session_id].get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = sessions[session_id]["phone"]
        feed = feed_config_manager.get_feed(phone, feed_id)
        
        if not feed:
            raise HTTPException(status_code=404, detail="Feed not found")
        
        # Determine new status
        new_active = not feed.active
        
        if new_active:
            # Check limits if turning ON
            sub_status = user_manager.get_subscription_status(phone)
            is_premium = sub_status.tier in [SubscriptionTier.PREMIUM, SubscriptionTier.TRIAL]
            
            # Check filters
            has_filters = False
            if feed.filters:
                f = feed.filters
                has_filters = (f.keywords_include or f.keywords_exclude or 
                    f.has_image is not None or f.has_video is not None or 
                    f.max_messages_per_hour is not None or f.max_messages_per_day is not None)
            
            if feed.source_filters:
                has_filters = has_filters or len(feed.source_filters) > 0
            
            if sub_status.tier == SubscriptionTier.FREE and has_filters:
                raise HTTPException(
                    status_code=403,
                    detail="This feed uses Premium features (filters). Start your free trial or upgrade to Premium to activate it."
                )
            
            # Deactivate others if free
            if not is_premium:
                user_feeds = feed_config_manager.get_user_feeds(phone)
                for other_feed in user_feeds:
                    if other_feed.id != feed_id and other_feed.active:
                        feed_config_manager.update_feed(phone, other_feed.id, {
                            "active": False,
                            "error": "Paused - Active feed limit reached"
                        })

        # Update the feed
        updates = {"active": new_active}
        if new_active:
            updates["error"] = None
            
        updated_feed = feed_config_manager.update_feed(phone, feed_id, updates)
        
        return {
            "success": True,
            "feed": updated_feed.model_dump()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/feeds/export")
async def export_feeds(session_id: Optional[str] = Cookie(None)):
    """Export user's feed configuration as JSON."""
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not sessions[session_id].get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = sessions[session_id]["phone"]
        feeds = feed_config_manager.get_user_feeds(phone)
        
        # Convert feeds to list of dicts
        feeds_data = [feed.model_dump() for feed in feeds]
        
        return {
            "feeds": feeds_data,
            "exported_at": datetime.now().isoformat(),
            "version": "1.0"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/feeds/import")
async def import_feeds(
    request: Request,
    session_id: Optional[str] = Cookie(None)
):
    """Import feeds from JSON."""
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not sessions[session_id].get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = sessions[session_id]["phone"]
        data = await request.json()
        feeds_to_import = data.get("feeds", [])
        
        if not feeds_to_import:
            raise HTTPException(status_code=400, detail="No feeds found in import data")
            
        # Check subscription limits
        sub_status = user_manager.get_subscription_status(phone)
        current_feeds = feed_config_manager.get_user_feeds(phone)
        
        imported_count = 0
        errors = []
        
        for feed_data in feeds_to_import:
            # Check limit for each feed
            if sub_status.tier == SubscriptionTier.FREE and (len(current_feeds) + imported_count) >= 1:
                errors.append(f"Skipped '{feed_data.get('name', 'Unknown')}': Free tier limit reached")
                continue
                
            try:
                # Create feed object (ignoring ID to create new)
                feed = models.FeedConfig(
                    name=feed_data["name"],
                    source_channel_ids=feed_data["source_channel_ids"],
                    destination_channel_id=feed_data["destination_channel_id"],
                    active=feed_data.get("active", True),
                    filters=models.FilterConfig(**feed_data["filters"]) if feed_data.get("filters") else None
                )
                
                # Check filters for Free tier
                if sub_status.tier == SubscriptionTier.FREE and feed.filters:
                    f = feed.filters
                    if (f.keywords_include or f.keywords_exclude or 
                        f.has_image is not None or f.has_video is not None or 
                        f.max_messages_per_hour is not None or f.max_messages_per_day is not None):
                        errors.append(f"Skipped '{feed.name}': Filters are Premium only")
                        continue

                feed_config_manager.create_feed(phone, feed)
                imported_count += 1
            except Exception as e:
                errors.append(f"Failed to import '{feed_data.get('name', 'Unknown')}': {str(e)}")
        
        return {
            "success": True,
            "imported_count": imported_count,
            "errors": errors
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/subscription")
async def get_subscription(request: Request):
    """Get current user subscription status."""
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = sessions[session_id]["phone"]
    status = user_manager.get_subscription_status(phone)
    
    # Handle expired trial auto-downgrade with feed management
    if status.is_expired and status.tier == SubscriptionTier.TRIAL:
        user_manager.downgrade_to_free(phone, feed_config_manager)
        # Reload status after downgrade
        status = user_manager.get_subscription_status(phone)
    
    return status

@app.post("/api/subscription/activate-trial")
@limiter.limit("10/minute")
async def activate_trial(request: Request):
    """Activate 3-day trial for current user."""
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = sessions[session_id]["phone"]
    
    try:
        user_manager.start_trial(phone)
        return user_manager.get_subscription_status(phone)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/subscription/upgrade")
async def upgrade_subscription(request: Request):
    """Upgrade current user to Premium."""
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = sessions[session_id]["phone"]
    user_manager.upgrade_to_premium(phone, payment_method="manual")
    return {"status": "success", "tier": "premium"}

@app.get("/api/referral")
async def get_referral_info(request: Request):
    """Get referral info for current user."""
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = sessions[session_id]["phone"]
    info = user_manager.get_referral_info(phone)
    if not info:
        raise HTTPException(status_code=404, detail="User not found")
        
    return info

@app.post("/api/payment/create-invoice")
@limiter.limit("10/minute")
async def create_payment_invoice(request: Request):
    """
    Create and send payment invoice to user
    
    Client should call this when user clicks "Upgrade with Telegram Stars"
    """
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = sessions[session_id]["phone"]
    
    try:
        # Get user's Telegram ID from their session
        manager = get_telegram_manager(phone)
        client = await manager.initialize()
        me = await client.get_me()
        telegram_user_id = me.id
        
        # Link Telegram ID to user for future webhook lookup
        user_manager.link_telegram_id(phone, telegram_user_id)
        
        # Send invoice
        result = await payment_service.create_invoice(
            chat_id=telegram_user_id,
            title="Televizor Premium",
            description="Unlock unlimited feeds and advanced filters",
            payload="premium_monthly"
        )
        
        return {
            "success": True,
            "message": "Invoice sent to your Telegram",
            "invoice_id": result.get("result", {}).get("message_id")
        }
        
    except Exception as e:
        logger.error(f"Error creating invoice: {e}")
        logger.error(f"Error creating invoice: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/payment/webhook")
async def payment_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(None)
):
    """
    Handle Telegram payment webhook updates
    """
    try:
        body = await request.body()
        body_str = body.decode('utf-8')
        
        # Verify webhook signature
        if not payment_service.verify_webhook_signature(
            body_str,
            x_telegram_bot_api_secret_token or ""
        ):
            logger.warning("Invalid webhook signature")
            raise HTTPException(status_code=403, detail="Invalid signature")
        
        update = json.loads(body_str)
        logger.info(f"Received payment webhook: {update}")
        
        # Handle pre-checkout query
        if "pre_checkout_query" in update:
            query = update["pre_checkout_query"]
            query_id = query["id"]
            payload = query.get("invoice_payload", "")
            
            # Validate payment
            is_valid = payment_service.validate_payment(payload)
            
            if is_valid:
                # Approve payment
                await payment_service.answer_pre_checkout_query(
                    pre_checkout_query_id=query_id,
                    ok=True
                )
                logger.info(f"Pre-checkout approved for query {query_id}")
            else:
                # Reject payment
                await payment_service.answer_pre_checkout_query(
                    pre_checkout_query_id=query_id,
                    ok=False,
                    error_message="Invalid payment payload"
                )
                logger.warning(f"Pre-checkout rejected for query {query_id}")
        
        # Handle successful payment
        elif "message" in update and "successful_payment" in update["message"]:
            payment = update["message"]["successful_payment"]
            user_id = update["message"]["from"]["id"]
            
            # Extract payment details
            currency = payment["currency"]
            amount = payment["total_amount"]
            payload = payment["invoice_payload"]
            charge_id = payment["telegram_payment_charge_id"]
            
            logger.info(f"Payment successful: {amount} {currency} from user {user_id}")
            
            # Validate payment
            if currency == "XTR" and payload == "premium_monthly":
                try:
                    # Find user by Telegram ID
                    phone = user_manager.get_phone_by_telegram_id(user_id)
                    
                    if phone:
                        user_manager.upgrade_to_premium(phone, payment_method="stars")
                        logger.info(f"User {phone} (ID: {user_id}) upgraded to premium")
                        
                        await payment_service.send_message(
                            user_id, 
                            "✅ Payment received! You are now Premium."
                        )
                    else:
                        logger.error(f"Could not find user for Telegram ID {user_id}")
                    
                except Exception as e:
                    logger.error(f"Error upgrading user: {e}")

        # Handle /start command (Deep Linking)
        elif "message" in update and "text" in update["message"]:
            text = update["message"]["text"]
            chat_id = update["message"]["chat"]["id"]
            
            if text == "/start upgrade":
                # User clicked the deep link
                # We need to find the user by Telegram ID to link them (if not already linked)
                # But here we only have Telegram ID. 
                # The user MUST have logged in via the web app first, which links the ID.
                # If not linked, we can't upgrade them properly later.
                
                # Check if we know this user
                phone = user_manager.get_phone_by_telegram_id(chat_id)
                if not phone:
                    await payment_service.send_message(
                        chat_id,
                        "⚠️ Please log in to the web dashboard first to link your account."
                    )
                else:
                    await payment_service.create_invoice(
                        chat_id=chat_id,
                        title="Televizor Premium",
                        description="Unlock unlimited feeds and advanced filters",
                        payload="premium_monthly"
                    )
            
        return {"ok": True}
        
    except json.JSONDecodeError:
        logger.error("Invalid JSON in webhook")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/payment/status")
async def get_payment_status(request: Request):
    """
    Check if user has an active Premium subscription
    """
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = sessions[session_id]["phone"]
    sub_status = user_manager.get_subscription_status(phone)
    
    return {
        "tier": sub_status.tier,
        "is_premium": sub_status.tier == "premium",
        "is_expired": sub_status.is_expired
    }


# Stripe Payment Endpoints

@app.post("/api/payment/stripe-checkout")
@limiter.limit("10/minute")
async def create_stripe_checkout(request: Request):
    """
    Create Stripe Checkout session for card payment
    """
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = sessions[session_id]["phone"]
    
    try:
        # Get user's Telegram ID if linked (for metadata)
        telegram_id = None
        try:
            manager = get_telegram_manager(phone)
            client = await manager.initialize()
            me = await client.get_me()
            telegram_id = me.id
        except Exception as e:
            logger.warning(f"Could not get Telegram ID: {e}")
        
        # Create checkout session
        # Use frontend URL for redirects (localhost:3000 for dev, can be configured via env var)
        frontend_url = "http://localhost:3000"
        
        session_data = await stripe_service.create_checkout_session(
            # customer_email=f"{phone}@televizor.app",  # Removed to allow user input
            success_url=f"{frontend_url}/subscription?success=true&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{frontend_url}/subscription?canceled=true",
            metadata={
                "phone": phone,
                "telegram_id": str(telegram_id) if telegram_id else "",
            }
        )
        
        return {
            "success": True,
            "session_id": session_data["session_id"],
            "url": session_data["url"]
        }
        
    except Exception as e:
        logger.error(f"Error creating Stripe checkout: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/payment/stripe-verify")
async def verify_stripe_payment(
    request: Request,
    session_id: str = Body(..., embed=True)
):
    """
    Verify a Stripe checkout session and upgrade user if successful.
    This is a fallback/alternative to webhooks for client-side confirmation.
    """
    session_cookie = request.cookies.get("session_id")
    if not session_cookie or session_cookie not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        # Retrieve session from Stripe
        session = await stripe_service.get_checkout_session(session_id)
        
        if session.get("payment_status") == "paid":
            # Extract phone from metadata
            phone = session.get("metadata", {}).get("phone")
            
            # Verify the phone matches the current user
            current_phone = sessions[session_cookie]["phone"]
            
            if phone and phone == current_phone:
                user_manager.upgrade_to_premium(phone, payment_method="stripe")
                logger.info(f"User {phone} upgraded to Premium via Stripe verification")
                return {"success": True, "status": "paid"}
            else:
                logger.warning(f"Phone mismatch in Stripe verification: {phone} vs {current_phone}")
                raise HTTPException(status_code=403, detail="Payment does not belong to this user")
        else:
            return {"success": False, "status": session.get("payment_status")}
            
    except Exception as e:
        logger.error(f"Error verifying Stripe payment: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/payment/stripe-webhook")
async def stripe_webhook(request: Request):
    """
    Handle Stripe webhook events
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing signature")
    
    try:
        # Verify and parse event
        event = stripe_service.verify_webhook_signature(payload, sig_header)
        
        logger.info(f"Received Stripe webhook: {event['type']}")
        
        # Handle checkout completion
        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            session_data = stripe_service.handle_checkout_completed(session)
            
            # Extract phone from metadata
            phone = session.get("metadata", {}).get("phone")
            
            if phone:
                # Upgrade user to Premium
                user_manager.upgrade_to_premium(phone, payment_method="stripe")
                logger.info(f"User {phone} upgraded to Premium via Stripe")
            else:
                logger.error("No phone in Stripe session metadata")
        
        # Handle subscription cancellation
        elif event["type"] == "customer.subscription.deleted":
            subscription = event["data"]["object"]
            sub_data = stripe_service.handle_subscription_deleted(subscription)
            
            # Note: We'd need to store stripe customer_id -> phone mapping
            # For now, subscriptions are managed manually
            logger.info(f"Subscription deleted: {sub_data['subscription_id']}")
        
        return {"ok": True}
        
    except Exception as e:
        logger.error(f"Stripe webhook error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# T-Bank Payment Endpoints

@app.post("/api/payment/tbank-init")
@limiter.limit("10/minute")
async def create_tbank_payment(request: Request):
    """
    Initialize T-Bank payment for Russian users
    """
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = sessions[session_id]["phone"]
    
    try:
        # Generate unique order ID
        import uuid
        order_id = f"order_{uuid.uuid4().hex[:12]}"
        
        # Store order_id -> phone mapping for webhook
        # This will be used when the webhook is triggered
        if not hasattr(app.state, 'tbank_orders'):
            app.state.tbank_orders = {}
        app.state.tbank_orders[order_id] = phone
        
        # Amount in kopecks (₽300.00 = 30000 kopecks)
        amount = 30000
        
        # Get base URL for redirects (Frontend URL)
        # In production, this should be configured via environment variable
        base_url = "http://localhost:3000"
        
        # Create payment with dedicated success/failure pages
        payment_data = await tbank_service.create_payment(
            order_id=order_id,
            amount=amount,
            description="Televizor Premium Subscription",
            success_url=f"{base_url}/payment/tbank/success?OrderId={order_id}",
            fail_url=f"{base_url}/payment/tbank/failure",
            customer_email=f"{phone}@televizor.app",
            metadata={"phone": phone}
        )
        
        logger.info(f"Created T-Bank payment for {phone}: {order_id}")
        
        return {
            "success": True,
            "payment_id": payment_data["payment_id"],
            "payment_url": payment_data["payment_url"],
            "order_id": order_id
        }
        
    except Exception as e:
        logger.error(f"Error creating T-Bank payment: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/payment/tbank-webhook")
async def tbank_webhook(request: Request):
    """
    Handle T-Bank payment notifications
    
    T-Bank sends notifications for the following statuses:
    - AUTHORIZED: Payment authorized (reserved)
    - CONFIRMED: Payment confirmed (captured)
    - REVERSED: Payment reversed (cancelled authorization)
    - REFUNDED: Payment refunded
    - PARTIAL_REFUNDED: Partial refund
    - REJECTED: Payment rejected
    """
    try:
        # Parse notification
        notification = await request.json()
        
        status = notification.get('Status')
        payment_id = notification.get('PaymentId')
        order_id = notification.get('OrderId')
        
        logger.info(f"Received T-Bank notification: Status={status}, PaymentId={payment_id}, OrderId={order_id}")
        
        # Verify signature
        if not tbank_service.verify_notification(notification):
            logger.error(f"Invalid T-Bank notification signature for payment {payment_id}")
            # Still return OK to prevent retries
            return PlainTextResponse("OK", status_code=200)
        
        # Handle notification
        result = tbank_service.handle_notification(notification)
        
        # If payment is successful (CONFIRMED or AUTHORIZED), upgrade user
        if result["is_success"] and order_id:
            # Retrieve phone from order mapping
            if hasattr(app.state, 'tbank_orders') and order_id in app.state.tbank_orders:
                phone = app.state.tbank_orders[order_id]
                
                try:
                    # Upgrade user to premium
                    user_config = load_user_config(phone)
                    user_config["subscription"] = {
                        "tier": "premium",
                        "start_date": datetime.now().isoformat(),
                        "payment_method": "tbank",
                        "payment_id": payment_id,
                        "order_id": order_id
                    }
                    save_user_config(phone, user_config)
                    
                    logger.info(f"Upgraded user {phone} to Premium via T-Bank payment {payment_id}")
                    
                    # Clean up the mapping
                    del app.state.tbank_orders[order_id]
                    
                except Exception as e:
                    logger.error(f"Error upgrading user after T-Bank payment: {e}")
            else:
                logger.warning(f"No phone mapping found for order_id: {order_id}")
        
        # Return OK as required by T-Bank API (no tags, uppercase)
        return PlainTextResponse("OK", status_code=200)
        
    except Exception as e:
        logger.error(f"T-Bank webhook error: {e}")
        # Still return OK to prevent retries for malformed requests
        return PlainTextResponse("OK", status_code=200)


@app.get("/api/payment/tbank-status/{payment_id}")
async def get_tbank_status(payment_id: str, request: Request):
    """
    Check T-Bank payment status
    """
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        status_data = await tbank_service.check_payment_status(payment_id)
        return status_data
    except Exception as e:
        logger.error(f"Error checking T-Bank status: {e}")
        raise HTTPException(status_code=500, detail=str(e))





@app.post("/api/payment/x402/upgrade")
async def upgrade_with_x402(request: Request):
    """
    Upgrade user to Premium using x402 crypto payment.
    Protected by x402 middleware.
    """
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in sessions:
        # Note: In a real scenario, we might want to handle this better if the payment succeeded but auth failed.
        # But for now, we assume valid session.
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = sessions[session_id]["phone"]
    
    try:
        user_manager.upgrade_to_premium(phone, payment_method="crypto")
        logger.info(f"User {phone} upgraded to Premium via x402")
        return {"success": True, "message": "Upgraded to Premium"}
    except Exception as e:
        logger.error(f"Error upgrading user via x402: {e}")
        raise HTTPException(status_code=500, detail=str(e))
@app.post("/api/payment/coinbase-charge")
@limiter.limit("10/minute")
async def create_coinbase_charge(request: Request):
    """Create a Coinbase Commerce charge for Premium subscription."""
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = sessions[session_id]["phone"]
    
    try:
        # Create charge
        charge = coinbase_service.create_charge(
            name="Televizor Premium",
            description="1 Month Premium Subscription",
            pricing_type="fixed_price",
            local_price={
                "amount": "2.00",
                "currency": "EUR"
            },
            metadata={
                "phone": phone,
                "type": "premium_subscription"
            },
            redirect_url=f"{config.HOST}/subscription?success=true&provider=coinbase",
            cancel_url=f"{config.HOST}/subscription?canceled=true"
        )
        
        return {
            "success": True,
            "hosted_url": charge.get("hosted_url"),
            "code": charge.get("code")
        }
    except Exception as e:
        logger.error(f"Error creating Coinbase charge: {e}")
        raise HTTPException(status_code=500, detail="Failed to create payment charge")

@app.post("/api/webhooks/coinbase")
async def coinbase_webhook(request: Request):
    """Handle Coinbase Commerce webhooks."""
    payload = await request.body()
    signature = request.headers.get("X-CC-Webhook-Signature")
    
    if not signature:
        raise HTTPException(status_code=400, detail="Missing signature")
        
    if not coinbase_service.verify_webhook_signature(payload, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")
        
    try:
        event = await request.json()
        event_data = event.get("event", {})
        event_type = event_data.get("type")
        
        if event_type == "charge:confirmed":
            data = event_data.get("data", {})
            metadata = data.get("metadata", {})
            phone = metadata.get("phone")
            
            if phone:
                logger.info(f"Processing Coinbase payment for {phone}")
                user_manager.upgrade_to_premium(phone, payment_method="coinbase")
            else:
                logger.warning("Coinbase webhook missing phone metadata")
                
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error processing Coinbase webhook: {e}")
        raise HTTPException(status_code=500, detail="Webhook processing failed")
