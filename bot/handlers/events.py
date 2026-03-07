"""
Event creation handlers for Agentic Calendar 2.0
Handles calendar event creation from parsed intent payloads.
"""

import re
import asyncio
from datetime import datetime, timedelta
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
from bot.states import EventFlowStates, DeleteFlowStates, UpdateFlowStates, RecurrenceFlowStates
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
# Core Event Engine (shared by single and multi-event flows)
# =============================================================================

async def _process_event_core(
    ev_payload: Dict[str, Any],
    tokens: Dict[str, str],
    user_id: int,
    user: UserData,
) -> Dict[str, Any]:
    """
    Single source of truth for all event creation logic.

    Handles:
    - Reminder Mode: prefix + Tangerine color override
    - Color resolution: explicit → payload → user prefs → default
    - Attendee email resolution
    - All-day end_time guard
    - calendar_service.add_event call

    Returns a result dict:
        {
          "status": "success" | "error" | "auth",
          "summary": str,
          "color_hebrew": str,
          "color_id": int | None,
          "event_link": str,
          "day_str": str,
          "raw_result": dict,   # full API response
        }
    """
    from services.calendar_service import DEFAULT_COLOR_ID

    summary = ev_payload.get("summary", "אירוע")
    user_contacts = user.get("contacts", {})

    # --- 1. Reminder Mode: prefix + force Tangerine -------------------------
    if ev_payload.get("original_intent") == "set_reminder":
        reminder_mode_on = user.get("preferences", {}).get("reminder_mode", False)
        if reminder_mode_on:
            if not summary.startswith("תזכורת: "):
                summary = f"תזכורת: {summary}"
                ev_payload["summary"] = summary
            ev_payload["color_name"] = "tangerine"
            logger.info(f"[Reminder] Mode ON — applied prefix + tangerine to: {summary}")

    # --- 2. Attendee email resolution ---------------------------------------
    attendee_names = ev_payload.get("attendees", [])
    if attendee_names:
        resolved = resolve_attendee_emails(attendee_names, user_contacts)
        ev_payload["resolved_attendees"] = resolved

    # --- 3. Color resolution ------------------------------------------------
    category = ev_payload.get("category", "general")
    color_map = user.get("calendar_config", {}).get("color_map", {})
    color_name = ev_payload.get("color_name")
    color_id = None
    color_source = "default"

    if color_name:
        canonical = HEBREW_COLOR_MAP.get(color_name, color_name)
        color_id = CALENDAR_COLORS.get(canonical)
        if color_id:
            color_source = f"explicit '{color_name}' → '{canonical}' → {color_id}"
        else:
            logger.warning(f"[Color] Unknown color name '{color_name}' (canonical: '{canonical}')")

    if not color_id and ev_payload.get("color_id"):
        color_id = ev_payload.get("color_id")
        color_source = f"payload color_id={color_id}"

    if not color_id and color_map and category in color_map:
        color_id = color_map[category]
        color_source = f"user prefs '{category}' → {color_id}"

    if not color_id:
        color_id = DEFAULT_COLOR_ID
        color_source = f"default Tangerine ({DEFAULT_COLOR_ID})"

    logger.info(f"[Color] Resolved: {color_source}")

    # Map the final color_id back to a Hebrew display name
    COLOR_ID_TO_HEBREW = {
        1: "לבנדר", 2: "מרווה", 3: "סגול",
        4: "פלמינגו", 5: "בננה", 6: "כתום",
        7: "תכלת", 8: "אפור", 9: "כחול",
        10: "ירוק", 11: "אדום",
    }
    color_hebrew = COLOR_ID_TO_HEBREW.get(int(color_id) if color_id else 7, "תכלת")

    # --- 4. All-day guard ---------------------------------------------------
    if ev_payload.get("is_all_day") and not ev_payload.get("end_time"):
        try:
            start_date = datetime.fromisoformat(ev_payload["start_time"])
            ev_payload["end_time"] = (start_date + timedelta(days=1)).strftime("%Y-%m-%d")
            logger.info(f"[AllDay] Auto-set end_time to {ev_payload['end_time']}")
        except Exception as e:
            logger.warning(f"[AllDay] Failed to auto-set end_time: {e}")

    # --- 5. Create via Google Calendar API ---------------------------------
    try:
        raw_result = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, lambda p=ev_payload, cid=color_id: calendar_service.add_event(
                    user_tokens=tokens,
                    event_data=p,
                    color_id=int(cid) if cid else None,
                    user_id=str(user_id)
                )
            ),
            timeout=15
        )
    except asyncio.TimeoutError:
        return {"status": "timeout", "summary": summary, "color_hebrew": color_hebrew,
                "color_id": color_id, "event_link": "", "day_str": "", "raw_result": {}}
    except Exception as e:
        logger.error(f"[EventCore] Unexpected error for '{summary}': {e}")
        return {"status": "error", "summary": summary, "color_hebrew": color_hebrew,
                "color_id": color_id, "event_link": "", "day_str": "", "raw_result": {}}

    if raw_result.get("status") != "success":
        status_key = "auth" if raw_result.get("type") == ERROR_AUTH_REQUIRED else "error"
        return {"status": status_key, "summary": summary, "color_hebrew": color_hebrew,
                "color_id": color_id, "event_link": "", "day_str": "", "raw_result": raw_result}

    # --- 6. Build return dict from API response ----------------------------
    created_ev = raw_result.get("event", {})
    event_link = created_ev.get("htmlLink", "")
    start_raw = created_ev.get("start", {})
    day_str = ""
    if "dateTime" in start_raw:
        try:
            dt = datetime.fromisoformat(start_raw["dateTime"])
            day_names = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
            day_str = f"{dt.strftime('%H:%M')} יום {day_names[dt.weekday()]}"
        except Exception:
            pass
    elif "date" in start_raw:
        day_str = start_raw["date"]

    return {
        "status": "success",
        "summary": summary,
        "color_hebrew": color_hebrew,
        "color_id": color_id,
        "event_link": event_link,
        "day_str": day_str,
        "raw_result": raw_result,
    }


# =============================================================================
# Ordinal Selection Parser (used by Phase 2 FSM handlers — no LLM needed)
# =============================================================================

