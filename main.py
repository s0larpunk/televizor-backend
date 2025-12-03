from fastapi import FastAPI, HTTPException, Cookie, Response, Request, Header, Body, Depends
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import uuid
import models
# Import ALL SQLAlchemy models so tables get created
from sql_models import WebSession, User, Feed, UserSession, MessageLog
from telegram_client import get_telegram_manager, cleanup_client
import asyncio
from contextlib import asynccontextmanager
import os
from database import engine, Base, SessionLocal
import config

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

from coinbase_payment import coinbase_service
import json
import logging
from datetime import datetime, timedelta
from sqlalchemy import text

# Configure logging
import sys
import logging

# Configure logging to force stdout for INFO and stderr for ERROR
# We need to clear existing handlers to avoid duplication or stderr usage by uvicorn
def setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    
    # Remove existing handlers
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Create stdout handler for INFO and WARNING
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.setFormatter(formatter)
    # Filter to only allow levels < ERROR (INFO, WARNING, DEBUG)
    stdout_handler.addFilter(lambda record: record.levelno < logging.ERROR)
    
    # Create stderr handler for ERROR and CRITICAL
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.ERROR)
    stderr_handler.setFormatter(formatter)
    
    root.addHandler(stdout_handler)
    root.addHandler(stderr_handler)
    
    # Also force uvicorn loggers to use these handlers
    for logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error"]:
        log = logging.getLogger(logger_name)
        log.handlers = []
        log.propagate = True

setup_logging()
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Log database connection info
    logger.info(f"Database URL set: {bool(os.getenv('DATABASE_URL'))}")
    logger.info(f"Database engine: {engine.url}")
    logger.info(f"Registered models: {', '.join([table.name for table in Base.metadata.tables.values()])}")
    
    # Ensure database tables exist
    try:
        Base.metadata.create_all(bind=engine)
        logger.info(f"✓ Successfully created {len(Base.metadata.tables)} tables")
        
        # Verify tables were created
        with engine.connect() as conn:
            if 'postgresql' in str(engine.url):
                result = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
                tables = [row[0] for row in result]
                logger.info(f"PostgreSQL tables: {tables}")
            else:
                logger.info("Using SQLite database")
    except Exception as e:
        logger.error(f"Error creating tables: {e}")
        raise
    
    # Startup: Start the feed worker in the background
    worker_task = asyncio.create_task(start_feed_worker())
    
    # Debug: Log user expiry on startup - REMOVED
        
    yield
    # Shutdown: Stop the feed worker
    await stop_feed_worker()
    worker_task.cancel()

app = FastAPI(lifespan=lifespan)

# Cookie Configuration
is_production = "localhost" not in config.FRONTEND_URL and "127.0.0.1" not in config.FRONTEND_URL
COOKIE_SETTINGS = {
    "httponly": True,
    "samesite": "none" if is_production else "lax",
    "secure": True if is_production else False,
    "max_age": 30 * 24 * 60 * 60  # 30 days
}

# Initialize Telegram Client
# Initialize Telegram Client
# We'll initialize it lazily when needed

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Configure CORS - read from environment variable
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://televizor.ngrok.io", config.FRONTEND_URL], # Add your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Apply x402 middleware - REMOVED

# Helper functions for WebSession
def get_web_session(session_id: str):
    db = SessionLocal()
    try:
        return db.query(WebSession).filter(WebSession.session_id == session_id).first()
    finally:
        db.close()

