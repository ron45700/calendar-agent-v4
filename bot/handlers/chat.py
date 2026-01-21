"""
Chat handlers for Agentic Calendar 2.0
Main entry point for all user messages (voice and text).
Uses LLM intent classification for intelligent routing.

Supports intents:
- create_event: Calendar events
- set_reminder: Quick pings/reminders
- reschedule_event: Move/postpone events
- edit_preferences: Settings changes
- chat: General conversation
"""

import os
import tempfile
from typing import Optional
from aiogram import Router, F, Bot
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from models.user import UserData
from services.openai_service import openai_service
from services.llm_service import llm_service
from services.firestore_service import firestore_service
from bot.utils import get_random_thinking_phrase, get_formatted_current_time
from bot.handlers.events import process_create_event


# Create router for chat handlers
router = Router(name="chat_router")


# =============================================================================
# Helper Functions
# =============================================================================

def is_registered(user: Optional[UserData]) -> bool:
    """Check if user exists in database."""
    return user is not None


def has_valid_tokens(user: Optional[UserData]) -> bool:
    """Check if user has OAuth tokens stored."""
    if not user:
        return False
    return user.get("calendar_config", {}).get("refresh_token") is not None


def needs_onboarding(user: Optional[UserData]) -> bool:
    """Check if user needs to complete onboarding."""
    if not user:
        return False
    return not user.get("onboarding_completed", False)


# =============================================================================
# Voice Message Handler
# =============================================================================

@router.message(F.voice)
async def handle_voice_message(message: Message, user: Optional[UserData], bot: Bot, state: FSMContext) -> None:
    """Handle voice messages - transcribe then route via intent classification."""
    if not is_registered(user):
        await message.answer(
            "🎙️ קיבלתי את ההודעה הקולית!\n"
            "אבל קודם אני צריך שתתחבר.\n"
            "שלח /auth כדי להתחבר עם Google."
        )
        return
    
    if not has_valid_tokens(user):
        await message.answer(
            "🎙️ קיבלתי את ההודעה הקולית!\n"
            "אבל ההרשאה שלך פגה.\n"
            "שלח /auth כדי להתחבר מחדש."
        )
        return
    
    if needs_onboarding(user):
        await message.answer(
            "🎙️ קיבלתי את ההודעה הקולית!\n"
            "אבל קודם בוא נסיים את ההגדרות.\n"
            "שלח /start כדי להמשיך."
        )
        return
    
    user_id = message.from_user.id
    status_msg = await message.answer("🎙️ מתמלל את ההודעה הקולית...")
    
    try:
        # Download and transcribe
        voice = message.voice
        file = await bot.get_file(voice.file_id)
        
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_file:
            temp_path = temp_file.name
        
        await bot.download_file(file.file_path, temp_path)
        transcribed_text = await openai_service.transcribe_audio_async(temp_path)
        
        try:
            os.unlink(temp_path)
        except:
            pass
        
        # Save to history
        firestore_service.save_message(user_id, "user", transcribed_text, {"voice": True})
        
        # Update status
        thinking_phrase = get_random_thinking_phrase()
        await status_msg.edit_text(
            f"🎙️ שמעתי: _{transcribed_text}_\n\n💭 {thinking_phrase}",
            parse_mode="Markdown"
        )
        
        # Process intent
        await process_user_intent(message, user, state, transcribed_text, user_id)
        
    except Exception as e:
        print(f"[Voice] Error: {e}")
        import traceback
        traceback.print_exc()
        await message.answer("❌ שגיאה בעיבוד ההודעה הקולית.\nנסה שוב או שלח הודעת טקסט.")


# =============================================================================
# Text Message Handler
# =============================================================================

