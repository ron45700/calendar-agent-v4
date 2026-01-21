"""
Handlers package for Agentic Calendar 2.0

This package contains all Telegram message and callback handlers,
organized by functionality:
- commands: /start, /auth, /me, /settings commands
- onboarding: FSM-based onboarding flow
- events: Event creation with LLM parsing and calendar integration
- chat: Voice and text message processing with LLM
"""

from aiogram import Router

from .commands import router as commands_router
from .onboarding import router as onboarding_router
from .events import router as events_router
from .chat import router as chat_router


# Main router that includes all sub-routers
router = Router(name="main_router")

# Include sub-routers in order of priority
# Commands first, then onboarding FSM, then events FSM, then chat fallback
router.include_router(commands_router)
router.include_router(onboarding_router)
router.include_router(events_router)
router.include_router(chat_router)

__all__ = ["router"]
