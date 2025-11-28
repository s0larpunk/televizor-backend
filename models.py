from pydantic import BaseModel, field_validator
from typing import List, Optional, Dict

# Auth Models
class SendCodeRequest(BaseModel):
    phone: str

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v: str) -> str:
        if not v.startswith('+'):
            raise ValueError('Phone number must start with +')
        if not v[1:].isdigit():
            raise ValueError('Phone number must contain only digits after +')
        return v

class VerifyCodeRequest(BaseModel):
    phone: str
    code: str
    phone_code_hash: str

class VerifyPasswordRequest(BaseModel):
    password: str

# Channel Models
class CreateChannelRequest(BaseModel):
    title: str
    about: Optional[str] = ""

    @field_validator('title')
    @classmethod
    def validate_title(cls, v: str) -> str:
        if not v.strip():
            raise ValueError('Title cannot be empty')
        if len(v) > 100:
            raise ValueError('Title too long (max 100 chars)')
        return v.strip()
    about: Optional[str] = ""

# Feed Models
# Feed Models
class FilterConfig(BaseModel):
    keywords_include: List[str] = []
    keywords_exclude: List[str] = []
    has_image: Optional[bool] = None
    has_video: Optional[bool] = None
    max_messages_per_hour: Optional[int] = None
    max_messages_per_day: Optional[int] = None

class FeedConfig(BaseModel):
    id: Optional[str] = None
    name: str
    source_channel_ids: List[int]
    destination_channel_id: int
    active: bool = True
    delay_enabled: bool = True
    filters: Optional[FilterConfig] = None
    source_filters: Dict[int, FilterConfig] = {}
    error: Optional[str] = None

class CreateFeedRequest(BaseModel):
    name: str
    source_channel_ids: List[int]
    destination_channel_id: int
    delay_enabled: bool = True

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError('Feed name cannot be empty')
        if len(v) > 100:
            raise ValueError('Feed name too long (max 100 chars)')
        return v.strip()

    @field_validator('source_channel_ids')
    @classmethod
    def validate_source_channels(cls, v: List[int]) -> List[int]:
        if len(v) == 0:
            raise ValueError('At least one source channel required')
        if len(v) > 50:
            raise ValueError('Maximum 50 source channels allowed')
        return v
    filters: Optional[FilterConfig] = None
    source_filters: Optional[Dict[int, FilterConfig]] = None

class UpdateFeedRequest(BaseModel):
    name: Optional[str] = None
    source_channel_ids: Optional[List[int]] = None
    destination_channel_id: Optional[int] = None
    active: Optional[bool] = None
    delay_enabled: Optional[bool] = None
    filters: Optional[FilterConfig] = None
    source_filters: Optional[Dict[int, FilterConfig]] = None

# Subscription Models
class SubscriptionTier(str):
    FREE = "free"
    TRIAL = "trial"
    PREMIUM = "premium"

class SubscriptionStatus(BaseModel):
    tier: str  # using str instead of enum for simpler serialization
    trial_start_date: Optional[str] = None
    expiry_date: Optional[str] = None
    is_expired: bool = False
    trial_available: bool = False  # True if user has never activated trial