_ORDINAL_MAP: Dict[str, int] = {
    # Numbers
    "1": 0, "2": 1, "3": 2, "4": 3, "5": 4,
    # Hebrew ordinals
    "ראשון": 0, "הראשון": 0, "ראשונה": 0, "הראשונה": 0,
    "שני": 1, "השני": 1, "שנייה": 1, "השנייה": 1,
    "שלישי": 2, "השלישי": 2, "שלישית": 2, "השלישית": 2,
    "רביעי": 3, "הרביעי": 3, "רביעית": 3, "הרביעית": 3,
    "חמישי": 4, "החמישי": 4, "חמישית": 4, "החמישית": 4,
    # English ordinals
    "first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4,
    "1st": 0, "2nd": 1, "3rd": 2, "4th": 3, "5th": 4,
}
_ALL_PHRASES = {"כולם", "הכל", "את כולם", "כל", "כולנ", "all", "all of them", "every"}


def _parse_ordinal(text: str, max_index: int) -> Optional[Any]:
    """
    Parse a selection from the user's text.

    Returns:
        - int index (0-based) for a single selection
        - "all" for select-all phrases
        - None if not understood
    """
    clean = text.strip().lower()
    if clean in _ALL_PHRASES:
        return "all"
    # Direct ordinal lookup
    for token in clean.split():
        if token in _ORDINAL_MAP:
            idx = _ORDINAL_MAP[token]
            if idx < max_index:
                return idx
    # Also try the whole phrase
    if clean in _ORDINAL_MAP:
        idx = _ORDINAL_MAP[clean]
        if idx < max_index:
            return idx
    return None


# =============================================================================
# Phase 4: Broad Fetch & Local Filter Helper
# =============================================================================
# 
# PILLAR 4 ARCHITECTURE: "Broad Fetch & Local Filter"
# =====================================================
# 
# PROBLEM:
# Google Calendar's `q` text-search parameter is unreliable for Hebrew and 
# partial matches. Example: q="אימון ביום ראשון" returns 0 results even when
# the event exists.
#
# SOLUTION:
# 1. Extract time window from LLM payload (time_hint_from / time_hint_to)
# 2. Fetch ALL events in that narrow window (no `q` param)
# 3. Filter locally using Python substring match (case-insensitive)
#
# BENEFITS:
# - Hebrew substring matching works perfectly (Python str.lower())
# - User says "אימון" → finds "אימון כדורסל" ✅
# - Narrow time window keeps performance acceptable
# - No dependency on Google's flaky text search
#
# TIME WINDOW STRATEGY:
# - If time_from specified: start at 00:00:00 of that day
# - If time_to specified: end at 23:59:59 of that day (FULL DAY COVERAGE)
# - If no time: default to today 00:00 → +7 days 23:59:59
# - This ensures we capture ALL events on the mentioned day
#
# =============================================================================

async def _fetch_and_filter_events(
    tokens: Dict,
    hint: str,
    time_from: Optional[str],
    time_to: Optional[str],
    user_id: int,
) -> tuple:
    """
    Phase 4 core search engine: fetch ALL events in a narrow time window,
    then filter locally by keyword substring match.

    This replaces Google's `q` text-search param which is unreliable for Hebrew
    partial matches (e.g. q="אימון ביום ראשון" returns 0 results).

    Args:
        tokens:    OAuth tokens
        hint:      Short keyword from original_event_hint (event name only)
        time_from: ISO YYYY-MM-DD start (from time_hint_from; may be None)
        time_to:   ISO YYYY-MM-DD end   (from time_hint_to;   may be None)
        user_id:   Telegram user ID for auth cleanup

    Returns:
        (status, events) where status is "success", "auth", or "error"
    """
    from zoneinfo import ZoneInfo
    ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

    try:
        # Build narrow time window with FULL DAY COVERAGE
        # ================================================
        # CRITICAL: When user says "Monday", we must search the ENTIRE day
        # from 00:00:00 to 23:59:59, not just a single timestamp.
        
        if time_from:
            from datetime import date as date_cls
            d_from = date_cls.fromisoformat(time_from)
            # START: Beginning of the day (00:00:00)
            time_min = datetime(d_from.year, d_from.month, d_from.day,
                                0, 0, 0, tzinfo=ISRAEL_TZ).isoformat()
        else:
            # Default: start of today
            now = datetime.now(ISRAEL_TZ)
            time_min = datetime(now.year, now.month, now.day,
                                0, 0, 0, tzinfo=ISRAEL_TZ).isoformat()

        if time_to:
            from datetime import date as date_cls
            d_to = date_cls.fromisoformat(time_to)
            # END: End of the day (23:59:59) - captures ALL events on that day
            time_max = datetime(d_to.year, d_to.month, d_to.day,
                                23, 59, 59, tzinfo=ISRAEL_TZ).isoformat()
        else:
            # Default: 7 days ahead (narrow enough to be useful)
            now = datetime.now(ISRAEL_TZ)
            future = now + timedelta(days=7)
            time_max = datetime(future.year, future.month, future.day,
                                23, 59, 59, tzinfo=ISRAEL_TZ).isoformat()

        logger.info(f"[FetchFilter] Fetching window {time_min} → {time_max} | hint='{hint}'")

        # Broad fetch: ALL events in the window, no q param
        result = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, lambda: calendar_service.get_upcoming_events(
                    tokens, max_results=50,
                    time_min=time_min, time_max=time_max,
                    user_id=str(user_id)
                )
            ), timeout=10
        )
    except asyncio.TimeoutError:
        logger.warning("[FetchFilter] Timeout")
        return ("timeout", [])
    except Exception as e:
        logger.error(f"[FetchFilter] Exception: {e}")
        return ("error", [])

    if result.get("status") != "success":
        if result.get("type") == ERROR_AUTH_REQUIRED:
            return ("auth", [])
        return ("error", [])

    all_events = result.get("events", [])
    logger.info(f"[FetchFilter] Fetched {len(all_events)} events in window")

    # Local filter: substring match (case-insensitive)
    hint_lower = hint.strip().lower()
    if hint_lower:
        filtered = [
            ev for ev in all_events
            if hint_lower in ev.get("summary", "").lower()
        ]
    else:
        filtered = all_events  # No hint — return all in window

    logger.info(f"[FetchFilter] {len(filtered)} events after local filter")
    return ("success", filtered)