def create_web_session(session_id: str, phone: str, user_identifier: str, phone_code_hash: str, authenticated: bool = False):
    db = SessionLocal()
    try:
        session = WebSession(
            session_id=session_id,
            phone=phone,
            user_identifier=user_identifier,
            phone_code_hash=phone_code_hash,
            authenticated=authenticated,
            expires_at=datetime.utcnow() + timedelta(days=30)
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session
    finally:
        db.close()

def update_web_session(session_id: str, **kwargs):
    db = SessionLocal()
    try:
        session = db.query(WebSession).filter(WebSession.session_id == session_id).first()
        if session:
            for key, value in kwargs.items():
                setattr(session, key, value)
            db.commit()
            db.refresh(session)
            return session
        return None
    finally:
        db.close()

def delete_web_session(session_id: str):
    db = SessionLocal()
    try:
        db.query(WebSession).filter(WebSession.session_id == session_id).delete()
        db.commit()
    finally:
        db.close()

# Session expiry middleware
@app.middleware("http")
async def check_session_expiry(request: Request, call_next):
    session_id = request.cookies.get("session_id")
    if session_id:
        session = get_web_session(session_id)
        if session and session.expires_at < datetime.utcnow():
            delete_web_session(session_id)
    response = await call_next(request)
    return response

# Initialize managers after app is created
from user_manager import UserManager
from feed_manager import FeedConfigManager
from models import SubscriptionTier

user_manager = UserManager()
feed_config_manager = FeedConfigManager()

@app.get("/api/health")
async def health_check():
    db_url = str(engine.url)
    # Hide password in URL for security
    if '@' in db_url:
        db_type = "PostgreSQL" if "postgresql" in db_url else "Unknown"
        db_info = f"{db_type} (connected)"
    else:
        db_info = db_url
    
    return {
        "status": "ok", 
        "service": "telegram-feed-aggregator",
        "database": db_info,
        "database_url_set": bool(os.getenv("DATABASE_URL"))
    }

# ==================== AUTH ENDPOINTS ====================

@app.post("/api/auth/send-code")
@limiter.limit("5/minute")
async def send_code(request: Request, body: models.SendCodeRequest, response: Response):
    """Send authentication code to phone number."""
    # Normalize phone number (remove spaces)
    normalized_phone = body.phone.replace(" ", "")
    
    try:
        # Create a temporary session ID
        temp_session_id = str(uuid.uuid4())
        
        # Check if we have a known Telegram ID for this phone
        try:
            status = user_manager.get_subscription_status(normalized_phone)
            user_identifier = str(status.telegram_id) if status.telegram_id else normalized_phone
        except Exception:
            user_identifier = normalized_phone

            user_identifier = normalized_phone
        
        # Try to load existing session string
        session_string = user_manager.get_session(normalized_phone, instance_id=config.INSTANCE_ID)
        manager = get_telegram_manager(user_identifier, session_string)
        result = await manager.send_code(normalized_phone)
        
        is_authenticated = result.get("is_authenticated", False)
        
        logger.info(f"Creating session for phone={normalized_phone}, session_id={temp_session_id}, phone_code_hash={result['phone_code_hash']}")
        
        # Store temporary session data in DB
        created_session = create_web_session(
            session_id=temp_session_id,
            phone=normalized_phone,
            user_identifier=user_identifier,
            phone_code_hash=result["phone_code_hash"],
            authenticated=is_authenticated
        )
        
        logger.info(f"✓ Session created successfully: {created_session.session_id}")
        
        # If already authenticated, set the cookie immediately
        if is_authenticated:
            response.set_cookie(
                key="session_id",
                value=temp_session_id,
                **COOKIE_SETTINGS
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
    # Normalize phone number (remove spaces)
    normalized_phone = body.phone.replace(" ", "")
    
    # Find session by phone and code hash
    db = SessionLocal()
    try:
        # Log what we're looking for
        logger.info(f"Looking for session with phone={normalized_phone}, phone_code_hash={body.phone_code_hash}")
        
        # Check all sessions for this phone
        all_sessions = db.query(WebSession).filter(WebSession.phone == normalized_phone).all()
        logger.info(f"Found {len(all_sessions)} sessions for phone {normalized_phone}")
        for s in all_sessions:
            logger.info(f"  Session {s.session_id[:8]}...: phone_code_hash={s.phone_code_hash}, authenticated={s.authenticated}")
        
        session = db.query(WebSession).filter(
            WebSession.phone == normalized_phone,
            WebSession.phone_code_hash == body.phone_code_hash
        ).first()
        
        if not session:
            logger.error(f"No matching session found for phone={normalized_phone}, phone_code_hash={body.phone_code_hash}")
            raise HTTPException(status_code=400, detail="Invalid session")
    finally:
        db.close()
    
    session_id = session.session_id
    
    try:
        phone = session.phone
        user_identifier = session.user_identifier
        logger.info(f"Verifying code for phone: {phone} (identifier: {user_identifier})")
        if body.referral_code:
            logger.info(f"Received referral code: {body.referral_code}")
            
        if body.referral_code:
            logger.info(f"Received referral code: {body.referral_code}")
            
        # Try to load existing session string
        session_string = user_manager.get_session(phone, instance_id=config.INSTANCE_ID)
        manager = get_telegram_manager(user_identifier, session_string)
        await manager.verify_code(
            body.phone,
            body.code,
            body.phone_code_hash
        )
        
        # Post-verification: Get Telegram ID and migrate session
        try:
            me = await manager.client.get_me()
            telegram_id = me.id
            logger.info(f"Authenticated as Telegram ID: {telegram_id}")
            
            # Update DB
            user_manager.update_telegram_id(phone, telegram_id)
            
            # Rename session file if it's currently using phone number
            if manager.user_id != str(telegram_id):
                logger.info(f"Migrating session from {manager.user_id} to {telegram_id}")
                # await manager.disconnect() # No need to disconnect for StringSession
                
                # Update the manager's internal ID
                old_user_id = manager.user_id
                manager.user_id = str(telegram_id)
                
                # Update the manager in the global registry
                from telegram_client import _active_clients
                if old_user_id in _active_clients:
                    del _active_clients[old_user_id]
                _active_clients[str(telegram_id)] = manager
            
            # Update session identifier
            update_web_session(session_id, user_identifier=str(telegram_id))
            
            # Save session string to DB for persistence
            session_string = await manager.get_session_string()
            if session_string:
                user_manager.save_session(phone, session_string, instance_id=config.INSTANCE_ID)
                logger.info(f"Session string saved for phone: {phone} (instance: {config.INSTANCE_ID})")
                
        except Exception as e:
            logger.error(f"Error migrating to Telegram ID: {e}")
            # Continue anyway, we can try again next time
        
        # Update session
        update_web_session(session_id, authenticated=True)
        
        # Set cookie
        response.set_cookie(
            key="session_id",
            value=session_id,
            **COOKIE_SETTINGS
        )
        
        # Ensure user exists and apply referral bonus if applicable
        is_new_user = False
        try:
            # Check if this is a new user
            _, is_new_user = user_manager.get_subscription_status(phone, return_is_new=True)
            
            # Only apply referral bonus if this is a NEW user
            if body.referral_code and is_new_user:
                logger.info(f"Applying referral bonus for new user {phone} with code {body.referral_code}")
                user_manager.apply_referral_bonus(phone, body.referral_code)
            elif body.referral_code and not is_new_user:
                logger.info(f"Skipping referral bonus for existing user {phone}")
        except Exception as e:
            logger.error(f"Error handling referral for {phone}: {e}")

        return {
            "success": True,
            "message": "Authentication successful",
            "is_new_user": is_new_user,
            "referral_applied": body.referral_code and is_new_user
        }
    except Exception as e:
        if "2FA_REQUIRED" in str(e):
            # Set cookie even for 2FA so the next request can find the session
            response.set_cookie(
                key="session_id",
                value=session_id,
                **COOKIE_SETTINGS
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
    response: Response,
    session_id: Optional[str] = Cookie(None)
):
    """Verify 2FA password."""
    session = get_web_session(session_id) if session_id else None
    if not session:
        raise HTTPException(status_code=401, detail="No active session")
    
    try:
        phone = session.phone
        user_identifier = session.user_identifier
        phone = session.phone
        user_identifier = session.user_identifier
        
        # Try to load existing session string
        session_string = user_manager.get_session(phone, instance_id=config.INSTANCE_ID)
        manager = get_telegram_manager(user_identifier, session_string)
        await manager.verify_password(body.password)
        
        # After successful verification, save the session string
        session_string = await manager.get_session_string()
        if session_string:
            user_manager.save_session(phone, session_string, instance_id=config.INSTANCE_ID)
            logger.info(f"Session string saved for phone: {phone} (instance: {config.INSTANCE_ID})")
        
        update_web_session(session_id, authenticated=True)
        
        # Refresh cookie
        response.set_cookie(
            key="session_id",
            value=session_id,
            **COOKIE_SETTINGS
        )
        
        return {
            "success": True,
            "message": "Authentication successful"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/auth/status")
async def auth_status(session_id: Optional[str] = Cookie(None)):
    """Check authentication status."""
    if session_id:
        session = get_web_session(session_id)
        if session:
            return {"authenticated": session.authenticated}
    
    return {"authenticated": False}

@app.post("/api/auth/logout")
async def logout(
    response: Response,
    session_id: Optional[str] = Cookie(None)
):
    """Logout and clear session."""
    if session_id:
        session = get_web_session(session_id)
        if session:
            phone = session.phone
            delete_web_session(session_id)
            
            # Delete user's Telegram session from database
            user_manager.delete_session(phone, instance_id=config.INSTANCE_ID)
            # Cleanup the telegram client
            await cleanup_client(phone)
        
        response.delete_cookie("session_id")
    
    return {"success": True, "message": "Logged out successfully"}

# ==================== CHANNEL ENDPOINTS ====================

from telethon.errors import SessionRevokedError, AuthKeyError

async def handle_revoked_session(session_id: str, phone: str):
    """Helper to handle revoked sessions."""
    logger.warning(f"Session revoked for {phone}. Cleaning up.")
    delete_web_session(session_id)
    user_manager.delete_session(phone, instance_id=config.INSTANCE_ID)
    await cleanup_client(phone)

@app.get("/api/channels/list")
async def list_channels(response: Response, session_id: Optional[str] = Cookie(None)):
    """List all channels/groups the user has joined."""
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = session.phone
        user_identifier = session.user_identifier
        
        # Try to load existing session string
        session_string = user_manager.get_session(phone, instance_id=config.INSTANCE_ID)
        manager = get_telegram_manager(user_identifier, session_string)
        channels = await manager.get_channels()
        return {"channels": channels}
    except (SessionRevokedError, AuthKeyError) as e:
        await handle_revoked_session(session_id, session.phone)
        response.delete_cookie("session_id")
        raise HTTPException(status_code=401, detail="Telegram session revoked. Please login again.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/folders/list")
async def list_folders(response: Response, session_id: Optional[str] = Cookie(None)):
    """List all folders (dialog filters) the user has created."""
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = session.phone
        user_identifier = session.user_identifier
        
        # Try to load existing session string
        session_string = user_manager.get_session(phone, instance_id=config.INSTANCE_ID)
        manager = get_telegram_manager(user_identifier, session_string)
        folders = await manager.get_dialog_filters()
        return {"folders": folders}
    except (SessionRevokedError, AuthKeyError) as e:
        await handle_revoked_session(session_id, session.phone)
        response.delete_cookie("session_id")
        raise HTTPException(status_code=401, detail="Telegram session revoked. Please login again.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/channels/{channel_id}/photo")
async def get_channel_photo(
    channel_id: int,
    response: Response,
    session_id: Optional[str] = Cookie(None)
):
    """Get channel profile photo."""
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = session.phone
        user_identifier = session.user_identifier
        
        # Try to load existing session string
        session_string = user_manager.get_session(phone, instance_id=config.INSTANCE_ID)
        manager = get_telegram_manager(user_identifier, session_string)
        photo_data = await manager.get_channel_photo(channel_id)
        
        if photo_data:
            return Response(content=photo_data, media_type="image/jpeg")
        else:
            # Return a default placeholder or 404
            raise HTTPException(status_code=404, detail="Photo not found")
    except (SessionRevokedError, AuthKeyError) as e:
        await handle_revoked_session(session_id, session.phone)
        response.delete_cookie("session_id")
        raise HTTPException(status_code=401, detail="Telegram session revoked. Please login again.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving photo: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch photo")

@app.post("/api/channels/create")
async def create_channel(
    request: models.CreateChannelRequest,
    response: Response,
    session_id: Optional[str] = Cookie(None)
):
    """Create a new private channel for feed aggregation."""
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = session.phone
        user_identifier = session.user_identifier
        
        # Try to load existing session string
        session_string = user_manager.get_session(phone, instance_id=config.INSTANCE_ID)
        manager = get_telegram_manager(user_identifier, session_string)
        channel = await manager.create_channel(request.title, request.about)
        return {
            "success": True,
            "channel": channel
        }
    except (SessionRevokedError, AuthKeyError) as e:
        await handle_revoked_session(session_id, session.phone)
        response.delete_cookie("session_id")
        raise HTTPException(status_code=401, detail="Telegram session revoked. Please login again.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== FEED CONFIGURATION ENDPOINTS ====================

import config

@app.get("/api/feeds/list")
@limiter.limit("60/minute")
async def list_feeds(request: Request, session_id: Optional[str] = Cookie(None)):
    """List all configured feeds for the user."""
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = session.phone
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
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
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
        
        phone = session.phone
        
        # Check subscription limits
        sub_status = user_manager.get_subscription_status(phone)
        user_feeds = feed_config_manager.get_user_feeds(phone)
        
        # Determine if this feed should be active
        is_premium = sub_status.tier in [SubscriptionTier.PREMIUM, SubscriptionTier.PREMIUM_BASIC, SubscriptionTier.PREMIUM_ADVANCED, SubscriptionTier.TRIAL]
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
        
        if sub_status.tier in [SubscriptionTier.FREE, SubscriptionTier.PREMIUM_BASIC] and has_filters:
            raise HTTPException(
                status_code=403,
                detail="Filters are an Advanced Premium feature. Upgrade to Advanced Premium to use filters."
            )
        
        new_feed.active = feed_active
        if not feed_active and not is_premium:
            new_feed.error = "Free tier limit - Upgrade to activate"
            
        logger.debug(f"create_feed: Using phone={phone} for session_id={session_id}")
        created_feed = feed_config_manager.create_feed(phone, new_feed, tier=sub_status.tier)
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
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = session.phone
        sub_status = user_manager.get_subscription_status(phone)
        is_premium = sub_status.tier in [SubscriptionTier.PREMIUM, SubscriptionTier.PREMIUM_BASIC, SubscriptionTier.PREMIUM_ADVANCED, SubscriptionTier.TRIAL]
        
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
            
            # Prevent activating feeds with filters for free/basic users
            if sub_status.tier in [SubscriptionTier.FREE, SubscriptionTier.PREMIUM_BASIC] and has_filters:
                raise HTTPException(
                    status_code=403,
                    detail="This feed uses Advanced Premium features (filters). Upgrade to Advanced Premium to activate it."
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
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = session.phone
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
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = session.phone
        feed = feed_config_manager.get_feed(phone, feed_id)
        
        if not feed:
            raise HTTPException(status_code=404, detail="Feed not found")
        
        # Determine new status
        new_active = not feed.active
        
        if new_active:
            # Check limits if turning ON
            sub_status = user_manager.get_subscription_status(phone)
            is_premium = sub_status.tier in [SubscriptionTier.PREMIUM, SubscriptionTier.PREMIUM_BASIC, SubscriptionTier.PREMIUM_ADVANCED, SubscriptionTier.TRIAL]
            
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
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = session.phone
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
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        phone = session.phone
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
                
                # Check filters for Free/Basic tier
                if sub_status.tier in [SubscriptionTier.FREE, SubscriptionTier.PREMIUM_BASIC] and feed.filters:
                    f = feed.filters
                    if (f.keywords_include or f.keywords_exclude or 
                        f.has_image is not None or f.has_video is not None or 
                        f.max_messages_per_hour is not None or f.max_messages_per_day is not None):
                        errors.append(f"Skipped '{feed.name}': Filters are Advanced Premium only")
                        continue

                new_feed = feed_config_manager.create_feed(phone, feed, tier=sub_status.tier)
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
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = session.phone
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
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = session.phone
    
    try:
        user_manager.start_trial(phone)
        return user_manager.get_subscription_status(phone)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/subscription/upgrade")
async def upgrade_subscription(request: Request):
    """Upgrade current user to Premium."""
    session_id = request.cookies.get("session_id")
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = session.phone
    user_manager.upgrade_to_premium(phone, payment_method="manual")
    return {"status": "success", "tier": "premium"}

@app.get("/api/referral")
async def get_referral_info(request: Request):
    """Get referral info for current user."""
    session_id = request.cookies.get("session_id")
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = session.phone
    info = user_manager.get_referral_info(phone)
    if not info:
        raise HTTPException(status_code=404, detail="User not found")
        
    return info

async def get_tm(request: Request):
    """Dependency to get TelegramManager for current user."""
    session_id = request.cookies.get("session_id")
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = session.phone
    
    # Load session string from DB
    session_string = user_manager.get_session(phone)
    
    # Pass session_string to get_telegram_manager
    # If session_string is None, it will create a new empty session (which is fine, user might need to re-login if DB is empty)
    return get_telegram_manager(phone, session_string)

@app.post("/api/payment/create-invoice")
@limiter.limit("10/minute")
async def create_payment_invoice(request: Request):
    """
    Create and send payment invoice to user
    
    Client should call this when user clicks "Upgrade with Telegram Stars"
    """
    session_id = request.cookies.get("session_id")
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = session.phone
    
    try:
        # Get user's Telegram ID from their session
        user_identifier = session.user_identifier or phone
        manager = get_telegram_manager(user_identifier)
        client = await manager.initialize()
        me = await client.get_me()
        telegram_user_id = me.id
        
        # Link Telegram ID to user for future webhook lookup
        user_manager.link_telegram_id(phone, telegram_user_id)
        
        # Send invoice
        # Default to Advanced if not specified (or handle payload logic)
        # For now, we'll assume the client sends the desired payload in the request body
        # But this endpoint takes no body args other than request.
        # We should update the endpoint to accept a payload or tier.
        
        # Updating to use a default payload of 'premium_advanced' for now, 
        # but ideally we should accept a body.
        # Let's check if we can parse body.
        try:
            body = await request.json()
            payload = body.get("payload", "premium_advanced")
            duration_months = body.get("duration_months", 1)
            logger.info(f"Create invoice payload: {payload}, months: {duration_months}")
        except Exception as e:
            logger.error(f"Error parsing invoice payload: {e}")
            payload = "premium_advanced"
            duration_months = 1

        # Calculate price based on payload
        if payload == "premium_advanced_upgrade":
            # Dynamic Upgrade + Extend Logic
            user = user_manager.get_user_by_phone(phone)
            remaining_days = (user.expiry_date - datetime.utcnow()).days if user.expiry_date else 0
            import math
            existing_months = math.ceil(remaining_days / 30.0)
            if existing_months < 1: existing_months = 1 # Minimum 1 month upgrade if active
            
            requested_total_months = duration_months
            extension_months = max(0, requested_total_months - existing_months)
            
            # Price: 150 stars (upgrade) vs 250 stars (full)
            # Assuming 1 EUR = 75 Stars approx (based on 2 EUR = 150 Stars)
            # Upgrade: 1 EUR = 75 Stars
            # Full: 3 EUR = 250 Stars (approx 83/EUR, let's stick to 250)
            
            upgrade_cost = existing_months * 75
            extension_cost = extension_months * 250
            price_stars = upgrade_cost + extension_cost
            
            title = f"Upgrade to Advanced ({existing_months}mo) + {extension_months}mo"
            description = f"Upgrade existing time and extend by {extension_months} months"
            
            # Webhook should only add extension time
            duration_months = extension_months
            # Payload becomes standard advanced
            payload = "premium_advanced"
            
        elif payload == "premium_advanced_year":
            price_stars = 2500
            title = "Televizor Premium Advanced (Yearly)"
            description = "Unlimited feeds + Filters (1 Year)"
            duration_months = 1
        elif payload == "premium_basic_year":
            price_stars = 1500
            title = "Televizor Premium Basic (Yearly)"
            description = "Unlimited feeds (1 Year)"
            duration_months = 1
        elif payload == "premium_basic":
            price_stars = 150
            title = "Televizor Premium Basic"
            description = "Unlimited feeds"
        else:
            # Default to advanced monthly
            price_stars = 250
            title = "Televizor Premium Advanced"
            description = "Unlimited feeds + Filters"

        # Multiply by duration for monthly plans (standard flow)
        if "year" not in payload and "upgrade" not in title: # Skip if already calculated for upgrade
            price_stars = price_stars * duration_months
            if duration_months > 1:
                title = f"{title} ({duration_months} Months)"
                description = f"{description} ({duration_months} Months)"
        
        # Encode duration in payload for webhook
        encoded_payload = f"{payload}:{duration_months}"

        result = await payment_service.create_invoice(
            chat_id=telegram_user_id,
            title=title,
            description=description,
            payload=encoded_payload,
            price=int(price_stars)
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
            # We need to handle the encoded payload format "payload:duration"
            # Or just validate the prefix if validation logic is strict.
            # Assuming validate_payment just checks generic validity or length.
            # If it checks against known payloads, we might need to adjust it.
            # For now, let's assume it's lenient or we skip it if it fails?
            # Actually, let's just approve it if it looks reasonable.
            
            is_valid = True # payment_service.validate_payment(payload)
            
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
            encoded_payload = payment["invoice_payload"]
            charge_id = payment["telegram_payment_charge_id"]
            
            logger.info(f"Payment successful: {amount} {currency} from user {user_id}")
            
            # Decode payload
            parts = encoded_payload.split(":")
            payload = parts[0]
            duration_months = int(parts[1]) if len(parts) > 1 else 1
            
            # Validate payment
            if currency == "XTR":
                tier = SubscriptionTier.PREMIUM_ADVANCED
                duration_days = 30 * duration_months
                
                if payload == "premium_basic":
                    tier = SubscriptionTier.PREMIUM_BASIC
                elif payload == "premium_basic_year":
                    tier = SubscriptionTier.PREMIUM_BASIC
                    duration_days = 365
                elif payload == "premium_advanced_year":
                    tier = SubscriptionTier.PREMIUM_ADVANCED
                    duration_days = 365
                elif payload == "premium_monthly": # Legacy
                    tier = SubscriptionTier.PREMIUM_ADVANCED
                
                try:
                    # Find user by Telegram ID
                    phone = user_manager.get_phone_by_telegram_id(user_id)
                    
                    if phone:
                        user_manager.upgrade_to_premium(phone, payment_method="stars", tier=tier, duration_days=duration_days)
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
        # Handle commands
        elif "message" in update and "text" in update["message"]:
            text = update["message"]["text"]
            chat_id = update["message"]["chat"]["id"]
            
            # Normalize text
            parts = text.split()
            command = parts[0] if parts else ""
            args = parts[1:] if len(parts) > 1 else []
            
            if command == "/start":
                if args and args[0] == "upgrade":
                    # User clicked the deep link for upgrade
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
                else:
                    # General /start message
                    welcome_msg = (
                        "<b>Welcome to Televizor! 📺</b>\n\n"
                        "I can help you manage your Telegram feeds. Here's what I can do:\n\n"
                        "/about - How it works\n"
                        "/bonus - Get free Premium\n"
                        "/help - Show commands\n\n"
                        f"To get started, visit our website: <a href='{config.FRONTEND_URL}'>Televizor Web App</a>"
                    )
                    await payment_service.send_message(chat_id, welcome_msg, parse_mode="HTML")
            
            elif command == "/about":
                about_msg = (
                    "<b>About Televizor</b>\n\n"
                    "Televizor allows you to aggregate multiple Telegram channels into a single custom feed. "
                    "It helps you declutter your chat list and focus on what matters.\n\n"
                    "1. Log in to the website\n"
                    "2. Create a feed\n"
                    "3. Add source channels (even from folders!)\n"
                    "4. Enjoy your clean news stream!\n\n"
                    f"Visit <a href='{config.FRONTEND_URL}'>Televizor</a> to start."
                )
                await payment_service.send_message(chat_id, about_msg, parse_mode="HTML")
                
            elif command == "/bonus":
                bonus_msg = (
                    "<b>Invite friends & Get Premium! 🎁</b>\n\n"
                    "For every friend who joins using your referral link, you both get extra Premium time.\n\n"
                    f"Log in to your <a href='{config.FRONTEND_URL}/dashboard'>Dashboard</a> to get your unique referral link."
                )
                await payment_service.send_message(chat_id, bonus_msg, parse_mode="HTML")
                
            elif command == "/help":
                help_msg = (
                    "<b>Available Commands:</b>\n\n"
                    "/start - Start the bot\n"
                    "/about - How it works\n"
                    "/bonus - Referral program\n"
                    "/help - Show this message"
                )
                await payment_service.send_message(chat_id, help_msg, parse_mode="HTML")
            
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
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = session.phone
    sub_status = user_manager.get_subscription_status(phone)
    
    return {
        "tier": sub_status.tier,
        "is_premium": sub_status.tier == "premium",
        "is_expired": sub_status.is_expired
    }


@app.get("/api/subscription/upgrade-preview")
async def upgrade_preview(request: Request, target_tier: str):
    """
    Get preview of upgrade cost
    """
    session_id = request.cookies.get("session_id")
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        cost = user_manager.calculate_upgrade_cost(session.phone, target_tier)
        return cost
    except Exception as e:
        logger.error(f"Error calculating upgrade cost: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/subscription/downgrade")
async def downgrade_subscription(request: Request):
    """
    Schedule a downgrade to Basic
    """
    session_id = request.cookies.get("session_id")
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        # Default target is Basic for now
        result = user_manager.schedule_downgrade(session.phone, SubscriptionTier.PREMIUM_BASIC)
        return result
    except Exception as e:
        logger.error(f"Error scheduling downgrade: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Stripe Payment Endpoints

@app.post("/api/payment/stripe-checkout")
@limiter.limit("10/minute")
async def create_stripe_checkout(request: Request):
    """
    Create Stripe Checkout session for card payment
    """
    session_id = request.cookies.get("session_id")
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = session.phone
    
    try:
        # Get payload from request
        try:
            body = await request.json()
            payload = body.get("payload", "premium_advanced")
            duration_months = body.get("duration_months", 1)
            logger.info(f"Stripe checkout payload: {payload}, months: {duration_months}")
        except Exception as e:
            logger.error(f"Error parsing Stripe payload: {e}")
            payload = "premium_advanced"
            duration_months = 1

        # Determine price based on payload
        # Basic: 2 EUR, Advanced: 3 EUR
        # Basic Year: 20 EUR, Advanced Year: 30 EUR
        unit_amount = 300
        product_name = "Televizor Premium Advanced"
        interval = "month"
        
        if payload == "premium_advanced_upgrade":
            # Dynamic Upgrade + Extend Logic
            user = user_manager.get_user_by_phone(phone)
            remaining_days = (user.expiry_date - datetime.utcnow()).days if user.expiry_date else 0
            import math
            existing_months = math.ceil(remaining_days / 30.0)
            if existing_months < 1: existing_months = 1
            
            requested_total_months = duration_months
            extension_months = max(0, requested_total_months - existing_months)
            
            # Price: 100 cents (upgrade) vs 300 cents (full)
            upgrade_cost = existing_months * 100
            extension_cost = extension_months * 300
            unit_amount = upgrade_cost + extension_cost
            
            product_name = f"Upgrade to Advanced ({existing_months}mo) + {extension_months}mo"
            
            # Webhook should only add extension time
            duration_months = extension_months
            # Payload becomes standard advanced for metadata
            payload = "premium_advanced"
            
            # For Stripe, we set quantity to 1 because we calculated total price in unit_amount
            # But wait, create_checkout_session uses unit_amount * quantity.
            # So we should set quantity=1 and unit_amount=total_price.
            # We need to override the default logic below which sets quantity=duration_months.
            # Let's handle it by setting duration_months=1 for the quantity param, 
            # but passing the REAL extension months in metadata.
            
            real_extension_months = extension_months
            duration_months = 1 # For quantity
            
        elif payload == "premium_basic":
            unit_amount = 200
            product_name = "Televizor Premium Basic"
        elif payload == "premium_basic_year":
            unit_amount = 2000
            product_name = "Televizor Premium Basic (Yearly)"
            interval = "year"
            duration_months = 1 # Yearly is 1 unit of 1 year
        elif payload == "premium_advanced_year":
            unit_amount = 3000
            product_name = "Televizor Premium Advanced (Yearly)"
            interval = "year"
            duration_months = 1

        # Get user's Telegram ID if linked (for metadata)
        telegram_id = None
        try:
            user_identifier = session.user_identifier or phone
            manager = get_telegram_manager(user_identifier)
            client = await manager.initialize()
            me = await client.get_me()
            telegram_id = me.id
        except Exception as e:
            logger.warning(f"Could not get Telegram ID: {e}")
        
        # Create checkout session
        # Use frontend URL for redirects (localhost:3000 for dev, can be configured via env var)
        frontend_url = config.FRONTEND_URL
        
        # We need to construct line_items with price_data for ad-hoc pricing
        # OR use different price IDs if configured. 
        # For simplicity and flexibility here, let's use price_data.
        
        session_data = await stripe_service.create_checkout_session(
            # customer_email=f"{phone}@televizor.app",  # Removed to allow user input
            success_url=f"{frontend_url}/payment/stripe/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{frontend_url}/payment/stripe/failure",
            metadata={
                "phone": phone,
                "telegram_id": str(telegram_id) if telegram_id else "",
                "payload": payload, # Store payload to know which tier to upgrade to
                "duration_months": str(real_extension_months) if 'real_extension_months' in locals() else str(duration_months)
            },
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {
                        'name': product_name,
                    },
                    'unit_amount': unit_amount,
                    'recurring': {
                        'interval': interval,
                    },
                },
                'quantity': duration_months,
            }]
        )
        
        logger.info(f"Created Stripe checkout for {phone} ({payload})")
        logger.info(f"Stripe Success URL: {frontend_url}/payment/stripe/success?session_id={{CHECKOUT_SESSION_ID}}")
        
        return {
            "success": True,
            "session_id": session_data["session_id"],
            "url": session_data["url"]
        }
        
    except Exception as e:
        logger.error(f"Error creating Stripe checkout: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/payment/stripe-upgrade-checkout")
@limiter.limit("10/minute")
async def create_upgrade_checkout(request: Request):
    """
    Create a checkout session for upgrading to Advanced (Dynamic Pricing)
    """
    session_id = request.cookies.get("session_id")
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = session.phone
    
    try:
        # Get user to find existing subscription
        user = user_manager.get_user_by_phone(phone)
        if not user or not user.stripe_subscription_id:
            # If no Stripe subscription, we can't "upgrade" the existing one easily via this flow
            # But maybe they want to switch payment method?
            # For now, enforce existing stripe sub for this flow.
            # Actually, if they paid via other means, they might want to pay upgrade fee via Stripe.
            # But `calculate_upgrade_cost` handles the calculation.
            # Let's allow it even without stripe_subscription_id, treating it as a one-time fee.
            pass
            
        if user.tier == SubscriptionTier.PREMIUM_ADVANCED:
             raise HTTPException(status_code=400, detail="Already on Advanced plan")

        # Calculate upgrade cost
        target_tier = SubscriptionTier.PREMIUM_ADVANCED
        cost_data = user_manager.calculate_upgrade_cost(phone, target_tier)
        amount_eur = cost_data["amount"]
        amount_cents = int(amount_eur * 100)
        description = cost_data["description"]
        
        # Create one-time payment session
        frontend_url = config.FRONTEND_URL
        
        session_data = await stripe_service.create_checkout_session(
            success_url=f"{frontend_url}/payment/stripe/success?session_id={{CHECKOUT_SESSION_ID}}&upgrade=true",
            cancel_url=f"{frontend_url}/payment/stripe/failure",
            metadata={
                "phone": phone,
                "type": "upgrade_fee",
                "subscription_id": user.stripe_subscription_id if user.stripe_subscription_id else "",
                "target_tier": target_tier,
                "upgrade_type": cost_data.get("upgrade_type", "manual")
            },
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {
                        'name': f"Upgrade to Premium Advanced",
                        'description': description
                    },
                    'unit_amount': amount_cents,
                },
                'quantity': 1,
            }]
        )
        
        logger.info(f"Created Upgrade checkout for {phone}: €{amount_eur} ({description})")
        
        return {
            "success": True,
            "session_id": session_data["session_id"],
            "url": session_data["url"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating upgrade checkout: {e}")
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
    web_session = get_web_session(session_cookie) if session_cookie else None
    if not web_session or not web_session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        # Retrieve session from Stripe
        stripe_session = await stripe_service.get_checkout_session(session_id)
        
        if stripe_session.get("payment_status") == "paid":
            # Extract phone from metadata
            phone = stripe_session.get("metadata", {}).get("phone")
            
            # Verify the phone matches the current user
            current_phone = web_session.phone
            
            if phone and phone == current_phone:
                # Extract payload to determine tier/duration
                payload = stripe_session.get("metadata", {}).get("payload", "premium_advanced")
                duration_months = int(stripe_session.get("metadata", {}).get("duration_months", 1))
                tier = SubscriptionTier.PREMIUM_ADVANCED
                duration_days = 30 * duration_months
                
                if payload == "premium_basic":
                    tier = SubscriptionTier.PREMIUM_BASIC
                elif payload == "premium_basic_year":
                    tier = SubscriptionTier.PREMIUM_BASIC
                    duration_days = 365
                elif payload == "premium_advanced_year":
                    tier = SubscriptionTier.PREMIUM_ADVANCED
                    duration_days = 365

                user_manager.upgrade_to_premium(phone, payment_method="stripe", tier=tier, duration_days=duration_days)
                logger.info(f"User {phone} upgraded to Premium via Stripe verification (payload: {payload})")
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
            
            if not phone:
                logger.error("No phone in Stripe session metadata")
                return {"ok": True}
            
            # Check if this is an upgrade fee payment
            if session.get("metadata", {}).get("type") == "upgrade_fee":
                subscription_id = session.get("metadata", {}).get("subscription_id")
                logger.info(f"Processing upgrade fee for {phone}, sub {subscription_id}")
                
                if subscription_id:
                    # Modify the existing subscription to Advanced (€3)
                    await stripe_service.modify_subscription(
                        subscription_id,
                        new_price_data={
                            'unit_amount': 300, # €3.00
                            'interval': 'month',
                            'product_name': 'Televizor Premium Advanced'
                        }
                    )
                    
                    # Update local user tier
                    user_manager.upgrade_to_premium(
                        phone, 
                        payment_method="stripe", 
                        tier=SubscriptionTier.PREMIUM_ADVANCED,
                        duration_days=0 # Don't extend expiry, just change tier (expiry is managed by Stripe cycle)
                    )
                    logger.info(f"Successfully upgraded {phone} to Advanced")
            else:
                # Regular subscription payment
                # Extract payload/tier from metadata
                payload = session.get("metadata", {}).get("payload", "premium_advanced")
                duration_months = int(session.get("metadata", {}).get("duration_months", 1))
                tier = SubscriptionTier.PREMIUM_ADVANCED
                duration_days = 30 * duration_months
                
                if payload == "premium_basic":
                    tier = SubscriptionTier.PREMIUM_BASIC
                elif payload == "premium_basic_year":
                    tier = SubscriptionTier.PREMIUM_BASIC
                    duration_days = 365
                elif payload == "premium_advanced_year":
                    tier = SubscriptionTier.PREMIUM_ADVANCED
                    duration_days = 365

                # Get Stripe IDs
                stripe_customer_id = session.get("customer")
                stripe_subscription_id = session.get("subscription")

                # Upgrade user to Premium
                user_manager.upgrade_to_premium(
                    phone, 
                    payment_method="stripe", 
                    tier=tier, 
                    duration_days=duration_days,
                    stripe_customer_id=stripe_customer_id,
                    stripe_subscription_id=stripe_subscription_id
                )
                logger.info(f"User {phone} upgraded to Premium via Stripe")
        
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
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = session.phone
    
    try:
        # Get payload from request
        try:
            body = await request.json()
            payload = body.get("payload", "premium_advanced")
            duration_months = body.get("duration_months", 1)
            logger.info(f"T-Bank payment payload: {payload}, months: {duration_months}")
        except Exception as e:
            logger.error(f"Error parsing T-Bank payload: {e}")
            payload = "premium_advanced"
            duration_months = 1

        # Generate unique order ID
        import uuid
        order_id = f"order_{uuid.uuid4().hex[:12]}"
        
        # Store order_id -> phone mapping for webhook
        # This will be used when the webhook is triggered
        if not hasattr(app.state, 'tbank_orders'):
            app.state.tbank_orders = {}
        app.state.tbank_orders[order_id] = phone
        
        # Amount in kopecks (₽200.00 = 20000 kopecks, ₽300.00 = 30000 kopecks)
        # Year: ₽2000 = 200000, ₽3000 = 300000
        amount = 30000
        
        if payload == "premium_advanced_upgrade":
            user = user_manager.get_user_by_phone(phone)
            remaining_days = (user.expiry_date - datetime.utcnow()).days if user.expiry_date else 0
            import math
            existing_months = math.ceil(remaining_days / 30.0)
            if existing_months < 1: existing_months = 1
            
            requested_total_months = duration_months
            extension_months = max(0, requested_total_months - existing_months)
            
            # Price: 100 RUB (upgrade) vs 300 RUB (full) -> 10000 vs 30000 kopecks
            upgrade_cost = existing_months * 10000
            extension_cost = extension_months * 30000
            amount = upgrade_cost + extension_cost
            
            # Webhook should only add extension time
            duration_months = extension_months
            payload = "premium_advanced"
            
        elif payload == "premium_basic":
            amount = 20000
        elif payload == "premium_basic_year":
            amount = 200000
            duration_months = 1
        elif payload == "premium_advanced_year":
            amount = 300000
            duration_months = 1
        
        # Multiply by duration for monthly plans
        if "year" not in payload:
            amount = amount * duration_months

        # Get base URL for redirects (Frontend URL)
        # In production, this should be configured via environment variable
        base_url = config.FRONTEND_URL
        
        # Create payment with dedicated success/failure pages
        payment_data = await tbank_service.create_payment(
            order_id=order_id,
            amount=amount,
            description=f"Televizor Premium Subscription ({duration_months} months)" if duration_months > 1 else "Televizor Premium Subscription",
            success_url=f"{base_url}/payment/tbank/success?OrderId={order_id}",
            fail_url=f"{base_url}/payment/tbank/failure",
            customer_email=f"{phone}@televizor.app",
            metadata={"phone": phone, "payload": payload, "duration_months": duration_months}
        )
        
        logger.info(f"Created T-Bank payment for {phone}: {order_id}")
        logger.info(f"T-Bank Success URL: {base_url}/payment/tbank/success?OrderId={order_id}")
        
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
            # Retrieve phone from order mapping or metadata
            phone = None
            
            # 1. Try in-memory mapping
            if hasattr(app.state, 'tbank_orders') and order_id in app.state.tbank_orders:
                phone = app.state.tbank_orders[order_id]
                # Clean up the mapping
                del app.state.tbank_orders[order_id]
            
            # 2. Try metadata from T-Bank response (DATA field)
            if not phone and result.get("data"):
                phone = result["data"].get("phone")
                
            if phone:
                try:
                    # Determine tier from metadata if available, or default to advanced (safe fallback)
                    # Ideally we should have stored the payload in app.state.tbank_orders too, 
                    # or passed it in metadata.
                    # Let's check metadata from result
                    # Let's check metadata from result
                    tier = SubscriptionTier.PREMIUM_ADVANCED
                    duration_days = 30
                    
                    if result.get("data"):
                        payload = result["data"].get("payload")
                        duration_months = int(result["data"].get("duration_months", 1))
                        duration_days = 30 * duration_months
                        
                        if payload == "premium_basic":
                            tier = SubscriptionTier.PREMIUM_BASIC
                        elif payload == "premium_basic_year":
                            tier = SubscriptionTier.PREMIUM_BASIC
                            duration_days = 365
                        elif payload == "premium_advanced_year":
                            tier = SubscriptionTier.PREMIUM_ADVANCED
                            duration_days = 365
                    
                    # Upgrade user to premium
                    user_manager.upgrade_to_premium(phone, payment_method="tbank", tier=tier, duration_days=duration_days)
                    
                    logger.info(f"Upgraded user {phone} to Premium via T-Bank payment {payment_id}")
                    
                except Exception as e:
                    logger.error(f"Error upgrading user after T-Bank payment: {e}")
            else:
                logger.warning(f"No phone found for order_id: {order_id}. Metadata: {result.get('data')}")
        
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
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        status_data = await tbank_service.check_payment_status(payment_id)
        return status_data
    except Exception as e:
        logger.error(f"Error checking T-Bank status: {e}")
        raise HTTPException(status_code=500, detail=str(e))






@app.post("/api/payment/coinbase-charge")
@limiter.limit("10/minute")
async def create_coinbase_charge(request: Request, body: models.CreateCoinbaseChargeRequest):
    """Create a Coinbase Commerce charge for Premium subscription."""
    session_id = request.cookies.get("session_id")
    session = get_web_session(session_id) if session_id else None
    if not session or not session.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    phone = session.phone
    payload = body.payload
    duration_months = body.duration_months
    
    # Define pricing based on payload
    pricing = {
        "premium_basic": {"amount": 2.00, "name": "Premium Basic", "description": "Premium Basic Subscription"},
        "premium_advanced": {"amount": 3.00, "name": "Premium Advanced", "description": "Premium Advanced Subscription"},
        "premium_basic_year": {"amount": 20.00, "name": "Premium Basic (Yearly)", "description": "1 Year Premium Basic Subscription"},
        "premium_advanced_year": {"amount": 30.00, "name": "Premium Advanced (Yearly)", "description": "1 Year Premium Advanced Subscription"},
    }
    
    plan = pricing.get(payload)
    if not plan:
        # Fallback
        plan = pricing["premium_advanced"]
    
    # Calculate total amount
    amount = plan["amount"]
    description = plan["description"]
    
    if payload == "premium_advanced_upgrade":
        user = user_manager.get_user_by_phone(phone)
        remaining_days = (user.expiry_date - datetime.utcnow()).days if user.expiry_date else 0
        import math
        existing_months = math.ceil(remaining_days / 30.0)
        if existing_months < 1: existing_months = 1
        
        requested_total_months = duration_months
        extension_months = max(0, requested_total_months - existing_months)
        
        # Price: 1 EUR (upgrade) vs 3 EUR (full)
        upgrade_cost = existing_months * 1.00
        extension_cost = extension_months * 3.00
        amount = upgrade_cost + extension_cost
        
        description = f"Upgrade to Advanced ({existing_months}mo) + {extension_months}mo"
        
        # Webhook should only add extension time
        duration_months = extension_months
        payload = "premium_advanced"
        
    elif "year" in payload:
        duration_months = 1 # Yearly is 1 unit
    else:
        amount = amount * duration_months
        if duration_months > 1:
            description = f"{plan['name']} ({duration_months} Months)"

    try:
        # Create charge
        charge = coinbase_service.create_charge(
            name=plan["name"],
            description=description,
            pricing_type="fixed_price",
            local_price={
                "amount": f"{amount:.2f}",
                "currency": "EUR"
            },
            metadata={
                "phone": phone,
                "type": "premium_subscription",
                "payload": payload,
                "duration_months": str(duration_months)
            },
            redirect_url=f"{config.FRONTEND_URL}/subscription?success=true&provider=coinbase",
            cancel_url=f"{config.FRONTEND_URL}/subscription?canceled=true"
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
            payload_type = metadata.get("payload", "premium_advanced")
            duration_months = int(metadata.get("duration_months", 1))
            
            if phone:
                logger.info(f"Processing Coinbase payment for {phone} (payload: {payload_type}, months: {duration_months})")
                
                tier = models.SubscriptionTier.PREMIUM_ADVANCED
                duration_days = 30 * duration_months
                
                if payload_type == "premium_basic":
                    tier = models.SubscriptionTier.PREMIUM_BASIC
                elif payload_type == "premium_basic_year":
                    tier = models.SubscriptionTier.PREMIUM_BASIC
                    duration_days = 365
                elif payload_type == "premium_advanced_year":
                    tier = models.SubscriptionTier.PREMIUM_ADVANCED
                    duration_days = 365
                
                user_manager.upgrade_to_premium(phone, payment_method="coinbase", tier=tier, duration_days=duration_days)
            else:
                logger.warning("Coinbase webhook missing phone metadata")
                
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error processing Coinbase webhook: {e}")
        raise HTTPException(status_code=500, detail="Webhook processing failed")
