"""
Chat handlers for Agentic Calendar 2.0
Handles voice messages (Whisper) and text messages (LLM).
Integrates with event creation when calendar intent is detected.
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
from bot.utils import get_random_thinking_phrase, get_formatted_current_time
from bot.prompts import get_system_prompt
from bot.handlers.events import process_event_request


# Create router for chat handlers
router = Router(name="chat_router")


# =============================================================================
# Helper Functions
# =============================================================================

def is_registered(user: Optional[UserData]) -> bool:
    """Check if user exists in database (completed OAuth at least once)."""
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


def detect_event_intent(text: str) -> bool:
    """
    Detect if the text expresses intent to create a calendar event.
    Uses keyword heuristics for quick detection.
    """
    # Hebrew event keywords
    event_keywords = [
        "פגישה", "פגישות", "אירוע", "תזכורת", "תזכיר",
        "להזכיר", "ביומן", "קבע", "קביעה", "לקבוע",
        "פגש", "פוגש", "נפגש", "נפגשים",
        "חדר כושר", "אימון", "ספורט", "יוגה",
        "רופא", "דוקטור", "פיזיו",
        "שיעור", "הרצאה", "קורס", "לימודים",
        "יום הולדת", "חגיגה", "מסיבה",
        "טיסה", "נסיעה", "חופשה",
        "תור", "תורים",
        "הזמן", "להזמין", "הזמנה",
        "מחר", "היום", "בשעה", "ב-", "בעוד"
    ]
    
    text_lower = text.lower()
    
    # Check for time indicators combined with actions
    has_time_indicator = any(kw in text_lower for kw in ["מחר", "היום", "בשעה", "ב-", "בעוד", "ביום", "בתאריך"])
    has_action_indicator = any(kw in text_lower for kw in ["פגישה", "אירוע", "תזכורת", "קבע", "נפגש", "תור", "שיעור", "אימון"])
    
    # Direct event keywords
    direct_event = any(kw in text_lower for kw in event_keywords[:10])
    
    return (has_time_indicator and has_action_indicator) or direct_event


# =============================================================================
# Voice Message Handler
# =============================================================================

@router.message(F.voice)
async def handle_voice_message(message: Message, user: Optional[UserData], bot: Bot, state: FSMContext) -> None:
    """
    Handle voice messages - transcribe then route based on intent.
    """
    # Check if user is registered and authenticated
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
    
    # Send initial "transcribing" message
    status_msg = await message.answer("🎙️ מתמלל את ההודעה הקולית...")
    
    try:
        # Download voice file from Telegram
        voice = message.voice
        file = await bot.get_file(voice.file_id)
        
        # Create temp file with .ogg extension
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_file:
            temp_path = temp_file.name
        
        # Download to temp file
        await bot.download_file(file.file_path, temp_path)
        print(f"[Voice] Downloaded voice file to: {temp_path}")
        
        # Transcribe using Whisper
        transcribed_text = await openai_service.transcribe_audio_async(temp_path)
        
        # Clean up temp file
        try:
            os.unlink(temp_path)
        except Exception as e:
            print(f"[Voice] Failed to delete temp file: {e}")
        
        # Check for event intent
        if detect_event_intent(transcribed_text):
            await status_msg.edit_text(
                f"🎙️ שמעתי: _{transcribed_text}_\n\n"
                f"📅 מעבד בקשה ליומן...",
                parse_mode="Markdown"
            )
            # Route to event handler
            await process_event_request(message, user, state, transcribed_text)
        else:
            # Regular chat - Update status message with transcription + thinking phrase
            thinking_phrase = get_random_thinking_phrase()
            await status_msg.edit_text(
                f"🎙️ שמעתי: _{transcribed_text}_\n\n"
                f"💭 {thinking_phrase}",
                parse_mode="Markdown"
            )
            
            # Process with LLM
            response_text = await process_user_message(transcribed_text, user)
            
            # Send final response
            await message.answer(response_text)
        
    except Exception as e:
        print(f"[Voice] Error processing voice message: {e}")
        import traceback
        traceback.print_exc()
        
        await message.answer(
            "❌ שגיאה בעיבוד ההודעה הקולית.\n"
            "נסה שוב או שלח הודעת טקסט."
        )


# =============================================================================
# Text Message Handler (Fallback)
# =============================================================================

@router.message(F.text)
async def handle_text_message(message: Message, user: Optional[UserData], state: FSMContext) -> None:
    """
    Handle any text message that doesn't match a command.
    Routes to event creation or LLM based on intent detection.
    """
    if not is_registered(user):
        await message.answer(
            "👋 היי! אני צריך שתתחבר קודם.\n"
            "שלח /auth כדי להתחבר עם Google."
        )
        return
    
    if not has_valid_tokens(user):
        await message.answer(
            "🔐 ההרשאה שלך פגה.\n"
            "שלח /auth כדי להתחבר מחדש."
        )
        return
    
    if needs_onboarding(user):
        await message.answer(
            "🚧 עוד לא סיימנו את ההגדרות.\n"
            "שלח /start כדי להמשיך."
        )
        return
    
    text = message.text
    
    # Check for event intent
    if detect_event_intent(text):
        thinking_msg = await message.answer("📅 מעבד בקשה ליומן...")
        
        try:
            await process_event_request(message, user, state, text)
            # Delete thinking message after event processing
            try:
                await thinking_msg.delete()
            except:
                pass
        except Exception as e:
            print(f"[Event] Error processing event request: {e}")
            import traceback
            traceback.print_exc()
            
            try:
                await thinking_msg.delete()
            except:
                pass
            
            await message.answer(
                "❌ שגיאה בעיבוד הבקשה.\n"
                "נסה לנסח מחדש."
            )
        return
    
    # Regular chat flow
    thinking_phrase = get_random_thinking_phrase()
    thinking_msg = await message.answer(f"💭 {thinking_phrase}")
    
    try:
        # Process with LLM
        response_text = await process_user_message(text, user)
        
        # Delete thinking message
        try:
            await thinking_msg.delete()
        except:
            pass
        
        await message.answer(response_text)
        
    except Exception as e:
        print(f"[Text] Error processing message: {e}")
        import traceback
        traceback.print_exc()
        
        try:
            await thinking_msg.delete()
        except:
            pass
        
        await message.answer(
            "❌ שגיאה בעיבוד ההודעה.\n"
            "נסה שוב בבקשה."
        )


# =============================================================================
# Unified Message Processing (for non-event messages)
# =============================================================================

async def process_user_message(text: str, user: UserData) -> str:
    """
    Process user message through LLM and return response.
    
    Args:
        text: User's message text (from text or transcribed voice)
        user: User data from Firestore
        
    Returns:
        LLM response text
    """
    # Get user nickname for personalization
    nickname = user.get("personal_info", {}).get("nickname") or "שובב"
    
    # Get agent nickname for self-reference
    agent_nickname = user.get("personal_info", {}).get("agent_nickname") or "הבוט"
    
    # Get formatted current time
    current_time = get_formatted_current_time()
    
    # Build system prompt with agent name
    system_prompt = get_system_prompt(
        user_nickname=nickname,
        current_time=current_time,
        agent_nickname=agent_nickname
    )
    
    # Build messages (single turn for now, no history)
    messages = [
        {"role": "user", "content": text}
    ]
    
    # Get LLM response
    response = await openai_service.get_chat_response_async(
        messages=messages,
        system_prompt=system_prompt
    )
    
    return response
