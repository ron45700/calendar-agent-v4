"""
User loading middleware for Agentic Calendar 2.0
Loads user from Firestore before each handler (NO auto-creation).

Architecture Note:
- User documents are ONLY created after successful Google OAuth callback (Phase 3)
- Middleware returns None for users not in DB
- Handlers must gracefully handle user=None case
"""

from typing import Callable, Dict, Any, Awaitable, Optional
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

from services.firestore_service import firestore_service


class UserMiddleware(BaseMiddleware):
    """
    Middleware that loads user data from Firestore before handlers execute.
    
    Important: Does NOT create users. Returns None if user not found.
    User creation happens only after successful OAuth in Phase 3.
    """
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        """
        Process incoming event and attach user data if exists.
        
        Args:
            handler: The next handler in the chain
            event: Incoming Telegram event (Message, CallbackQuery, etc.)
            data: Handler context data
            
        Returns:
            Result from the handler
        """
        user_id = self._extract_user_id(event)
        
        if user_id:
            # Only fetch - do NOT create
            user_data = firestore_service.get_user(user_id)
            data["user"] = user_data  # Will be None if not found
            
            if user_data:
                print(f"[Middleware] Loaded existing user {user_id}")
            else:
                print(f"[Middleware] User {user_id} not in DB (anonymous)")
        else:
            data["user"] = None
            print("[Middleware] Could not extract user_id from event")
        
        # Continue to handler
        return await handler(event, data)
    
    def _extract_user_id(self, event: TelegramObject) -> Optional[int]:
        """
        Extract Telegram user ID from various event types.
        
        Args:
            event: Telegram event object
            
        Returns:
            User ID or None if not found
        """
        if isinstance(event, Message):
            return event.from_user.id if event.from_user else None
        elif isinstance(event, CallbackQuery):
            return event.from_user.id if event.from_user else None
        
        # Try generic attribute access for other event types
        if hasattr(event, "from_user") and event.from_user:
            return event.from_user.id
        
        return None