# =============================================================================
# Multi-Event Creation Handler
# =============================================================================

async def process_multi_event_creation(
    message: Message,
    user: UserData,
    state: FSMContext,
    events_batch: List[Dict[str, Any]],
    response_text: str
) -> None:
    """
    Process multiple events from a single user message (events_batch payload).

    Delegates each event to _process_event_core for consistent behaviour.
    Aggregates results into one consolidated summary message.

    Events that need FSM pauses (missing contacts, open-ended recurrence)
    are skipped with a note so the user can create them individually.
    """
    user_id = message.from_user.id
    user_contacts = user.get("contacts", {})
    tokens = get_user_tokens(user)

    if not tokens:
        await message.answer("🔐 ההרשאה שלך פגה.\nשלח /auth כדי להתחבר מחדש.")
        return

    total = len(events_batch)
    successes: List[str] = []
    failures: List[str] = []

    status_msg = await message.answer(
        f"⚡ *יוצר {total} אירועים...* רגע אחד!",
        parse_mode="Markdown"
    )

    abort_remaining = False
    for ev_payload in events_batch:
        if abort_remaining:
            break

        summary = ev_payload.get("summary", "אירוע")

        # Guard: missing date
        if not ev_payload.get("start_time"):
            logger.warning(f"[MultiEvent] Skipping '{summary}' — start_time is None")
            failures.append(f"⚠️ *{summary}* — לא צוינו תאריך ושעה (צור ידנית)")
            continue

        # Guard: missing contact email (can't pause FSM mid-batch)
        attendee_names = ev_payload.get("attendees", [])
        if attendee_names:
            missing = find_missing_contacts(attendee_names, user_contacts)
            if missing:
                failures.append(f"❌ *{summary}* — חסר מייל של {missing[0]} (צור ידנית)")
                continue

        # Guard: open-ended recurrence (can't pause FSM mid-batch)
        if ev_payload.get("recurrence_freq") and not ev_payload.get("recurrence_end_date"):
            failures.append(f"⚠️ *{summary}* — אירוע חוזר ללא תאריך סיום (צור ידנית)")
            continue

        # Delegate to core engine
        core = await _process_event_core(ev_payload, tokens, user_id, user)

        if core["status"] == "success":
            line = f"✅ *{core['summary']}*"
            if core["day_str"]:
                line += f" | {core['day_str']}"
            line += f" (צבע: {core['color_hebrew']})"
            successes.append(line)
        elif core["status"] == "auth":
            failures.append(f"🔐 *{summary}* — ההרשאה פגה, בוטלו שאר האירועים")
            abort_remaining = True
        elif core["status"] == "timeout":
            failures.append(f"⏳ *{summary}* — timeout ב-Google Calendar")
        else:
            failures.append(f"❌ *{summary}* — {core['raw_result'].get('message', 'שגיאה')}")

    # Delete status spinner
    try:
        await status_msg.delete()
    except Exception:
        pass

    # Build and send consolidated summary
    lines = [f"📋 *תוצאות יצירת {total} האירועים:*\n"]
    lines.extend(successes)
    if failures:
        if successes:
            lines.append("")
        lines.extend(failures)

    summary_msg = "\n".join(lines)
    firestore_service.save_message(user_id, "assistant", summary_msg)
    await message.answer(summary_msg, parse_mode="Markdown")

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

    # -------------------------------------------------------------------------
    # Reminder Mode Enforcement
    # If this create_event was originally a set_reminder intent AND the user
    # has reminder_mode ON, apply: prefix "תזכורת: " + force Tangerine color.
    # This is fully deterministic — no LLM involvement needed here.
    # -------------------------------------------------------------------------
    if payload.get("original_intent") == "set_reminder":
        reminder_mode_on = user.get("preferences", {}).get("reminder_mode", False)
        if reminder_mode_on:
            current_summary = payload.get("summary", "")
            if not current_summary.startswith("תזכורת: "):
                payload["summary"] = f"תזכורת: {current_summary}"
            # Force Tangerine (Orange) — the dedicated reminder color
            payload["color_name"] = "tangerine"
            logger.info(f"[Reminder] Mode ON — applied prefix + tangerine color to: {payload['summary']}")

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
    
    # Check for recurring event without end date
    recurrence_freq = payload.get("recurrence_freq")
    recurrence_end_date = payload.get("recurrence_end_date")
    
    if recurrence_freq and not recurrence_end_date:
        # Recurring event without end date - ask "until when?"
        await state.update_data(
            pending_event=payload,
            original_response=response_text
        )
        await state.set_state(RecurrenceFlowStates.WAITING_FOR_END_CONDITION)
        
        # Build frequency description in Hebrew
        freq_map = {
            "DAILY": "יומי",
            "WEEKLY": "שבועי",
            "MONTHLY": "חודשי",
            "YEARLY": "שנתי"
        }
        freq_desc = freq_map.get(recurrence_freq, "חוזר")
        interval = payload.get("recurrence_interval", 1)
        if interval > 1:
            freq_desc = f"כל {interval} {freq_desc.lower()}"
        
        ask_end_msg = (
            f"📅 שמתי לב שזה אירוע {freq_desc}.\n\n"
            f"עד מתי האירוע צריך לחזור?\n"
            f"(תן לי תאריך, למשל: 'עד סוף מרץ', 'עד ה-15/03', 'עד 31 במרץ')"
        )
        
        firestore_service.save_message(user_id, "assistant", ask_end_msg)
        await message.answer(ask_end_msg)
        return
    
    # All contacts resolved and recurrence handled - create event
    await create_event_from_payload(message, user, payload, response_text)


