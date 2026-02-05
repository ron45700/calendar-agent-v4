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
import sys
import tempfile
import random
import logging
from datetime import datetime, timedelta
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
from utils.performance import measure_time


# =============================================================================
# Logging Configuration
# =============================================================================

logger = logging.getLogger(__name__)


# Create router for chat handlers
router = Router(name="chat_router")


# Welcome back messages (48+ hours inactive)
WELCOME_BACK_MESSAGES = [
    "איזה כיף שחזרת {name}! התגעגעתי 😊",
    "היי {name}! שמח לראות אותך שוב! 👋",
    "{name}! כמה זמן, מה נשמע? 😄",
    "וואו {name} חזרת! חשבתי שכבר שכחת ממני 😅",
    "אהלן {name}! טוב לראות אותך! 🎉"
]


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


async def check_and_send_welcome_back(message: Message, user: UserData, user_id: int) -> None:
    """
    Check if user has been away for 48+ hours and send welcome back message.
    Also updates last_seen timestamp.
    """
    logger.info(f"[WelcomeBack] Checking last_seen for user {user_id}")
    
    last_seen = user.get("last_seen")
    nickname = user.get("personal_info", {}).get("nickname") or message.from_user.first_name or "חבר"
    
    # Check if we should send welcome back
    should_greet = False
    
    if last_seen:
        # Handle Firestore timestamp
        if hasattr(last_seen, 'timestamp'):
            last_seen_dt = datetime.fromtimestamp(last_seen.timestamp())
        elif isinstance(last_seen, datetime):
            last_seen_dt = last_seen
        else:
            last_seen_dt = None
        
        if last_seen_dt:
            time_diff = datetime.utcnow() - last_seen_dt
            if time_diff > timedelta(hours=48):
                should_greet = True
                logger.info(f"[WelcomeBack] User {user_id} was away for {time_diff}")
    
    # Update last_seen immediately
    firestore_service.update_last_seen(user_id)
    logger.info(f"[WelcomeBack] Updated last_seen for user {user_id}")
    
    # Send greeting if needed
    if should_greet:
        welcome_msg = random.choice(WELCOME_BACK_MESSAGES).format(name=nickname)
        logger.info(f"[WelcomeBack] Sending welcome back to user {user_id}")
        await message.answer(welcome_msg)


# =============================================================================
# Voice Message Handler
# =============================================================================

@router.message(F.voice)
@measure_time
async def handle_voice_message(message: Message, user: Optional[UserData], bot: Bot, state: FSMContext) -> None:
    """Handle voice messages - transcribe then route via intent classification."""
    user_id = message.from_user.id
    logger.info(f"📥 [Voice] Received from user {user_id}")
    
    if not is_registered(user):
        logger.warning(f"[Voice] User {user_id} not registered")
        await message.answer(
            "🎙️ קיבלתי את ההודעה הקולית!\n"
            "אבל קודם אני צריך שתתחבר.\n"
            "שלח /auth כדי להתחבר עם Google."
        )
        return
    
    if not has_valid_tokens(user):
        logger.warning(f"[Voice] User {user_id} has no valid tokens")
        await message.answer(
            "🎙️ קיבלתי את ההודעה הקולית!\n"
            "אבל ההרשאה שלך פגה.\n"
            "שלח /auth כדי להתחבר מחדש."
        )
        return
    
    if needs_onboarding(user):
        logger.warning(f"[Voice] User {user_id} needs onboarding")
        await message.answer(
            "🎙️ קיבלתי את ההודעה הקולית!\n"
            "אבל קודם בוא נסיים את ההגדרות.\n"
            "שלח /start כדי להמשיך."
        )
        return
    
    # Check for welcome back
    await check_and_send_welcome_back(message, user, user_id)
    
    status_msg = await message.answer("🎙️ מתמלל את ההודעה הקולית...")
    
    try:
        # Download and transcribe
        logger.info(f"[Voice] Downloading voice file for user {user_id}")
        voice = message.voice
        file = await bot.get_file(voice.file_id)
        
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_file:
            temp_path = temp_file.name
        
        await bot.download_file(file.file_path, temp_path)
        logger.info(f"[Voice] File downloaded, starting transcription")
        
        logger.info(f"🤖 [Whisper] Sending to OpenAI for transcription...")
        transcribed_text = await openai_service.transcribe_audio_async(temp_path)
        logger.info(f"✅ [Whisper] Transcription received: {transcribed_text[:50]}...")
        
        try:
            os.unlink(temp_path)
        except:
            pass
        
        # Save to history
        logger.info(f"[Firestore] Saving user message to history")
        firestore_service.save_message(user_id, "user", transcribed_text, {"voice": True})
        
        # Update status
        thinking_phrase = get_random_thinking_phrase()
        await status_msg.edit_text(
            f"🎙️ שמעתי: _{transcribed_text}_\n\n💭 {thinking_phrase}",
            parse_mode="Markdown"
        )
        
        # Process intent
        logger.info(f"[Intent] Processing user intent...")
        await process_user_intent(message, user, state, transcribed_text, user_id)
        
    except Exception as e:
        logger.error(f"❌ [Voice] Error: {e}")
        import traceback
        traceback.print_exc()
        await message.answer("❌ שגיאה בעיבוד ההודעה הקולית.\nנסה שוב או שלח הודעת טקסט.")


