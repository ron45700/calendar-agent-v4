"""
Event creation handlers for Agentic Calendar 2.0
Handles calendar event creation from parsed intent payloads.
"""

import re
import asyncio
from datetime import datetime
from typing import Optional, Dict, List, Any
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from models.user import UserData
from services.llm_service import llm_service
from services.calendar_service import (
    calendar_service, ERROR_AUTH_REQUIRED, ERROR_GENERIC,
    CALENDAR_COLORS, COLOR_ID_EMOJI, DEFAULT_EVENT_EMOJI
)
from services.firestore_service import firestore_service
from bot.states import EventFlowStates, DeleteFlowStates
from bot.utils import get_formatted_current_time
from config import WEBAPP_URL

import logging
logger = logging.getLogger(__name__)


# =============================================================================
# Hebrew → Canonical Google Color Name Translation
# =============================================================================
# The LLM may output Hebrew color names or informal English.
# This map normalizes them to the canonical CALENDAR_COLORS keys.

HEBREW_COLOR_MAP = {
    # Hebrew → canonical
    "לבנדר": "lavender", "סגול בהיר": "lavender",
    "ירוק מרווה": "sage", "מנטה": "sage",
    "סגול": "grape", "סגול כהה": "grape",
    "ורוד": "flamingo", "פלמינגו": "flamingo",
    "צהוב": "banana", "בננה": "banana",
    "כתום": "tangerine", "תפוז": "tangerine",
    "תכלת": "peacock", "כחול בהיר": "peacock", "טורקיז": "peacock", "cyan": "peacock",
    "אפור": "graphite", "גרפיט": "graphite",
    "כחול": "blueberry", "כחול כהה": "blueberry", "blue": "blueberry",
    "ירוק": "basil", "ירוק כהה": "basil", "green": "basil",
    "אדום": "tomato", "אדום כהה": "tomato", "red": "tomato",
}


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
    
    Uses STRICT EXACT MATCHING to prevent false positives.
    "Revach" ≠ "Roy", "Dan" ≠ "Daniel"
    """
    missing = []
    # Create case-insensitive lookup for exact matches only
    contact_names_lower = {name.lower().strip(): name for name in user_contacts.keys()}
    
    for name in attendee_names:
        name_lower = name.lower().strip()
        # STRICT: Exact match only
        if name_lower not in contact_names_lower:
            missing.append(name)
    
    return missing


def resolve_attendee_emails(
    attendee_names: List[str],
    user_contacts: Dict[str, str]
) -> List[Dict[str, str]]:
    """
    Resolve attendee names to emails from user's contact list.
    
    Uses STRICT EXACT MATCHING to prevent false positives.
    Only resolves if the name is an exact match (case-insensitive).
    """
    resolved = []
    # Create case-insensitive lookup for exact matches only
    contact_names_lower = {
        name.lower().strip(): (name, email) 
        for name, email in user_contacts.items()
    }
    
    for name in attendee_names:
        name_lower = name.lower().strip()
        # STRICT: Exact match only
        if name_lower in contact_names_lower:
            contact_name, email = contact_names_lower[name_lower]
            resolved.append({"name": contact_name, "email": email})
    
    return resolved


# =============================================================================
# Event Creation from Intent Payload
# =============================================================================

async def process_create_event(
    message: Message,
    user: UserData,
    state: FSMContext,
    payload: Dict[str, Any],
    response_text: str
) -> None:
    """
    Process create_event intent from LLM classification.
    
    Args:
        message: Telegram message
        user: User data from Firestore
        state: FSM context
        payload: Event payload from LLM intent
        response_text: Natural response from LLM
    """
    user_id = message.from_user.id
    user_contacts = user.get("contacts", {})
    
    # Check for missing contacts
    attendee_names = payload.get("attendees", [])
    
    if attendee_names:
        missing_contacts = find_missing_contacts(attendee_names, user_contacts)
        
        if missing_contacts:
            # Stop flow - need email for missing contact
            missing_name = missing_contacts[0]
            
            # Save pending event data to FSM
            await state.update_data(
                pending_event=payload,
                missing_contact_name=missing_name,
                remaining_missing=missing_contacts[1:] if len(missing_contacts) > 1 else [],
                original_response=response_text
            )
            
            await state.set_state(EventFlowStates.WAITING_FOR_MISSING_CONTACT_EMAIL)
            
            ask_email_msg = (
                f"👤 שמתי לב שביקשת להזמין את *{missing_name}*,\n"
                f"אבל אין לי את המייל שלו.\n\n"
                f"מה המייל של {missing_name}?"
            )
            
            # Save to history
            firestore_service.save_message(user_id, "assistant", ask_email_msg)
            
            await message.answer(ask_email_msg, parse_mode="Markdown")
            return
    
    # All contacts resolved - create event
    await create_event_from_payload(message, user, payload, response_text)


async def create_event_from_payload(
    message: Message,
    user: UserData,
    payload: Dict[str, Any],
    response_text: str
) -> None:
    """
    Create Google Calendar event from intent payload.
    """
    user_id = message.from_user.id
    
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
    attendee_names = payload.get("attendees", [])
    
    if attendee_names:
        resolved = resolve_attendee_emails(attendee_names, user_contacts)
        payload["resolved_attendees"] = resolved
    
    # Color hierarchy: Explicit Name > Payload ID > User Prefs > Default (Tangerine)
    category = payload.get("category", "general")
    color_map = user.get("calendar_config", {}).get("color_map", {})
    color_name = payload.get("color_name")
    color_id = None
    color_source = "default"  # For debug logging
    
    # 1. Explicit color name from LLM (highest priority)
    if color_name:
        # Normalize: try Hebrew→canonical translation, then direct lookup
        canonical = HEBREW_COLOR_MAP.get(color_name, color_name)
        color_id = CALENDAR_COLORS.get(canonical)
        if color_id:
            color_source = f"explicit '{color_name}' → '{canonical}' → {color_id}"
        else:
            logger.warning(f"[Color] Unknown color name '{color_name}' (canonical: '{canonical}')")
    
    # 2. Fallback to payload color_id
    if not color_id and payload.get("color_id"):
        color_id = payload.get("color_id")
        color_source = f"payload color_id={color_id}"
    
    # 3. Fallback to user's custom category preferences
    if not color_id and color_map and category in color_map:
        color_id = color_map[category]
        color_source = f"user prefs '{category}' → {color_id}"
    
    # 4. Final fallback: default Tangerine (only if nothing else matched)
    if not color_id:
        from services.calendar_service import DEFAULT_COLOR_ID
        color_id = DEFAULT_COLOR_ID
        color_source = f"default Tangerine ({DEFAULT_COLOR_ID})"
    
    logger.info(f"[Color] Resolved: {color_source}")
    
    # Create event - pass user_id for auth cleanup on failure
    result = calendar_service.add_event(
        user_tokens=tokens,
        event_data=payload,
        color_id=int(color_id) if color_id else None,
        user_id=str(user_id)
    )
    
    # Check result status - CRITICAL: Don't lie to user!
    if result.get("status") != "success":
        error_type = result.get("type", ERROR_GENERIC)
        print(f"[Event] ❌ add_event failed with type: {error_type}")
        
        if error_type == ERROR_AUTH_REQUIRED:
            # Auth failed - credentials cleared, need re-login
            # Use simple text to avoid Markdown parsing issues
            auth_link = f"{WEBAPP_URL}/auth?user_id={user_id}"
            error_response = (
                "🔐 החיבור ליומן התנתק\n\n"
                "מטעמי אבטחה, Google מנתק את החיבור מדי פעם.\n\n"
                "שלח /auth להתחברות מחדש."
            )
            # Send without Markdown to avoid parsing issues
            firestore_service.save_message(user_id, "assistant", error_response)
            await message.answer(error_response)
        else:
            # Generic Error - SANITIZED: Never show raw error to user
            error_response = (
                "❌ נתקלתי בשגיאה טכנית\n\n"
                "לא הצלחתי ליצור את האירוע כרגע.\n"
                "נסה שוב מאוחר יותר."
            )
            # Send without Markdown to avoid parsing issues
            firestore_service.save_message(user_id, "assistant", error_response)
            await message.answer(error_response)
        return
    
    # SUCCESS - event was created
    created_event = result.get("event", {})
    event_link = created_event.get("htmlLink", "")
    summary = payload.get("summary", "אירוע")
    
    # Format success message
    confirmation = await llm_service.confirm_event_details(payload)
    
    success_response = (
        f"✅ האירוע נוצר בהצלחה!\n\n"
        f"{confirmation}\n"
        f"פתח ביומן: {event_link}"
    )
    
    # Save assistant response to history
    firestore_service.save_message(user_id, "assistant", success_response)
    
    # Send without Markdown to be safe
    await message.answer(success_response, disable_web_page_preview=True)


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
    
    # Save this message to history
    firestore_service.save_message(user_id, "user", email)
    
    # --- CANCEL vs SKIP detection (strict separation) ---
    CANCEL_PHRASES = {"בטל", "בטל אירוע", "עזוב", "לא משנה", "תעצור", "cancel", "stop", "abort"}
    SKIP_PHRASES = {"לא צריך", "בלי הזמנה", "בלי", "בלעדיו", "רק תרשום", "דלג", "תדלג", "skip", "no invite", "without email", "no need", "לא"}
    text_lower = email.lower().strip()
    
    # CANCEL → Abort entire event creation
    if text_lower in CANCEL_PHRASES:
        await state.clear()
        cancel_msg = "❌ האירוע בוטל."
        firestore_service.save_message(user_id, "assistant", cancel_msg)
        await message.answer(cancel_msg)
        return
    
    # SKIP → Drop this invite, still create the event
    if text_lower in SKIP_PHRASES:
        data = await state.get_data()
        pending_event = data.get("pending_event", {})
        missing_name = data.get("missing_contact_name", "")
        remaining = data.get("remaining_missing", [])
        original_response = data.get("original_response", "")
        
        # Remove skipped attendee
        attendees = pending_event.get("attendees", [])
        pending_event["attendees"] = [a for a in attendees if a != missing_name]
        
        skip_msg = f"👌 סבבה, יוצר בלי הזמנה ל{missing_name}."
        firestore_service.save_message(user_id, "assistant", skip_msg)
        await message.answer(skip_msg)
        
        if remaining:
            next_missing = remaining[0]
            await state.update_data(
                pending_event=pending_event,
                missing_contact_name=next_missing,
                remaining_missing=remaining[1:]
            )
            ask_msg = f"👤 מה המייל של *{next_missing}*?"
            firestore_service.save_message(user_id, "assistant", ask_msg)
            await message.answer(ask_msg, parse_mode="Markdown")
            return
        
        # All done — create event without the skipped invite
        await state.clear()
        fresh_user = firestore_service.get_user(user_id) or user
        await create_event_from_payload(message, fresh_user, pending_event, original_response)
        return
    
    # Validate email format
    if not is_valid_email(email):
        error_msg = (
            "❌ זה לא נראה כמו מייל תקין.\n"
            "נסה שוב, למשל: example@gmail.com"
        )
        firestore_service.save_message(user_id, "assistant", error_msg)
        await message.answer(error_msg)
        return
    
    # Get pending event data
    data = await state.get_data()
    pending_event = data.get("pending_event")
    missing_name = data.get("missing_contact_name")
    remaining_missing = data.get("remaining_missing", [])
    original_response = data.get("original_response", "")
    
    if not pending_event or not missing_name:
        await state.clear()
        await message.answer("🤔 משהו השתבש. נסה שוב.")
        return
    
    # Update user's contacts in Firestore
    firestore_service.update_user(user_id, {
        f"contacts.{missing_name}": email
    })
    
    print(f"[Event] Added contact {missing_name}: {email} for user {user_id}")
    
    confirm_msg = f"✅ הוספתי את {missing_name} לאנשי הקשר!"
    firestore_service.save_message(user_id, "assistant", confirm_msg)
    await message.answer(confirm_msg)
    
    # Check if there are more missing contacts
    if remaining_missing:
        next_missing = remaining_missing[0]
        await state.update_data(
            missing_contact_name=next_missing,
            remaining_missing=remaining_missing[1:]
        )
        
        ask_msg = f"👤 מה המייל של *{next_missing}*?"
        firestore_service.save_message(user_id, "assistant", ask_msg)
        await message.answer(ask_msg, parse_mode="Markdown")
        return
    
    # All contacts resolved - update the pending event with new contact
    user_contacts = user.get("contacts", {}) if user else {}
    user_contacts[missing_name] = email
    
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
    await create_event_from_payload(message, fresh_user, pending_event, original_response)


# =============================================================================
# Event Confirmation Handlers (Optional - for future use)
# =============================================================================

@router.callback_query(EventFlowStates.WAITING_FOR_EVENT_CONFIRMATION, F.data == "event_confirm_yes")
async def confirm_event_creation(callback: CallbackQuery, state: FSMContext, user: Optional[UserData]) -> None:
    """Handle event confirmation - create the event."""
    await callback.answer()
    
    data = await state.get_data()
    pending_event = data.get("pending_event")
    original_response = data.get("original_response", "")
    
    if not pending_event or not user:
        await callback.message.edit_text("🤔 משהו השתבש. נסה שוב.")
        await state.clear()
        return
    
    await callback.message.edit_text("⏳ יוצר את האירוע...")
    await create_event_from_payload(callback.message, user, pending_event, original_response)
    await state.clear()


@router.callback_query(EventFlowStates.WAITING_FOR_EVENT_CONFIRMATION, F.data == "event_confirm_no")
async def cancel_event_creation(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle event cancellation."""
    await callback.answer()
    await callback.message.edit_text("❌ האירוע בוטל.")
    await state.clear()