async def create_event_from_payload(
    message: Message,
    user: UserData,
    payload: Dict[str, Any],
    response_text: str
) -> None:
    """
    Create a single Google Calendar event from an intent payload.

    Delegates all business logic to _process_event_core().
    Handles auth tokens, missing-date guard, and sends the
    rich single-event confirmation card to Telegram.
    """
    user_id = message.from_user.id

    # Token guard
    tokens = get_user_tokens(user)
    if not tokens:
        await message.answer(
            "🔐 ההרשאה שלך פגה.\n"
            "שלח /auth כדי להתחבר מחדש."
        )
        return

    # Missing date guard (LLM hallucination protection)
    if not payload.get("start_time"):
        logger.warning("[Event] start_time is None — asking user to clarify")
        await message.answer(
            "חסרים לי פרטים על התאריך והשעה כדי לקבוע את האירוע. "
            "תוכל לפרט מתי בדיוק תרצה אותו?"
        )
        return

    # Delegate to shared core engine
    core = await _process_event_core(payload, tokens, user_id, user)

    # Handle errors
    if core["status"] == "auth":
        error_response = (
            "🔐 החיבור ליומן התנתק\n\n"
            "מטעמי אבטחה, Google מנתק את החיבור מדי פעם.\n\n"
            "שלח /auth להתחברות מחדש."
        )
        firestore_service.save_message(user_id, "assistant", error_response)
        await message.answer(error_response)
        return

    if core["status"] in ("error", "timeout"):
        error_response = (
            "❌ נתקלתי בשגיאה טכנית\n\n"
            "לא הצלחתי ליצור את האירוע כרגע.\n"
            "נסה שוב מאוחר יותר."
        )
        firestore_service.save_message(user_id, "assistant", error_response)
        await message.answer(error_response)
        return

    # SUCCESS — build confirmation card using verified values from core
    confirmation = await llm_service.confirm_event_details(payload)
    success_response = (
        f"✅ האירוע נוצר בהצלחה!\n\n"
        f"{confirmation}\n"
        f"פתח ביומן: {core['event_link']}"
    )
    firestore_service.save_message(user_id, "assistant", success_response)
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


def _format_event_card(event: dict) -> str:
    """
    Render a Google Calendar event as a unified visual card.

    Used by both process_update_event (Before-state) and process_delete_event
    (confirmation display) so the two flows look identical to the user.

    Example output:
        📌 *פגישה עם רון*
        ⏰ יום שני 10/03 ב-14:00
        📍 משרד
        👥 רון, דני
    """
    summary = event.get("summary", "ללא שם")
    time_str = _format_event_time(event)
    location = event.get("location", "")
    attendees = event.get("attendees", [])
    color_id = str(event.get("colorId", ""))

    lines = [
        f"📌 *{summary}*",
        f"⏰ {time_str}",
    ]
    if location:
        lines.append(f"📍 {location}")
    if attendees:
        att_names = ", ".join(
            a.get("displayName", a.get("email", "")) for a in attendees[:5]
        )
        lines.append(f"👥 {att_names}")
    if color_id:
        color_emoji = COLOR_ID_EMOJI.get(color_id, "")
        if color_emoji:
            lines.append(f"🎨 {color_emoji}")

    return "\n".join(lines)


async def process_multi_event_update(
    message: Message,
    user: UserData,
    state: FSMContext,
    update_batch: List[Dict[str, Any]],
    response_text: str,
) -> None:
    """
    Apply the same (or similar) update to multiple events in a single pass.

    Each item in update_batch must have:
      - original_event_hint (REQUIRED): search keyword to locate the event
      - Any update fields: new_color_name, new_summary, new_start_time, etc.

    For each item:
      - Searches calendar. Exactly 1 match → patches it.
      - 0 matches → adds to failures.
      - >1 matches → adds a clarification note (user can retry individually or via Phase 2 FSM).
    Sends one consolidated summary at the end to avoid Telegram spam.
    """
    user_id = message.from_user.id
    tokens = get_user_tokens(user)
    if not tokens:
        await message.answer("🔐 ההרשאה שלך פגה.\nשלח /auth כדי להתחבר מחדש.")
        return

    total = len(update_batch)
    successes: List[str] = []
    failures: List[str] = []

    status_msg = await message.answer(
        f"⚡ *מעדכן {total} אירועים...* רגע אחד!",
        parse_mode="Markdown"
    )

    for item in update_batch:
        hint = item.get("original_event_hint", "")
        if not hint:
            failures.append("⚠️ פריט ללא `original_event_hint` — דולג")
            continue

        time_from = item.get("time_hint_from")
        time_to = item.get("time_hint_to") or time_from

        # Phase 4: Broad fetch + local filter
        logger.info(f"[MultiUpdate] FetchFilter hint='{hint}' from={time_from} to={time_to}")
        status, events = await _fetch_and_filter_events(tokens, hint, time_from, time_to, user_id)

        if status == "auth":
            failures.append(f"🔐 *{hint}* — ההרשאה פגה")
            break
        if status in ("error", "timeout"):
            failures.append(f"❌ *{hint}* — שגיאה בחיפוש")
            continue

        if len(events) == 0:
            failures.append(f"🔍 *{hint}* — לא נמצא ביומן")
            continue
        if len(events) > 1:
            failures.append(f"⚠️ *{hint}* — נמצאו {len(events)} תוצאות, פרט יותר או עדכן ידנית")
            continue

        target_event = events[0]
        event_id = target_event.get("id")
        ev_summary = target_event.get("summary", "ללא שם")
        old_color_id = target_event.get("colorId", "")
        old_location = target_event.get("location", "")

        # 2. Build updates dict
        updates: Dict[str, Any] = {}
        if item.get("new_summary"):
            updates["summary"] = item["new_summary"]
        if item.get("new_start_time"):
            updates["start_time"] = item["new_start_time"]
            if item.get("new_end_time"):
                updates["end_time"] = item["new_end_time"]
        if item.get("new_color_name"):
            new_color_id = CALENDAR_COLORS.get(item["new_color_name"])
            if new_color_id:
                updates["color_id"] = new_color_id
        if item.get("new_location"):
            updates["location"] = item["new_location"]

        if not updates:
            failures.append(f"⚠️ *{ev_summary}* — לא זוהו שינויים")
            continue

        # 3. Apply the update
        try:
            update_result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, lambda eid=event_id, u=updates: calendar_service.update_event(
                        tokens, event_id=eid, updates=u, user_id=str(user_id)
                    )
                ), timeout=10
            )
        except asyncio.TimeoutError:
            failures.append(f"⏳ *{ev_summary}* — timeout בעדכון")
            continue
        except Exception as e:
            logger.error(f"[MultiUpdate] Update error for '{ev_summary}': {e}")
            failures.append(f"❌ *{ev_summary}* — שגיאה בעדכון")
            continue

        if update_result.get("status") == "success":
            new_color_heb = item.get("new_color_name_hebrew", "")
            color_note = f" (צבע: {new_color_heb})" if new_color_heb else ""
            successes.append(f"✅ *{ev_summary}*{color_note}")
        elif update_result.get("type") == ERROR_AUTH_REQUIRED:
            failures.append(f"🔐 *{ev_summary}* — ההרשאה פגה")
            break
        else:
            failures.append(f"❌ *{ev_summary}* — {update_result.get('message', 'שגיאה')}")

    # Delete spinner
    try:
        await status_msg.delete()
    except Exception:
        pass

    # Send consolidated summary
    lines = [f"📋 *תוצאות עדכון {total} האירועים:*\n"]
    lines.extend(successes)
    if failures:
        if successes:
            lines.append("")
        lines.extend(failures)
    summary_msg = "\n".join(lines)
    firestore_service.save_message(user_id, "assistant", summary_msg)
    await message.answer(summary_msg, parse_mode="Markdown")


