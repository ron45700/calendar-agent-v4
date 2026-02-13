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
from .admin_tests import router as admin_tests_router


# Main router that includes all sub-routers
router = Router(name="main_router")

# Include sub-routers in order of priority
# Admin tests FIRST (highest priority - checks for admin states)
# Then commands, onboarding FSM, events FSM, chat fallback
router.include_router(admin_tests_router)
router.include_router(commands_router)
router.include_router(onboarding_router)
router.include_router(events_router)
router.include_router(chat_router)

__all__ = ["router"]