@router.message(F.text)
async def handle_text_message(message: Message, user: Optional[UserData], state: FSMContext) -> None:
    """Handle text messages - route via LLM intent classification."""
    if not is_registered(user):
        await message.answer("👋 היי! אני צריך שתתחבר קודם.\nשלח /auth כדי להתחבר עם Google.")
        return
    
    if not has_valid_tokens(user):
        await message.answer("🔐 ההרשאה שלך פגה.\nשלח /auth כדי להתחבר מחדש.")
        return
    
    if needs_onboarding(user):
        await message.answer("🚧 עוד לא סיימנו את ההגדרות.\nשלח /start כדי להמשיך.")
        return
    
    text = message.text
    user_id = message.from_user.id
    
    # Save to history
    firestore_service.save_message(user_id, "user", text)
    
    # Show thinking
    thinking_phrase = get_random_thinking_phrase()
    thinking_msg = await message.answer(f"💭 {thinking_phrase}")
    
    try:
        await process_user_intent(message, user, state, text, user_id, thinking_msg)
    except Exception as e:
        print(f"[Text] Error: {e}")
        import traceback
        traceback.print_exc()
        
        try:
            await thinking_msg.delete()
        except:
            pass
        await message.answer("❌ שגיאה בעיבוד ההודעה.\nנסה שוב בבקשה.")


# =============================================================================
# Unified Intent Processing
# =============================================================================

async def process_user_intent(
    message: Message,
    user: UserData,
    state: FSMContext,
    text: str,
    user_id: int,
    thinking_msg: Optional[Message] = None
) -> None:
    """Process user message through LLM intent classification and route accordingly."""
    current_time = get_formatted_current_time()
    
    # Get user info
    personal_info = user.get("personal_info", {})
    agent_name = personal_info.get("agent_nickname") or "הבוט"
    user_nickname = personal_info.get("nickname") or "חבר"
    
    # Get preferences
    user_preferences = {
        "enable_reminders": user.get("enable_reminders", False),
        "enable_daily_check": user.get("enable_daily_check", False),
        "color_map": user.get("calendar_config", {}).get("color_map", {}),
        "daily_check_hour": user.get("calendar_config", {}).get("daily_check_hour")
    }
    
    contacts = user.get("contacts", {})
    history = firestore_service.get_recent_messages(user_id, limit=8)
    
    # Classify intent
    result = await llm_service.parse_user_intent(
        text=text,
        current_time=current_time,
        user_preferences=user_preferences,
        contacts=contacts,
        history=history,
        agent_name=agent_name,
        user_nickname=user_nickname
    )
    
    intent = result.get("intent", "chat")
    response_text = result.get("response_text", "")
    payload = result.get("payload", {})
    
    print(f"[Intent] Classified: {intent}")
    
    # Delete thinking message
    if thinking_msg:
        try:
            await thinking_msg.delete()
        except:
            pass
    
    # =========================================================================
    # Intent Routing
    # =========================================================================
    
    if intent == "create_event":
        # Route to event creation
        await process_create_event(message, user, state, payload, response_text)
    
    elif intent == "set_reminder":
        # Placeholder for reminders
        reminder_text = payload.get("reminder_text", "משהו")
        due_time = payload.get("due_time", "")
        
        reminder_response = (
            f"📝 *תזכורת נרשמה!*\n\n"
            f"_{reminder_text}_\n\n"
            f"_(פיצ'ר התזכורות בפיתוח - אזכיר לך בקרוב!)_"
        )
        firestore_service.save_message(user_id, "assistant", reminder_response)
        await message.answer(reminder_response, parse_mode="Markdown")
    
    elif intent == "reschedule_event":
        # Placeholder for rescheduling
        original_hint = payload.get("original_event_hint", "האירוע")
        new_time = payload.get("new_start_time", "")
        
        reschedule_response = (
            f"🔄 *הזזת אירוע*\n\n"
            f"אני מבין שאתה רוצה להזיז את *{original_hint}*.\n\n"
            f"_(פיצ'ר העדכון בפיתוח - בינתיים אפשר למחוק וליצור מחדש)_"
        )
        firestore_service.save_message(user_id, "assistant", reschedule_response)
        await message.answer(reschedule_response, parse_mode="Markdown")
    
    elif intent == "edit_preferences":
        # Placeholder for preferences
        key = payload.get("key", "")
        value = payload.get("value", "")
        
        prefs_response = (
            f"⚙️ אני רואה שאתה רוצה לשנות הגדרות.\n\n"
            f"שלח /settings לעדכון ההגדרות, או ש*{response_text}*"
        )
        firestore_service.save_message(user_id, "assistant", prefs_response)
        await message.answer(prefs_response, parse_mode="Markdown")
    
    else:
        # General chat
        firestore_service.save_message(user_id, "assistant", response_text)
        await message.answer(response_text)