async def process_multi_event_delete(
    message: Message,
    user: UserData,
    state: FSMContext,
    delete_batch: List[Dict[str, Any]],
    response_text: str,
) -> None:
    """
    Delete multiple explicitly-named events in a single pass.

    Each item must have original_event_hint.
    Safety: each deletion is preceded by an individual match check.
    If more than 1 event matches a hint, it is skipped with a clarification note.
    A single consolidated summary is sent at the end.
    """
    user_id = message.from_user.id
    tokens = get_user_tokens(user)
    if not tokens:
        await message.answer("🔐 ההרשאה שלך פגה.\nשלח /auth כדי להתחבר מחדש.")
        return

    total = len(delete_batch)
    successes: List[str] = []
    failures: List[str] = []

    status_msg = await message.answer(
        f"⚡ *מוחק {total} אירועים...* רגע אחד!",
        parse_mode="Markdown"
    )

    for item in delete_batch:
        hint = item.get("original_event_hint", "")
        if not hint:
            failures.append("⚠️ פריט ללא `original_event_hint` — דולג")
            continue

        time_from = item.get("time_hint_from")
        time_to = item.get("time_hint_to") or time_from

        # Phase 4: Broad fetch + local filter
        logger.info(f"[MultiDelete] FetchFilter hint='{hint}' from={time_from} to={time_to}")
        status, events = await _fetch_and_filter_events(tokens, hint, time_from, time_to, user_id)

        if status == "auth":
            failures.append(f"🔐 *{hint}* — ההרשאה פגה")
            break
        if status in ("error", "timeout"):
            failures.append(f"❌ *{hint}* — שגיאה בחיפוש")
            continue

        if len(events) == 0:
            failures.append(f"🔍 *{hint}* — לא נמצא ביומן")
            continue
        if len(events) > 1:
            failures.append(f"⚠️ *{hint}* — נמצאו {len(events)} תוצאות, פרט יותר או מחק ידנית")
            continue

        target_event = events[0]
        event_id = target_event.get("id")
        ev_summary = target_event.get("summary", "ללא שם")

        # 2. Delete
        try:
            delete_result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, lambda eid=event_id: calendar_service.delete_event(
                        tokens, event_id=eid, user_id=str(user_id)
                    )
                ), timeout=10
            )
        except asyncio.TimeoutError:
            failures.append(f"⏳ *{ev_summary}* — timeout במחיקה")
            continue
        except Exception as e:
            logger.error(f"[MultiDelete] Delete error for '{ev_summary}': {e}")
            failures.append(f"❌ *{ev_summary}* — שגיאה במחיקה")
            continue

        if delete_result.get("status") == "success":
            successes.append(f"🗑️ *{ev_summary}* — נמחק")
        elif delete_result.get("type") == ERROR_AUTH_REQUIRED:
            failures.append(f"🔐 *{ev_summary}* — ההרשאה פגה")
            break
        else:
            failures.append(f"❌ *{ev_summary}* — {delete_result.get('message', 'שגיאה')}")

    # Delete spinner
    try:
        await status_msg.delete()
    except Exception:
        pass

    # Send consolidated summary
    lines = [f"📋 *תוצאות מחיקת {total} האירועים:*\n"]
    lines.extend(successes)
    if failures:
        if successes:
            lines.append("")
        lines.extend(failures)
    summary_msg = "\n".join(lines)
    firestore_service.save_message(user_id, "assistant", summary_msg)
    await message.answer(summary_msg, parse_mode="Markdown")


