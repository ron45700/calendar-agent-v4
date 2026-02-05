"""
Prompts Module for Agentic Calendar (Sochen Yoman)
Modular prompt structure for personality, guardrails, and intent routing.
"""

from prompts.base import SYSTEM_PROMPT, get_base_prompt
from prompts.router import ROUTER_SYSTEM_PROMPT, INTENT_FUNCTION_SCHEMA

# Skill prompts
from prompts.skills import (
    CREATE_EVENT_PROMPT,
    PREFERENCES_PROMPT,
    REMINDERS_PROMPT,
    DAILY_CHECK_PROMPT,
    CHAT_PROMPT,
    GET_EVENTS_PROMPT
)

__all__ = [
    # Base prompts
    "SYSTEM_PROMPT",
    "get_base_prompt",
    "ROUTER_SYSTEM_PROMPT", 
    "INTENT_FUNCTION_SCHEMA",
    # Skill prompts
    "CREATE_EVENT_PROMPT",
    "PREFERENCES_PROMPT",
    "REMINDERS_PROMPT",
    "DAILY_CHECK_PROMPT",
    "CHAT_PROMPT",
    "GET_EVENTS_PROMPT"
]
