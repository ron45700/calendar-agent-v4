"""
Event creation handlers for Agentic Calendar 2.0
Handles natural language event parsing, contact validation, and calendar creation.
"""

import re
from typing import Optional, Dict, List, Any
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from models.user import UserData
from services.llm_service import llm_service
from services.calendar_service import calendar_service
from services.firestore_service import firestore_service
from bot.states import EventFlowStates
from bot.utils import get_formatted_current_time, get_random_thinking_phrase
from bot.keyboards import get_yes_no_keyboard


# Create router for event handlers
router = Router(name="event_router")


# =============================================================================
# Helper Functions
# =============================================================================

def is_valid_email(email: str) -> bool:
    """Validate email format."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email.strip()) is not None


def get_user_tokens(user: UserData) -> Optional[Dict[str, str]]:
    """Extract OAuth tokens from user data."""
    calendar_config = user.get("calendar_config", {})
    if not calendar_config.get("refresh_token"):
        return None
    return {
        "access_token": calendar_config.get("access_token"),
        "refresh_token": calendar_config.get("refresh_token")
    }


def find_missing_contacts(
    attendee_names: List[str],
    user_contacts: Dict[str, str]
) -> List[str]:
    """
    Find attendee names that don't have emails in user's contact list.
    
    Args:
        attendee_names: List of names parsed from text
        user_contacts: User's contact dict {name: email}
        
    Returns:
        List of names without email mappings
    """
    missing = []
    contact_names_lower = {name.lower(): name for name in user_contacts.keys()}
    
    for name in attendee_names:
        name_lower = name.lower()
        # Check for exact or fuzzy match
        found = False
        for contact_lower, contact_name in contact_names_lower.items():
            if name_lower in contact_lower or contact_lower in name_lower:
                found = True
                break
        if not found:
            missing.append(name)
    
    return missing


def resolve_attendee_emails(
    attendee_names: List[str],
    user_contacts: Dict[str, str]
) -> List[Dict[str, str]]:
    """
    Resolve attendee names to emails from user's contact list.
    
    Returns:
        List of {name, email} dicts for all resolved attendees
    """
    resolved = []
    contact_names_lower = {name.lower(): (name, email) for name, email in user_contacts.items()}
    
    for name in attendee_names:
        name_lower = name.lower()
        for contact_lower, (contact_name, email) in contact_names_lower.items():
            if name_lower in contact_lower or contact_lower in name_lower:
                resolved.append({"name": contact_name, "email": email})
                break
    
    return resolved


# =============================================================================
# Event Creation Flow
# =============================================================================

async def process_event_request(
    message: Message,
    user: UserData,
    state: FSMContext,
    text: str
) -> None:
    """
    Main event processing logic - called from chat handler when event intent is detected.
    
    Args:
        message: Telegram message
        user: User data from Firestore
        state: FSM context
        text: User's text (from message or transcription)
    """
    user_id = message.from_user.id
    
    # Get current time for LLM context
    current_time = get_formatted_current_time()
    
    # Get user's contacts
    user_contacts = user.get("contacts", {})
    
    # Parse event with LLM
    parsed_event = await llm_service.parse_event_from_text(
        text=text,
        current_time=current_time,
        contacts=user_contacts
    )
    
    if not parsed_event:
        await message.answer(
            "🤔 לא הצלחתי להבין את הבקשה.\n"
            "נסה לנסח מחדש, למשל:\n"
            "_פגישה עם דני מחר ב-15:00_\n"
            "_חדר כושר ביום שלישי בשעה 18:00_",
            parse_mode="Markdown"
        )
        return
    
    # Check for missing contacts
    attendee_names = parsed_event.get("attendees", [])
    
    if attendee_names:
        missing_contacts = find_missing_contacts(attendee_names, user_contacts)
        
        if missing_contacts:
            # Stop flow - need email for missing contact
            missing_name = missing_contacts[0]  # Handle one at a time
            
            # Save pending event data to FSM
            await state.update_data(
                pending_event=parsed_event,
                missing_contact_name=missing_name,
                remaining_missing=missing_contacts[1:] if len(missing_contacts) > 1 else []
            )
            
            await state.set_state(EventFlowStates.WAITING_FOR_MISSING_CONTACT_EMAIL)
            
            await message.answer(
                f"👤 שמתי לב שביקשת להזמין את *{missing_name}*,\n"
                f"אבל אין לי את המייל שלו.\n\n"
                f"מה המייל של {missing_name}?",
                parse_mode="Markdown"
            )
            return
    
    # All contacts resolved - create event
    await create_event_from_parsed(message, user, parsed_event)


async def create_event_from_parsed(
    message: Message,
    user: UserData,
    parsed_event: Dict[str, Any]
) -> None:
    """
    Create Google Calendar event from parsed data.
    """
    # Get user tokens
    tokens = get_user_tokens(user)
    if not tokens:
        await message.answer(
            "🔐 ההרשאה שלך פגה.\n"
            "שלח /auth כדי להתחבר מחדש."
        )
        return
    
    # Resolve attendees to emails
    user_contacts = user.get("contacts", {})
    attendee_names = parsed_event.get("attendees", [])
    
    if attendee_names:
        resolved = resolve_attendee_emails(attendee_names, user_contacts)
        parsed_event["resolved_attendees"] = resolved
    
    # Get color ID for category
    category = parsed_event.get("category", "other")
    color_map = user.get("calendar_config", {}).get("color_map", {})
    color_id = calendar_service.get_color_id_for_category(category, color_map)
    
    # Create event
    created_event = calendar_service.add_event(
        user_tokens=tokens,
        event_data=parsed_event,
        color_id=color_id
    )
    
    if created_event:
        event_link = created_event.get("htmlLink", "")
        summary = parsed_event.get("summary", "אירוע")
        
        # Format success message
        confirmation = await llm_service.confirm_event_details(parsed_event)
        
        await message.answer(
            f"✅ *האירוע נוצר בהצלחה!*\n\n"
            f"{confirmation}\n"
            f"[פתח ביומן]({event_link})",
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
    else:
        await message.answer(
            "❌ שגיאה ביצירת האירוע.\n"
            "נסה שוב או בדוק את ההרשאות."
        )


# =============================================================================
# Missing Contact Email Handler
# =============================================================================

@router.message(EventFlowStates.WAITING_FOR_MISSING_CONTACT_EMAIL)
async def handle_missing_contact_email(
    message: Message,
    state: FSMContext,
    user: Optional[UserData]
) -> None:
    """
    Handle user providing email for a missing contact.
    """
    email = message.text.strip()
    user_id = message.from_user.id
    
    # Validate email format
    if not is_valid_email(email):
        await message.answer(
            "❌ זה לא נראה כמו מייל תקין.\n"
            "נסה שוב, למשל: example@gmail.com"
        )
        return
    
    # Get pending event data
    data = await state.get_data()
    pending_event = data.get("pending_event")
    missing_name = data.get("missing_contact_name")
    remaining_missing = data.get("remaining_missing", [])
    
    if not pending_event or not missing_name:
        await state.clear()
        await message.answer("🤔 משהו השתבש. נסה שוב.")
        return
    
    # Update user's contacts in Firestore
    firestore_service.update_user(user_id, {
        f"contacts.{missing_name}": email
    })
    
    print(f"[Event] Added contact {missing_name}: {email} for user {user_id}")
    
    await message.answer(f"✅ הוספתי את {missing_name} לאנשי הקשר!")
    
    # Check if there are more missing contacts
    if remaining_missing:
        next_missing = remaining_missing[0]
        await state.update_data(
            missing_contact_name=next_missing,
            remaining_missing=remaining_missing[1:]
        )
        
        await message.answer(
            f"👤 מה המייל של *{next_missing}*?",
            parse_mode="Markdown"
        )
        return
    
    # All contacts resolved - update the pending event with new contact
    user_contacts = user.get("contacts", {}) if user else {}
    user_contacts[missing_name] = email  # Add the new contact
    
    # Re-resolve attendees with updated contacts
    attendee_names = pending_event.get("attendees", [])
    resolved = resolve_attendee_emails(attendee_names, user_contacts)
    pending_event["resolved_attendees"] = resolved
    
    # Clear state
    await state.clear()
    
    # Get fresh user data with updated contacts
    fresh_user = firestore_service.get_user(user_id)
    if not fresh_user:
        await message.answer("❌ שגיאה בטעינת הנתונים. נסה שוב.")
        return
    
    # Create the event
    await create_event_from_parsed(message, fresh_user, pending_event)


# =============================================================================
# Event Confirmation Handlers (Optional - for future use)
# =============================================================================

@router.callback_query(EventFlowStates.WAITING_FOR_EVENT_CONFIRMATION, F.data == "event_confirm_yes")
async def confirm_event_creation(callback: CallbackQuery, state: FSMContext, user: Optional[UserData]) -> None:
    """Handle event confirmation - create the event."""
    await callback.answer()
    
    data = await state.get_data()
    pending_event = data.get("pending_event")
    
    if not pending_event or not user:
        await callback.message.edit_text("🤔 משהו השתבש. נסה שוב.")
        await state.clear()
        return
    
    await callback.message.edit_text("⏳ יוצר את האירוע...")
    await create_event_from_parsed(callback.message, user, pending_event)
    await state.clear()


@router.callback_query(EventFlowStates.WAITING_FOR_EVENT_CONFIRMATION, F.data == "event_confirm_no")
async def cancel_event_creation(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle event cancellation."""
    await callback.answer()
    await callback.message.edit_text("❌ האירוע בוטל.")
    await state.clear()