# =============================================================================
# Update Event Handler
# =============================================================================

# Helper: color ID → Hebrew name mapping
COLOR_ID_HEBREW = {
    1: "לבנדר", 2: "ירוק מרווה", 3: "סגול", 4: "פלמינגו",
    5: "בננה", 6: "כתום", 7: "תכלת", 8: "גרפיט",
    9: "כחול", 10: "ירוק", 11: "אדום"
}

# Helper: format a Google Calendar event datetime for display
def _format_event_time(event: Dict[str, Any]) -> str:
    """Format event start time for Hebrew display."""
    start_raw = event.get("start", {})
    if "dateTime" in start_raw:
        dt = datetime.fromisoformat(start_raw["dateTime"])
        day_names = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
        day_name = day_names[dt.weekday()]
        return f"יום {day_name} {dt.strftime('%d/%m')} ב-{dt.strftime('%H:%M')}"
    elif "date" in start_raw:
        return "כל היום"
    return "לא ידוע"


async def process_update_event(
    message: Message,
    user: UserData,
    state: FSMContext,
    payload: Dict[str, Any],
    response_text: str
) -> None:
    """
    Process update_event intent: search → find → patch → show Before/After diff.
    """
    user_id = message.from_user.id
    
    # Get tokens
    tokens = get_user_tokens(user)
    if not tokens:
        await message.answer("🔐 ההרשאה שלך פגה.\nשלח /auth כדי להתחבר מחדש.")
        return
    
    # Extract search hint
    hint = payload.get("original_event_hint", "")
    if not hint:
        await message.answer("🤔 לא הבנתי איזה אירוע לעדכן. נסה שוב עם שם האירוע.")
        return
    
    # Search for the event
    logger.info(f"[Update] Searching for event: '{hint}'")
    try:
        result = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, lambda: calendar_service.search_events(
                    tokens, query=hint, user_id=str(user_id)
                )
            ), timeout=10
        )
    except asyncio.TimeoutError:
        await message.answer("⏳ Google Calendar לא הגיב בזמן. נסה שוב.")
        return
    except Exception as e:
        logger.error(f"[Update] Search error: {e}")
        await message.answer("❌ שגיאה בחיפוש האירוע. נסה שוב.")
        return
    
    if result.get("status") != "success":
        if result.get("type") == ERROR_AUTH_REQUIRED:
            await message.answer("🔐 ההרשאה שלך פגה.\nשלח /auth כדי להתחבר מחדש.")
        else:
            await message.answer("❌ שגיאה בחיפוש האירוע. נסה שוב.")
        return
    
    events = result.get("events", [])
    
    # --- Handle match count ---
    if len(events) == 0:
        no_match_msg = (
            f"לא מצאתי אירוע בשם '{hint}' ביומן שלך 🤔\n"
            f"נסה לתת לי שם מדויק יותר או תאריך."
        )
        firestore_service.save_message(user_id, "assistant", no_match_msg)
        await message.answer(no_match_msg)
        return
    
    if len(events) > 1:
        # Multiple matches — ask user to clarify
        lines = ["מצאתי כמה אירועים שמתאימים:\n"]
        for i, ev in enumerate(events[:5], 1):  # Cap at 5
            summary = ev.get("summary", "ללא שם")
            time_str = _format_event_time(ev)
            lines.append(f"{i}️⃣ {summary} - {time_str}")
        lines.append("\nאיזה מהם לעדכן?")
        multi_msg = "\n".join(lines)
        firestore_service.save_message(user_id, "assistant", multi_msg)
        await message.answer(multi_msg)
        return
    
    # --- Exactly 1 match: execute the update ---
    target_event = events[0]
    event_id = target_event.get("id")
    old_summary = target_event.get("summary", "ללא שם")
    old_time_str = _format_event_time(target_event)
    old_color_id = target_event.get("colorId", "")
    old_location = target_event.get("location", "")
    
    # Build updates dict for calendar_service.update_event
    updates = {}
    diff_lines = []  # For Before→After display
    
    # Title change
    if payload.get("new_summary"):
        updates["summary"] = payload["new_summary"]
        diff_lines.append(
            f"📝 שם האירוע:\n"
            f"  ⬅️ {old_summary}\n"
            f"  ➡️ {payload['new_summary']}"
        )
    
    # Time change
    if payload.get("new_start_time"):
        updates["start_time"] = payload["new_start_time"]
        if payload.get("new_end_time"):
            updates["end_time"] = payload["new_end_time"]
        # Format new time for display
        try:
            new_dt = datetime.fromisoformat(payload["new_start_time"])
            day_names = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
            new_day = day_names[new_dt.weekday()]
            new_time_str = f"יום {new_day} {new_dt.strftime('%d/%m')} ב-{new_dt.strftime('%H:%M')}"
        except:
            new_time_str = payload["new_start_time"]
        
        diff_lines.append(
            f"⏰ מועד:\n"
            f"  ⬅️ {old_time_str}\n"
            f"  ➡️ {new_time_str}"
        )
    
    # Color change
    if payload.get("new_color_name"):
        new_color_id = CALENDAR_COLORS.get(payload["new_color_name"])
        if new_color_id:
            updates["color_id"] = new_color_id
            old_emoji = COLOR_ID_EMOJI.get(str(old_color_id), DEFAULT_EVENT_EMOJI)
            new_emoji = COLOR_ID_EMOJI.get(str(new_color_id), DEFAULT_EVENT_EMOJI)
            old_color_heb = COLOR_ID_HEBREW.get(int(old_color_id) if old_color_id else 0, "ברירת מחדל")
            new_color_heb = payload.get("new_color_name_hebrew", COLOR_ID_HEBREW.get(new_color_id, "?"))
            diff_lines.append(
                f"🎨 צבע:\n"
                f"  ⬅️ {old_emoji} {old_color_heb}\n"
                f"  ➡️ {new_emoji} {new_color_heb}"
            )
    
    # Location change
    if payload.get("new_location"):
        updates["location"] = payload["new_location"]
        old_loc_display = old_location if old_location else "ללא מיקום"
        diff_lines.append(
            f"📍 מיקום:\n"
            f"  ⬅️ {old_loc_display}\n"
            f"  ➡️ {payload['new_location']}"
        )
    
    # Attendees change
    if payload.get("new_attendees"):
        user_contacts = user.get("contacts", {})
        resolved = resolve_attendee_emails(payload["new_attendees"], user_contacts)
        if resolved:
            # Merge with existing attendees
            existing_attendees = target_event.get("attendees", [])
            merged = list(existing_attendees)  # Keep existing
            existing_emails = {a.get("email", "").lower() for a in existing_attendees}
            for att in resolved:
                if att["email"].lower() not in existing_emails:
                    merged.append({"email": att["email"], "displayName": att.get("name", "")})
            updates["attendees"] = merged
            names = ", ".join(a.get("name", a["email"]) for a in resolved)
            diff_lines.append(f"👥 משתתפים:\n  ➕ {names} נוסף/ו לאירוע")
    
    if not updates:
        await message.answer("🤔 לא הבנתי מה לשנות. נסה לפרט מה לעדכן.")
        return
    
    # Execute the update
    logger.info(f"[Update] Patching event {event_id}: {list(updates.keys())}")
    try:
        update_result = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, lambda: calendar_service.update_event(
                    tokens, event_id=event_id, updates=updates, user_id=str(user_id)
                )
            ), timeout=10
        )
    except asyncio.TimeoutError:
        await message.answer("⏳ Google Calendar לא הגיב בזמן. נסה שוב.")
        return
    except Exception as e:
        logger.error(f"[Update] API error: {e}")
        await message.answer("❌ שגיאה בעדכון האירוע. נסה שוב.")
        return
    
    if update_result.get("status") != "success":
        if update_result.get("type") == ERROR_AUTH_REQUIRED:
            await message.answer("🔐 ההרשאה שלך פגה.\nשלח /auth כדי להתחבר מחדש.")
        else:
            error_msg = update_result.get("message", "שגיאה לא ידועה")
            await message.answer(f"❌ {error_msg}")
        return
    
    # SUCCESS — build the Before→After diff message
    diff_display = "\n\n".join(diff_lines)
    success_msg = f"✅ האירוע עודכן בהצלחה!\n\n{diff_display}\n\nעוד שינוי? 😎"
    
    firestore_service.save_message(user_id, "assistant", success_msg)
    await message.answer(success_msg)