# =============================================================================
# Update Event Handler
# =============================================================================

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
    
    # Extract search hint and time window
    hint = payload.get("original_event_hint", "")
    if not hint:
        await message.answer("🤔 לא הבנתי איזה אירוע לעדכן. נסה שוב עם שם האירוע.")
        return

    time_from = payload.get("time_hint_from")
    time_to = payload.get("time_hint_to") or time_from  # Default end = same day as start

    # Phase 4: Broad fetch + local filter
    logger.info(f"[Update] FetchFilter hint='{hint}' from={time_from} to={time_to}")
    status, events = await _fetch_and_filter_events(tokens, hint, time_from, time_to, user_id)

    if status == "auth":
        await message.answer("🔐 ההרשאה שלך פגה.\nשלח /auth כדי להתחבר מחדש.")
        return
    if status in ("error", "timeout"):
        await message.answer("❌ שגיאה בחיפוש האירוע. נסה שוב.")
        return


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
        # Multiple matches — enter FSM so next reply bypasses LLM
        candidates = events[:5]
        lines = [f"מצאתי כמה אירועים שמתאימים ל-'{hint}':\n"]
        for i, ev in enumerate(candidates, 1):
            ev_summary = ev.get("summary", "ללא שם")
            time_str = _format_event_time(ev)
            lines.append(f"{i}\ufe0f\u20e3 {ev_summary} - {time_str}")
        lines.append("\nאיזה מהם לעדכן? (כתוב מספר, שם לדוגמא 'ראשון', או 'כולם')")
        multi_msg = "\n".join(lines)
        # Save candidates + pending update payload to FSM
        await state.update_data(
            update_candidates=candidates,
            update_payload=payload,
            update_tokens=tokens,
        )
        await state.set_state(UpdateFlowStates.WAITING_FOR_SELECTION)
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
    
    # SUCCESS — build the Before→After diff message using the unified card
    before_card = _format_event_card(target_event)
    diff_display = "\n\n".join(diff_lines)
    success_msg = (
        f"✅ *האירוע עודכן בהצלחה!*\n\n"
        f"⬅️ *לפני:*\n{before_card}\n\n"
        f"➡️ *השינויים:*\n{diff_display}\n\n"
        f"עוד שינוי? 😎"
    )

    firestore_service.save_message(user_id, "assistant", success_msg)
    await message.answer(success_msg, parse_mode="Markdown")


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
    
    # Extract search hint and time window
    hint = payload.get("original_event_hint", "")
    if not hint:
        await message.answer("🤔 לא הבנתי איזה אירוע למחוק. נסה שוב עם שם האירוע.")
        return

    time_from = payload.get("time_hint_from")
    time_to = payload.get("time_hint_to") or time_from

    # Phase 4: Broad fetch + local filter
    logger.info(f"[Delete] FetchFilter hint='{hint}' from={time_from} to={time_to}")
    status, events = await _fetch_and_filter_events(tokens, hint, time_from, time_to, user_id)

    if status == "auth":
        await message.answer("🔐 ההרשאה שלך פגה.\nשלח /auth כדי להתחבר מחדש.")
        return
    if status in ("error", "timeout"):
        await message.answer("❌ שגיאה בחיפוש האירוע. נסה שוב.")
        return


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
        # Multiple matches — enter FSM so next reply bypasses LLM
        candidates = events[:5]
        lines = [f"מצאתי כמה אירועים שמתאימים ל-'{hint}':\n"]
        for i, ev in enumerate(candidates, 1):
            ev_summary = ev.get("summary", "ללא שם")
            time_str = _format_event_time(ev)
            lines.append(f"{i}\ufe0f\u20e3 {ev_summary} - {time_str}")
        lines.append("\nאיזה מהם למחוק? (כתוב מספר, שם לדוגמא 'ראשון', או 'כולם')")
        multi_msg = "\n".join(lines)
        await state.update_data(
            delete_candidates=candidates,
            delete_tokens=tokens,
        )
        await state.set_state(DeleteFlowStates.WAITING_FOR_MULTI_SELECTION)
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
    
    # Build confirmation message using the unified event card
    event_card = _format_event_card(target_event)
    confirm_msg = (
        f"🗑️ *מצאתי את האירוע הזה:*\n\n"
        f"{event_card}\n\n"
        f"⚠️ *בטוח שאתה רוצה למחוק את האירוע הזה?*\n"
        f"(כתוב *כן* למחיקה או *לא* לביטול)"
    )

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


# =============================================================================
# Phase 2: Update Selection Handler
# =============================================================================

