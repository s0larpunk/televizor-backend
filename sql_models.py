from sqlalchemy import Boolean, Column, Integer, String, JSON, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from database import Base
import datetime

class User(Base):
    __tablename__ = "users"

    phone = Column(String, primary_key=True, index=True)
    telegram_id = Column(Integer, unique=True, index=True, nullable=True)
    tier = Column(String, default="free")
    trial_start_date = Column(DateTime, nullable=True)
    expiry_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    feeds = relationship("Feed", back_populates="owner")

class Feed(Base):
    __tablename__ = "feeds"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.phone"))
    name = Column(String)
    source_channel_ids = Column(JSON)  # List of integers
    destination_channel_id = Column(Integer)
    active = Column(Boolean, default=True)
    delay_enabled = Column(Boolean, default=True)
    filters = Column(JSON, nullable=True)
    source_filters = Column(JSON, nullable=True)
    error = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    owner = relationship("User", back_populates="feeds")
