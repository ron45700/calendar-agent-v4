"""
User data models for Agentic Calendar 2.0
Uses TypedDict for type hints while maintaining Firestore compatibility.
"""

from typing import TypedDict, Optional, Dict, List
from datetime import datetime


class PersonalInfo(TypedDict, total=False):
    """User's personal information for personalized interactions."""
    nickname: Optional[str]          # What the bot calls the user
    gender: Optional[str]            # "male" | "female" | "neutral"
    use_telegram_style: bool         # Use Telegram stickers/emojis in responses


class CalendarConfig(TypedDict, total=False):
    """Google Calendar configuration and tokens."""
    access_token: Optional[str]
    refresh_token: Optional[str]
    token_expiry: Optional[datetime]
    color_map: Dict[str, str]        # Category to Google Calendar color ID
    reminder_hour: int               # Hour for daily reminders (0-23)


class Reminder(TypedDict):
    """A scheduled reminder."""
    id: str
    text: str
    trigger_time: datetime
    sent: bool


class PendingCommand(TypedDict, total=False):
    """Stores a command to retry after re-authentication."""
    command: Optional[str]           # Original command text
    timestamp: Optional[datetime]    # When the command was stored


class UserData(TypedDict, total=False):
    """
    Complete user document schema for Firestore.
    Document ID is the Telegram user_id as string.
    """
    user_id: int                           # Telegram user ID
    onboarding_completed: bool             # Has user finished onboarding?
    current_state: Optional[str]           # FSM state (e.g., "awaiting_nickname")
    
    # Feature toggles
    enable_reminders: bool                 # Toggle "Remind me" feature
    enable_daily_check: bool               # Toggle daily task check-in
    
    personal_info: PersonalInfo
    calendar_config: CalendarConfig
    contacts: Dict[str, str]               # Name to email mapping
    reminders: List[Reminder]
    pending_command: PendingCommand
    
    created_at: datetime
    updated_at: datetime


def create_default_user(user_id: int) -> UserData:
    """
    Create a new user document with default values.
    
    Args:
        user_id: Telegram user ID
        
    Returns:
        UserData with all defaults set
    """
    from config import DEFAULT_REMINDER_HOUR, DEFAULT_COLOR_MAP
    
    now = datetime.utcnow()
    
    return UserData(
        user_id=user_id,
        onboarding_completed=False,
        current_state=None,
        # Feature toggles - default to False for engaging first experience
        enable_reminders=False,
        enable_daily_check=False,
        personal_info=PersonalInfo(
            nickname=None,
            gender=None,
            use_telegram_style=True  # Use stickers/emojis by default
        ),
        calendar_config=CalendarConfig(
            access_token=None,
            refresh_token=None,
            token_expiry=None,
            color_map=DEFAULT_COLOR_MAP.copy(),
            reminder_hour=DEFAULT_REMINDER_HOUR
        ),
        contacts={},
        reminders=[],
        pending_command=PendingCommand(
            command=None,
            timestamp=None
        ),
        created_at=now,
        updated_at=now
    )