@router.message(UpdateFlowStates.WAITING_FOR_SELECTION)
async def handle_update_selection(
    message: Message,
    state: FSMContext,
    user: Optional[UserData]
) -> None:
    """
    Handle the user's selection reply after the bot listed multiple matching events.
    Resolves the ordinal locally — no LLM call.
    Supports single picks ('ראשון', '2') and select-all ('כולם').
    """
    user_id = message.from_user.id
    text = message.text.strip() if message.text else ""
    firestore_service.save_message(user_id, "user", text)

    data = await state.get_data()
    candidates: List[Dict] = data.get("update_candidates", [])
    payload: Dict = data.get("update_payload", {})
    tokens: Dict = data.get("update_tokens", {})

    # Cancel check
    CANCEL_WORDS = {"בטל", "עצור", "לא משנה", "cancel", "stop", "עזוב", "ביטול"}
    if text.strip().lower() in CANCEL_WORDS:
        await state.clear()
        ack = "✅ בוטל."
        firestore_service.save_message(user_id, "assistant", ack)
        await message.answer(ack)
        return

    selection = _parse_ordinal(text, len(candidates))
    if selection is None:
        retry_msg = (
            f"לא הבנתי את הבחירה. 🤔\n"
            f"כתוב מספר בין 1 ל-{len(candidates)}, שם כמו 'ראשון', או 'כולם'."
        )
        firestore_service.save_message(user_id, "assistant", retry_msg)
        await message.answer(retry_msg)
        return

    # Resolve to list of target events
    targets = candidates if selection == "all" else [candidates[selection]]

    await state.clear()

    if len(targets) == 1:
        # Single update — run through existing single-event update engine
        target_event = targets[0]
        event_id = target_event.get("id")
        old_summary_str = target_event.get("summary", "ללא שם")
        old_time_str = _format_event_time(target_event)
        old_color_id = target_event.get("colorId", "")
        old_location = target_event.get("location", "")

        updates: Dict = {}
        diff_lines: List[str] = []

        if payload.get("new_summary"):
            updates["summary"] = payload["new_summary"]
            diff_lines.append(f"📝 שם:\n  ⬅️ {old_summary_str}\n  ➡️ {payload['new_summary']}")

        if payload.get("new_start_time"):
            updates["start_time"] = payload["new_start_time"]
            if payload.get("new_end_time"):
                updates["end_time"] = payload["new_end_time"]
            try:
                new_dt = datetime.fromisoformat(payload["new_start_time"])
                dn = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
                new_time_str = f"יום {dn[new_dt.weekday()]} {new_dt.strftime('%d/%m')} ב-{new_dt.strftime('%H:%M')}"
            except Exception:
                new_time_str = payload["new_start_time"]
            diff_lines.append(f"⏰ מועד:\n  ⬅️ {old_time_str}\n  ➡️ {new_time_str}")

        if payload.get("new_color_name"):
            new_color_id = CALENDAR_COLORS.get(payload["new_color_name"])
            if new_color_id:
                updates["color_id"] = new_color_id
                old_emoji = COLOR_ID_EMOJI.get(str(old_color_id), DEFAULT_EVENT_EMOJI)
                new_emoji = COLOR_ID_EMOJI.get(str(new_color_id), DEFAULT_EVENT_EMOJI)
                old_heb = COLOR_ID_HEBREW.get(int(old_color_id) if old_color_id else 0, "ברירת מחדל")
                new_heb = payload.get("new_color_name_hebrew", COLOR_ID_HEBREW.get(new_color_id, "?"))
                diff_lines.append(f"🎨 צבע:\n  ⬅️ {old_emoji} {old_heb}\n  ➡️ {new_emoji} {new_heb}")

        if payload.get("new_location"):
            updates["location"] = payload["new_location"]
            diff_lines.append(f"📍 מיקום:\n  ⬅️ {old_location or 'ללא'}\n  ➡️ {payload['new_location']}")

        if not updates:
            await message.answer("🤔 לא הבנתי מה לשנות.")
            return

        try:
            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, lambda: calendar_service.update_event(
                        tokens, event_id=event_id, updates=updates, user_id=str(user_id)
                    )
                ), timeout=10
            )
        except asyncio.TimeoutError:
            await message.answer("⏳ Google Calendar לא הגיב. נסה שוב.")
            return

        if result.get("status") != "success":
            await message.answer("❌ שגיאה בעדכון. נסה שוב.")
            return

        before_card = _format_event_card(target_event)
        diff_display = "\n\n".join(diff_lines)
        success_msg = (
            f"✅ *האירוע עודכן בהצלחה!*\n\n"
            f"⬅️ *לפני:*\n{before_card}\n\n"
            f"➡️ *השינויים:*\n{diff_display}\n\nעוד שינוי? 😎"
        )
        firestore_service.save_message(user_id, "assistant", success_msg)
        await message.answer(success_msg, parse_mode="Markdown")

    else:
        # Multi-update: loop all selected events, send a consolidated summary
        successes: List[str] = []
        failures: List[str] = []
        for target_event in targets:
            event_id = target_event.get("id")
            ev_summary = target_event.get("summary", "ללא שם")
            old_color_id = target_event.get("colorId", "")
            updates: Dict = {}

            if payload.get("new_summary"):
                updates["summary"] = payload["new_summary"]
            if payload.get("new_start_time"):
                updates["start_time"] = payload["new_start_time"]
                if payload.get("new_end_time"):
                    updates["end_time"] = payload["new_end_time"]
            if payload.get("new_color_name"):
                new_color_id = CALENDAR_COLORS.get(payload["new_color_name"])
                if new_color_id:
                    updates["color_id"] = new_color_id
            if payload.get("new_location"):
                updates["location"] = payload["new_location"]

            if not updates:
                failures.append(f"⚠️ *{ev_summary}* — אין שינויים להחיל")
                continue

            try:
                result = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, lambda eid=event_id, u=updates: calendar_service.update_event(
                            tokens, event_id=eid, updates=u, user_id=str(user_id)
                        )
                    ), timeout=10
                )
                if result.get("status") == "success":
                    successes.append(f"✅ *{ev_summary}*")
                else:
                    failures.append(f"❌ *{ev_summary}* — {result.get('message', 'שגיאה')}")
            except asyncio.TimeoutError:
                failures.append(f"⏳ *{ev_summary}* — timeout")
            except Exception as e:
                logger.error(f"[UpdateSelection] Error on '{ev_summary}': {e}")
                failures.append(f"❌ *{ev_summary}* — שגיאה")

        lines_out = [f"📋 *עדכנתי {len(targets)} אירועים:*\n"]
        lines_out.extend(successes)
        if failures:
            if successes:
                lines_out.append("")
            lines_out.extend(failures)
        summary_msg = "\n".join(lines_out)
        firestore_service.save_message(user_id, "assistant", summary_msg)
        await message.answer(summary_msg, parse_mode="Markdown")


# =============================================================================
# Phase 2: Delete Multi-Selection Handler
# =============================================================================

@router.message(DeleteFlowStates.WAITING_FOR_MULTI_SELECTION)
async def handle_delete_selection(
    message: Message,
    state: FSMContext,
    user: Optional[UserData]
) -> None:
    """
    Handle the user's selection reply after the bot listed multiple matching events for deletion.
    Resolves ordinals locally — no LLM call.
    For a single pick: transitions to WAITING_FOR_DELETE_CONFIRM (shows confirmation card).
    For 'כולם': loops and deletes all, sends consolidated result.
    """
    user_id = message.from_user.id
    text = message.text.strip() if message.text else ""
    firestore_service.save_message(user_id, "user", text)

    data = await state.get_data()
    candidates: List[Dict] = data.get("delete_candidates", [])
    tokens: Dict = data.get("delete_tokens", {})

    # Cancel check
    CANCEL_WORDS = {"בטל", "עצור", "לא משנה", "cancel", "stop", "עזוב", "ביטול"}
    if text.strip().lower() in CANCEL_WORDS:
        await state.clear()
        ack = "✅ בוטל."
        firestore_service.save_message(user_id, "assistant", ack)
        await message.answer(ack)
        return

    selection = _parse_ordinal(text, len(candidates))
    if selection is None:
        retry_msg = (
            f"לא הבנתי את הבחירה. 🤔\n"
            f"כתוב מספר בין 1 ל-{len(candidates)}, שם כמו 'ראשון', או 'כולם'."
        )
        firestore_service.save_message(user_id, "assistant", retry_msg)
        await message.answer(retry_msg)
        return

    if selection != "all":
        # Single pick — transition to standard delete confirmation flow
        target_event = candidates[selection]
        event_id = target_event.get("id")
        ev_summary = target_event.get("summary", "ללא שם")
        time_str = _format_event_time(target_event)

        event_card = _format_event_card(target_event)
        confirm_msg = (
            f"🗑️ *מצאתי את האירוע הזה:*\n\n"
            f"{event_card}\n\n"
            f"⚠️ *בטוח שאתה רוצה למחוק את האירוע הזה?*\n"
            f"(כתוב *כן* למחיקה או *לא* לביטול)"
        )
        await state.update_data(
            delete_event_id=event_id,
            delete_event_summary=ev_summary,
            delete_event_time=time_str,
            delete_tokens=tokens,
        )
        await state.set_state(DeleteFlowStates.WAITING_FOR_DELETE_CONFIRM)
        firestore_service.save_message(user_id, "assistant", confirm_msg)
        await message.answer(confirm_msg, parse_mode="Markdown")
        return

    # "כולם" — delete all candidates with a single consolidated confirm prompt
    await state.clear()
    summaries = [ev.get("summary", "ללא שם") for ev in candidates]
    bulk_confirm = (
        f"⚠️ *בטוח שאתה רוצה למחוק את כל {len(candidates)} האירועים הבאים?*\n"
        + "\n".join(f"• {s}" for s in summaries)
        + "\n\n(כתוב *כן* למחיקה או *לא* לביטול)"
    )
    # Save the full batch for the confirm handler
    await state.update_data(
        delete_all_candidates=candidates,
        delete_tokens=tokens,
    )
    await state.set_state(DeleteFlowStates.WAITING_FOR_DELETE_CONFIRM)
    firestore_service.save_message(user_id, "assistant", bulk_confirm)
    await message.answer(bulk_confirm, parse_mode="Markdown")


