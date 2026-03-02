"""
Search Flow Handler for Agentic Calendar 2.0

Handles the FSM state WAITING_FOR_TIMEFRAME, entered when the user asks for
an entity search without specifying a date range (e.g. "Do I have meetings
with Danny?"). The bot asks for clarification, then executes the search here.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from aiogram import Router
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from models.user import UserData
from services.calendar_service import calendar_service
from services.firestore_service import firestore_service
from bot.states import SearchFlowStates

logger = logging.getLogger(__name__)

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

# Aliases for user's timeframe reply
THIS_WEEK_KEYWORDS = {"השבוע", "שבוע", "week", "this week"}
THIS_MONTH_KEYWORDS = {"החודש", "חודש", "month", "this month", "כל החודש"}

router = Router(name="search_router")


def _resolve_timeframe(text: str):
    """
    Parse user's freeform timeframe reply into (time_min, time_max) ISO strings.
    Returns (None, None) if we can't parse it.
    """
    text_lower = text.strip().lower()
    now = datetime.now(ISRAEL_TZ)
    today = now.date()

    if text_lower in THIS_WEEK_KEYWORDS:
        # Monday of this week → Sunday
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        time_min = datetime(monday.year, monday.month, monday.day, tzinfo=ISRAEL_TZ).isoformat()
        time_max = datetime(sunday.year, sunday.month, sunday.day, 23, 59, 59, tzinfo=ISRAEL_TZ).isoformat()
        label = "השבוע"
        return time_min, time_max, label

    if text_lower in THIS_MONTH_KEYWORDS:
        # First of this month → last day
        first = today.replace(day=1)
        if today.month == 12:
            last = today.replace(month=12, day=31)
        else:
            last = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        time_min = datetime(first.year, first.month, first.day, tzinfo=ISRAEL_TZ).isoformat()
        time_max = datetime(last.year, last.month, last.day, 23, 59, 59, tzinfo=ISRAEL_TZ).isoformat()
        label = "החודש"
        return time_min, time_max, label

    # Try to parse a specific date from the text (basic check for dd/mm pattern)
    import re
    match = re.search(r'(\d{1,2})[/\-.](\d{1,2})', text)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        year = today.year if month >= today.month else today.year + 1
        try:
            from datetime import date
            specific = date(year, month, day)
            time_min = datetime(specific.year, specific.month, specific.day, tzinfo=ISRAEL_TZ).isoformat()
            time_max = datetime(specific.year, specific.month, specific.day, 23, 59, 59, tzinfo=ISRAEL_TZ).isoformat()
            label = f"{day:02d}/{month:02d}"
            return time_min, time_max, label
        except ValueError:
            pass

    return None, None, "הטווח שביקשת"


@router.message(SearchFlowStates.WAITING_FOR_TIMEFRAME)
async def handle_search_timeframe(
    message: Message,
    state: FSMContext,
    user: Optional[UserData]
) -> None:
    """
    Handles the user's timeframe reply during an entity search clarification.

    Resolves the text to a date range, runs search_events(), applies fuzzy
    ±2 day fallback on zero results, then presents the results.
    """
    user_id = message.from_user.id
    tokens = user.get("calendar_config", {}) if user else {}
    fsm_data = await state.get_data()
    entity_name = fsm_data.get("search_entity", "")

    await state.clear()

    timeframe_text = message.text or ""
    time_min, time_max, label = _resolve_timeframe(timeframe_text)

    if not time_min:
        # Could not parse — ask again gently or fall back to this week
        now = datetime.now(ISRAEL_TZ)
        monday = now.date() - timedelta(days=now.weekday())
        sunday = monday + timedelta(days=6)
        time_min = datetime(monday.year, monday.month, monday.day, tzinfo=ISRAEL_TZ).isoformat()
        time_max = datetime(sunday.year, sunday.month, sunday.day, 23, 59, 59, tzinfo=ISRAEL_TZ).isoformat()
        label = "השבוע"

    try:
        result = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, lambda: calendar_service.search_events(
                    tokens,
                    query=entity_name,
                    time_min=time_min,
                    time_max=time_max,
                    max_results=15,
                    user_id=str(user_id)
                )
            ), timeout=10
        )

        events = result.get("events", []) if result.get("status") == "success" else []

        # Fuzzy ±2 day fallback
        if not events:
            logger.info(f"[Search] No results for '{entity_name}' in {label}, expanding ±2 days")
            expanded_min = (datetime.fromisoformat(time_min) - timedelta(days=2)).isoformat()
            expanded_max = (datetime.fromisoformat(time_max) + timedelta(days=2)).isoformat()
            exp_result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, lambda: calendar_service.search_events(
                        tokens,
                        query=entity_name,
                        time_min=expanded_min,
                        time_max=expanded_max,
                        max_results=15,
                        user_id=str(user_id)
                    )
                ), timeout=10
            )
            events = exp_result.get("events", []) if exp_result.get("status") == "success" else []

            if events:
                response = (
                    f"🔍 לא מצאתי '{entity_name}' ב{label}, אבל מצאתי בתאריכים קרובים:\n\n"
                    + calendar_service.format_search_results(events, entity_name)
                )
            else:
                upcoming = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, lambda: calendar_service.get_upcoming_events(
                            tokens, max_results=3, user_id=str(user_id)
                        )
                    ), timeout=10
                )
                upcoming_events = upcoming.get("events", []) if upcoming.get("status") == "success" else []
                response = f"🔍 לא מצאתי אירועים עם '{entity_name}' ב{label}.\n\n"
                if upcoming_events:
                    response += "אבל הנה מה שמגיע בקרוב:\n" + calendar_service.format_today_events(upcoming_events)
                else:
                    response += "גם אין אירועים קרובים אחרים."
        else:
            response = (
                f"📅 *אירועים עם {entity_name} ב{label}:*\n\n"
                + calendar_service.format_search_results(events, entity_name)
            )

    except asyncio.TimeoutError:
        response = "⏳ Google Calendar לא הגיב בזמן. נסה שוב בעוד רגע."
    except Exception as e:
        logger.error(f"[Search FSM] Error: {e}")
        response = "❌ שגיאה בחיפוש. נסה שוב."

    firestore_service.save_message(user_id, "assistant", response)
    await message.answer(response, parse_mode="Markdown")