# =============================================================================
# Delete Event Handler (Phase 1: Search + Confirm)
# =============================================================================

async def process_delete_event(
    message: Message,
    user: UserData,
    state: FSMContext,
    payload: Dict[str, Any],
    response_text: str
) -> None:
    """
    Process delete_event intent: search → find → ask confirmation → wait for FSM.
    Does NOT delete immediately — enters WAITING_FOR_DELETE_CONFIRM state.
    """
    user_id = message.from_user.id
    
    # Get tokens
    tokens = get_user_tokens(user)
    if not tokens:
        await message.answer("🔐 ההרשאה שלך פגה.\nשלח /auth כדי להתחבר מחדש.")
        return
    
    # Extract search hint
    hint = payload.get("original_event_hint", "")
    if not hint:
        await message.answer("🤔 לא הבנתי איזה אירוע למחוק. נסה שוב עם שם האירוע.")
        return
    
    # Search for the event
    logger.info(f"[Delete] Searching for event: '{hint}'")
    try:
        result = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, lambda: calendar_service.search_events(
                    tokens, query=hint, user_id=str(user_id)
                )
            ), timeout=10
        )
    except asyncio.TimeoutError:
        await message.answer("⏳ Google Calendar לא הגיב בזמן. נסה שוב.")
        return
    except Exception as e:
        logger.error(f"[Delete] Search error: {e}")
        await message.answer("❌ שגיאה בחיפוש האירוע. נסה שוב.")
        return
    
    if result.get("status") != "success":
        if result.get("type") == ERROR_AUTH_REQUIRED:
            await message.answer("🔐 ההרשאה שלך פגה.\nשלח /auth כדי להתחבר מחדש.")
        else:
            await message.answer("❌ שגיאה בחיפוש האירוע. נסה שוב.")
        return
    
    events = result.get("events", [])
    
    # --- Handle match count ---
    if len(events) == 0:
        no_match_msg = (
            f"לא מצאתי אירוע בשם '{hint}' ביומן שלך 🤔\n"
            f"אפשר לנסות שם אחר או תאריך מדויק יותר?"
        )
        firestore_service.save_message(user_id, "assistant", no_match_msg)
        await message.answer(no_match_msg)
        return
    
    if len(events) > 1:
        # Multiple matches — ask user to clarify
        lines = [f"מצאתי כמה אירועים שמתאימים ל'{hint}':\n"]
        for i, ev in enumerate(events[:5], 1):
            summary = ev.get("summary", "ללא שם")
            time_str = _format_event_time(ev)
            lines.append(f"{i}️⃣ {summary} - {time_str}")
        lines.append("\nאיזה מהם למחוק?")
        multi_msg = "\n".join(lines)
        firestore_service.save_message(user_id, "assistant", multi_msg)
        await message.answer(multi_msg)
        return
    
    # --- Exactly 1 match: ask for confirmation (Phase 1) ---
    target_event = events[0]
    event_id = target_event.get("id")
    summary = target_event.get("summary", "ללא שם")
    time_str = _format_event_time(target_event)
    location = target_event.get("location", "")
    attendees = target_event.get("attendees", [])
    
    # Build confirmation message
    confirm_lines = [
        "🗑️ מצאתי את האירוע הזה:\n",
        f"📌 *{summary}*",
        f"⏰ {time_str}",
    ]
    if location:
        confirm_lines.append(f"📍 {location}")
    if attendees:
        att_names = ", ".join(a.get("displayName", a.get("email", "")) for a in attendees[:5])
        confirm_lines.append(f"👥 {att_names}")
    confirm_lines.append("")
    confirm_lines.append("⚠️ *בטוח שאתה רוצה למחוק את האירוע הזה?*")
    confirm_lines.append("(כתוב *כן* למחיקה או *לא* לביטול)")
    
    confirm_msg = "\n".join(confirm_lines)
    
    # Save event data to FSM for Phase 2
    await state.update_data(
        delete_event_id=event_id,
        delete_event_summary=summary,
        delete_event_time=time_str
    )
    await state.set_state(DeleteFlowStates.WAITING_FOR_DELETE_CONFIRM)
    
    firestore_service.save_message(user_id, "assistant", confirm_msg)
    await message.answer(confirm_msg, parse_mode="Markdown")


