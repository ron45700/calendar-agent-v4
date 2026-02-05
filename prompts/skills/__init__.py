"""
Skills Module for Agentic Calendar Prompts
Contains intent-specific prompts for each handler.
"""

from prompts.skills.create_event import CREATE_EVENT_PROMPT
from prompts.skills.edit_preferences import PREFERENCES_PROMPT
from prompts.skills.reminders import REMINDERS_PROMPT
from prompts.skills.daily_check import DAILY_CHECK_PROMPT
from prompts.skills.chat import CHAT_PROMPT
from prompts.skills.get_events import GET_EVENTS_PROMPT

__all__ = [
    "CREATE_EVENT_PROMPT",
    "PREFERENCES_PROMPT",
    "REMINDERS_PROMPT",
    "DAILY_CHECK_PROMPT",
    "CHAT_PROMPT",
    "GET_EVENTS_PROMPT"
]