@router.message(RecurrenceFlowStates.WAITING_FOR_END_CONDITION)
async def handle_recurrence_end_date(
    message: Message,
    state: FSMContext,
    user: Optional[UserData]
) -> None:
    """
    Handle user providing end date for recurring event.
    Uses LLM to parse the date from natural language.
    """
    user_id = message.from_user.id
    user_text = message.text.strip() if message.text else ""
    
    # Save user message to history
    firestore_service.save_message(user_id, "user", user_text)
    
    # Cancel detection
    CANCEL_PHRASES = {"בטל", "עזוב", "לא משנה", "תעצור", "cancel", "stop", "abort"}
    if user_text.lower() in CANCEL_PHRASES:
        await state.clear()
        cancel_msg = "❌ האירוע בוטל."
        firestore_service.save_message(user_id, "assistant", cancel_msg)
        await message.answer(cancel_msg)
        return
    
    # Get pending event data
    data = await state.get_data()
    pending_event = data.get("pending_event")
    original_response = data.get("original_response", "")
    
    if not pending_event:
        await state.clear()
        await message.answer("🤔 משהו השתבש. נסה שוב.")
        return
    
    # Use router's LLM to parse end date from user's natural language
    try:
        from services.llm_service import llm_service
        from bot.utils import get_formatted_current_time
        
        current_time = get_formatted_current_time()
        
        # Create a focused prompt for date extraction by reusing router
        # We'll create a temporary create_event payload to leverage the router's date parsing
        temp_payload = {
            "summary": pending_event.get("summary", "אירוע"),
            "start_time": pending_event.get("start_time", ""),
            "recurrence_freq": pending_event.get("recurrence_freq"),
            "recurrence_interval": pending_event.get("recurrence_interval", 1)
        }
        
        # Use router to parse the end date from user's text
        # We'll ask it to extract recurrence_end_date from the user's response
        parse_query = f"עד מתי האירוע צריך לחזור? {user_text}"
        
        # Reuse router's intent classification but focus on extracting end date
        result = await llm_service.parse_user_intent(
            text=parse_query,
            current_time=current_time,
            user_preferences={},
            contacts={},
            history=None,
            agent_name="הבוט",
            user_nickname="חבר"
        )
        
        parsed_end_date = result.get("payload", {}).get("recurrence_end_date")
        
        if parsed_end_date:
            # Validate date format
            try:
                datetime.fromisoformat(parsed_end_date)
                pending_event["recurrence_end_date"] = parsed_end_date
                logger.info(f"[Recurrence] Parsed end date: {parsed_end_date}")
            except ValueError:
                logger.warning(f"[Recurrence] Invalid date format: {parsed_end_date}")
                error_msg = (
                    "❌ לא הצלחתי להבין את התאריך.\n"
                    "נסה שוב בפורמט ברור יותר, למשל: 'עד סוף מרץ' או 'עד ה-15/03'"
                )
                firestore_service.save_message(user_id, "assistant", error_msg)
                await message.answer(error_msg)
                return
        else:
            error_msg = (
                "❌ לא הצלחתי להבין את התאריך.\n"
                "נסה שוב בפורמט ברור יותר, למשל: 'עד סוף מרץ' או 'עד ה-15/03'"
            )
            firestore_service.save_message(user_id, "assistant", error_msg)
            await message.answer(error_msg)
            return
    
    except Exception as e:
        logger.error(f"[Recurrence] Error parsing end date: {e}")
        error_msg = (
            "❌ שגיאה בעיבוד התאריך.\n"
            "נסה שוב בפורמט ברור יותר, למשל: 'עד סוף מרץ' או 'עד ה-15/03'"
        )
        firestore_service.save_message(user_id, "assistant", error_msg)
        await message.answer(error_msg)
        return
    
    # Clear state and create event with end date
    await state.clear()
    
    # Check for missing contacts before creating
    attendee_names = pending_event.get("attendees", [])
    user_contacts = user.get("contacts", {}) if user else {}
    
    if attendee_names:
        missing_contacts = find_missing_contacts(attendee_names, user_contacts)
        if missing_contacts:
            # Re-enter missing contact flow
            missing_name = missing_contacts[0]
            await state.update_data(
                pending_event=pending_event,
                missing_contact_name=missing_name,
                remaining_missing=missing_contacts[1:] if len(missing_contacts) > 1 else [],
                original_response=original_response
            )
            await state.set_state(EventFlowStates.WAITING_FOR_MISSING_CONTACT_EMAIL)
            
            ask_email_msg = (
                f"👤 שמתי לב שביקשת להזמין את *{missing_name}*,\n"
                f"אבל אין לי את המייל שלו.\n\n"
                f"מה המייל של {missing_name}?"
            )
            firestore_service.save_message(user_id, "assistant", ask_email_msg)
            await message.answer(ask_email_msg, parse_mode="Markdown")
            return
    
    # All good - create event
    fresh_user = firestore_service.get_user(user_id) or user
    await create_event_from_payload(message, fresh_user, pending_event, original_response)


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