# =============================================================================
# Delete Confirmation Handler (Phase 2: Execute or Cancel)
# =============================================================================

# Hebrew confirmation/cancellation keywords
DELETE_CONFIRM_PHRASES = {"כן", "בטוח", "מחק", "תמחק", "yes", "כן בטוח", "מחק את זה", "כן תמחק"}
DELETE_CANCEL_PHRASES = {"לא", "ביטול", "תעזוב", "עזוב", "no", "cancel", "אל תמחק", "בטל","לא משנה"}


@router.message(DeleteFlowStates.WAITING_FOR_DELETE_CONFIRM)
async def handle_delete_confirmation(
    message: Message,
    state: FSMContext,
    user: Optional[UserData]
) -> None:
    """
    Handle user's Yes/No response to delete confirmation.
    Phase 2 of the 2-step deletion FSM.
    """
    user_id = message.from_user.id
    text = message.text.strip().lower() if message.text else ""
    
    # Save user message to history
    firestore_service.save_message(user_id, "user", message.text or "")
    
    data = await state.get_data()
    event_id = data.get("delete_event_id")
    event_summary = data.get("delete_event_summary", "האירוע")
    event_time = data.get("delete_event_time", "")
    
    if not event_id:
        await state.clear()
        await message.answer("🤔 משהו השתבש. נסה שוב.")
        return
    
    # --- User CANCELS ---
    if text in DELETE_CANCEL_PHRASES:
        await state.clear()
        cancel_msg = f"👍 ביטלתי! האירוע *'{event_summary}'* נשמר ביומן שלך. בטוח שלך!"
        firestore_service.save_message(user_id, "assistant", cancel_msg)
        await message.answer(cancel_msg, parse_mode="Markdown")
        return
    
    # --- User CONFIRMS ---
    if text in DELETE_CONFIRM_PHRASES:
        # Get tokens
        tokens = get_user_tokens(user) if user else None
        if not tokens:
            await state.clear()
            await message.answer("🔐 ההרשאה שלך פגה.\nשלח /auth כדי להתחבר מחדש.")
            return
        
        # Execute deletion
        logger.info(f"[Delete] Confirmed! Deleting event {event_id}")
        try:
            delete_result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, lambda: calendar_service.delete_event(
                        tokens, event_id=event_id, user_id=str(user_id)
                    )
                ), timeout=10
            )
        except asyncio.TimeoutError:
            await state.clear()
            await message.answer("⏳ Google Calendar לא הגיב בזמן. נסה שוב.")
            return
        except Exception as e:
            logger.error(f"[Delete] API error: {e}")
            await state.clear()
            await message.answer("❌ שגיאה במחיקת האירוע. נסה שוב.")
            return
        
        await state.clear()
        
        if delete_result.get("status") == "success":
            success_msg = (
                f"✅ האירוע *'{event_summary}'* נמחק מהיומן.\n"
                f"אם מחקת בטעות, תמיד אפשר ליצור אותו מחדש 📅"
            )
            firestore_service.save_message(user_id, "assistant", success_msg)
            await message.answer(success_msg, parse_mode="Markdown")
        elif delete_result.get("type") == ERROR_AUTH_REQUIRED:
            await message.answer("🔐 ההרשאה שלך פגה.\nשלח /auth כדי להתחבר מחדש.")
        else:
            await message.answer("❌ שגיאה במחיקת האירוע. נסה שוב.")
        return
    
    # --- Unrecognized input ---
    unclear_msg = "לא הבנתי 🤔 כתוב *כן* כדי למחוק או *לא* כדי לבטל."
    firestore_service.save_message(user_id, "assistant", unclear_msg)
    await message.answer(unclear_msg, parse_mode="Markdown")