# =============================================================================
# Text Message Handler
# =============================================================================

@router.message(F.text)
@measure_time
async def handle_text_message(message: Message, user: Optional[UserData], state: FSMContext) -> None:
    """Handle text messages - route via LLM intent classification."""
    user_id = message.from_user.id
    text = message.text
    
    logger.info(f"📥 [Text] Received from user {user_id}: {text[:50]}...")
    
    if not is_registered(user):
        logger.warning(f"[Text] User {user_id} not registered")
        await message.answer("👋 היי! אני צריך שתתחבר קודם.\nשלח /auth כדי להתחבר עם Google.")
        return
    
    if not has_valid_tokens(user):
        logger.warning(f"[Text] User {user_id} has no valid tokens")
        await message.answer("🔐 ההרשאה שלך פגה.\nשלח /auth כדי להתחבר מחדש.")
        return
    
    if needs_onboarding(user):
        logger.warning(f"[Text] User {user_id} needs onboarding")
        await message.answer("🚧 עוד לא סיימנו את ההגדרות.\nשלח /start כדי להמשיך.")
        return
    
    # Check for welcome back
    await check_and_send_welcome_back(message, user, user_id)
    
    # Save to history
    logger.info(f"[Firestore] Saving user message to history")
    firestore_service.save_message(user_id, "user", text)
    
    # Show thinking
    thinking_phrase = get_random_thinking_phrase()
    thinking_msg = await message.answer(f"💭 {thinking_phrase}")
    logger.info(f"[UI] Sent thinking message")
    
    try:
        await process_user_intent(message, user, state, text, user_id, thinking_msg)
    except Exception as e:
        logger.error(f"❌ [Text] Error processing: {e}")
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

