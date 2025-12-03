from sqlalchemy import Boolean, Column, Integer, String, JSON, DateTime, ForeignKey, BigInteger
from sqlalchemy.orm import relationship
from database import Base
import datetime

class User(Base):
    __tablename__ = "users"

    phone = Column(String, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=True)
    tier = Column(String, default="free")
    trial_start_date = Column(DateTime, nullable=True)
    expiry_date = Column(DateTime, nullable=True)
    payment_method = Column(String, nullable=True)  # stripe, tbank, stars, crypto
    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    session_string = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    referral_code = Column(String, unique=True, index=True, nullable=True)
    referred_by = Column(String, ForeignKey("users.phone", ondelete="SET NULL"), nullable=True)
    referral_count = Column(Integer, default=0)

    feeds = relationship("Feed", back_populates="owner", cascade="all, delete-orphan")

class Feed(Base):
    __tablename__ = "feeds"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.phone", ondelete="CASCADE"))
    name = Column(String)
    source_channel_ids = Column(JSON)  # List of integers
    destination_channel_id = Column(BigInteger)
    active = Column(Boolean, default=True)
    delay_enabled = Column(Boolean, default=True)
    filters = Column(JSON, nullable=True)
    source_filters = Column(JSON, nullable=True)
    error = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    owner = relationship("User", back_populates="feeds")

class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_phone = Column(String, ForeignKey("users.phone", ondelete="CASCADE"), nullable=False)
    session_string = Column(String, nullable=False)
    instance_id = Column(String, default="default", nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_used_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationship
    user = relationship("User", back_populates="sessions")

# Update User to have relationship
User.sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")

class WebSession(Base):
    __tablename__ = "web_sessions"

    session_id = Column(String, primary_key=True, index=True)
    phone = Column(String, nullable=True)
    user_identifier = Column(String, nullable=True)
    phone_code_hash = Column(String, nullable=True)
    authenticated = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    expires_at = Column(DateTime)

class MessageLog(Base):
    __tablename__ = "message_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_phone = Column(String, index=True)
    source_channel_id = Column(BigInteger)
    destination_channel_id = Column(BigInteger)
    message_id = Column(BigInteger)
    status = Column(String)  # 'received', 'queued', 'forwarded', 'error'
    details = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