@measure_time
async def process_user_intent(
    message: Message,
    user: UserData,
    state: FSMContext,
    text: str,
    user_id: int,
    thinking_msg: Optional[Message] = None
) -> None:
    """Process user message through LLM intent classification and route accordingly."""
    logger.info(f"[Intent] Starting intent classification for user {user_id}")
    
    current_time = get_formatted_current_time()
    logger.info(f"[Intent] Current time: {current_time}")
    
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
    
    logger.info(f"[Firestore] Getting recent messages for context")
    history = firestore_service.get_recent_messages(user_id, limit=8)
    logger.info(f"[Firestore] Got {len(history)} messages from history")
    
    # Classify intent with OpenAI
    logger.info(f"🤖 [OpenAI] Sending request to classify intent...")
    try:
        result = await llm_service.parse_user_intent(
            text=text,
            current_time=current_time,
            user_preferences=user_preferences,
            contacts=contacts,
            history=history,
            agent_name=agent_name,
            user_nickname=user_nickname
        )
        logger.info(f"✅ [OpenAI] Response received!")
    except Exception as e:
        logger.error(f"❌ [OpenAI] Error calling LLM: {e}")
        import traceback
        traceback.print_exc()
        
        if thinking_msg:
            try:
                await thinking_msg.delete()
            except:
                pass
        await message.answer("❌ שגיאה בתקשורת עם OpenAI. נסה שוב.")
        return
    
    intent = result.get("intent", "chat")
    response_text = result.get("response_text", "")
    payload = result.get("payload", {})
    
    logger.info(f"[Intent] Classified as: {intent}")
    logger.info(f"[Intent] Response text: {response_text[:100] if response_text else 'EMPTY'}...")
    logger.info(f"[Intent] Payload: {payload}")
    
    # Delete thinking message
    if thinking_msg:
        try:
            logger.info(f"[UI] Deleting thinking message")
            await thinking_msg.delete()
        except Exception as e:
            logger.warning(f"[UI] Failed to delete thinking message: {e}")
    
    # =========================================================================
    # Intent Routing
    # =========================================================================
    
    if intent == "create_event":
        logger.info(f"[Routing] -> create_event")
        await process_create_event(message, user, state, payload, response_text)
    
    elif intent == "set_reminder":
        logger.info(f"[Routing] -> set_reminder")
        reminder_text = payload.get("reminder_text", "משהו")
        
        reminder_response = (
            f"📝 *תזכורת נרשמה!*\n\n"
            f"_{reminder_text}_\n\n"
            f"_(פיצ'ר התזכורות בפיתוח - אזכיר לך בקרוב!)_"
        )
        logger.info(f"[Firestore] Saving assistant response")
        firestore_service.save_message(user_id, "assistant", reminder_response)
        
        logger.info(f"📤 [Telegram] Sending response...")
        await message.answer(reminder_response, parse_mode="Markdown")
        logger.info(f"✅ [Telegram] Response sent!")
    
    elif intent == "reschedule_event":
        logger.info(f"[Routing] -> reschedule_event")
        original_hint = payload.get("original_event_hint", "האירוע")
        
        reschedule_response = (
            f"🔄 *הזזת אירוע*\n\n"
            f"אני מבין שאתה רוצה להזיז את *{original_hint}*.\n\n"
            f"_(פיצ'ר העדכון בפיתוח - בינתיים אפשר למחוק וליצור מחדש)_"
        )
        logger.info(f"[Firestore] Saving assistant response")
        firestore_service.save_message(user_id, "assistant", reschedule_response)
        
        logger.info(f"📤 [Telegram] Sending response...")
        await message.answer(reschedule_response, parse_mode="Markdown")
        logger.info(f"✅ [Telegram] Response sent!")
    
    elif intent == "edit_preferences":
        logger.info(f"[Routing] -> edit_preferences")
        
        prefs_response = (
            f"⚙️ אני רואה שאתה רוצה לשנות הגדרות.\n\n"
            f"שלח /settings לעדכון ההגדרות."
        )
        logger.info(f"[Firestore] Saving assistant response")
        firestore_service.save_message(user_id, "assistant", prefs_response)
        
        logger.info(f"📤 [Telegram] Sending response...")
        await message.answer(prefs_response, parse_mode="Markdown")
        logger.info(f"✅ [Telegram] Response sent!")
    
    else:
        # General chat
        logger.info(f"[Routing] -> chat (general)")
        
        if not response_text:
            logger.error(f"❌ [Chat] Empty response_text from OpenAI!")
            response_text = "סליחה, לא הבנתי. אפשר לנסח אחרת?"
        
        logger.info(f"[Firestore] Saving assistant response")
        firestore_service.save_message(user_id, "assistant", response_text)
        
        logger.info(f"📤 [Telegram] Sending response: {response_text[:50]}...")
        await message.answer(response_text)
        logger.info(f"✅ [Telegram] Response sent!")
    
    logger.info(f"[Intent] Processing complete for user {user_id}")
