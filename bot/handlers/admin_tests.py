"""
Admin Test Suite Handler
Fully isolated from regular user flows.
Requires password-protected entry.

Tests:
1. CRUD Obstacle Course - Create → Read → Update → Delete
2. Onboarding Simulation - Full onboarding flow
3. Voice Loop - Multiple voice messages
4. Search Loop - Multiple calendar searches
5. Dry-Run Event - Event parsing without saving
"""

import re
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from models.user import UserData
from bot.states import AdminTestStates
from services.calendar_service import calendar_service, ERROR_AUTH_REQUIRED
from services.firestore_service import firestore_service
from services.llm_service import llm_service
from bot.handlers.events import create_event_from_payload, process_update_event, process_delete_event
from bot.utils import get_formatted_current_time
from config import ADMIN_TEST_ENABLED

logger = logging.getLogger(__name__)

# Create router for admin tests
router = Router(name="admin_tests_router")

# =============================================================================
# Global Exit Handler (Highest Priority - Works from ANY admin state)
# =============================================================================

EXIT_KEYWORDS = [
    "צא", "עצור", "ביטול",  # Hebrew
    "יציאה", "סיום", "exit", "quit", "cancel", "back", "menu"  # English + Hebrew
]

@router.message(
    StateFilter(AdminTestStates), 
    F.text.regexp(re.compile(r'צא|עצור|ביטול|יציאה|סיום|exit|quit|cancel|back|menu', re.IGNORECASE))
)
async def handle_global_exit(message: Message, state: FSMContext):
    """
    Global exit - works from any admin test state.
    Immediately clears state and returns user to normal flow.
    """
    user_id = message.from_user.id
    await state.clear()
    
    exit_msg = "✅ יצאת ממצב בדיקה. חזרת למצב רגיל."
    firestore_service.save_message(user_id, "assistant", exit_msg)
    await message.answer(exit_msg)
    logger.info(f"[AdminTest] User {user_id} exited admin test suite")


# =============================================================================
# Main Menu Handler
# =============================================================================

@router.message(StateFilter(AdminTestStates.MAIN_MENU))
async def handle_main_menu(message: Message, state: FSMContext, user: Optional[UserData]):
    """Show test menu and handle selection."""
    if not ADMIN_TEST_ENABLED:
        await message.answer("❌ Admin Test Suite is disabled.")
        await state.clear()
        return
    
    text = message.text.strip().lower() if message.text else ""
    user_id = message.from_user.id
    
    # Save user message
    firestore_service.save_message(user_id, "user", message.text or "")
    
    # Test selection
    if text in ["1", "crud", "קרוד", "crud test"]:
        await state.set_state(AdminTestStates.CRUD_CREATE)
        await start_crud_test(message, state, user)
    
    elif text in ["2", "onboarding", "אונבורדינג", "onboarding sim"]:
        await state.set_state(AdminTestStates.ONBOARDING_SIM)
        await start_onboarding_sim(message, state, user)
    
    elif text in ["3", "voice", "קול", "voice loop"]:
        await state.set_state(AdminTestStates.VOICE_LOOP)
        await start_voice_loop(message, state, user)
    
    elif text in ["4", "search", "חיפוש", "search loop"]:
        await state.set_state(AdminTestStates.SEARCH_LOOP)
        await start_search_loop(message, state, user)
    
    elif text in ["5", "dry-run", "dry run", "דרי רן", "dry-run event"]:
        await state.set_state(AdminTestStates.DRY_RUN_EVENT)
        await start_dry_run_event(message, state, user)
    
    else:
        # Show menu again
        menu_msg = (
            "🧪 *Admin Test Suite*\n\n"
            "בחר בדיקה:\n"
            "1️⃣ CRUD Obstacle Course\n"
            "2️⃣ Onboarding Simulation\n"
            "3️⃣ Voice Loop\n"
            "4️⃣ Search Loop\n"
            "5️⃣ Dry-Run Event\n\n"
            "לצאת: כתוב *צא* או *exit*"
        )
        firestore_service.save_message(user_id, "assistant", menu_msg)
        await message.answer(menu_msg, parse_mode="Markdown")


# =============================================================================
# Test 1: CRUD Obstacle Course
# =============================================================================

async def start_crud_test(message: Message, state: FSMContext, user: Optional[UserData]):
    """Start CRUD test sequence."""
    user_id = message.from_user.id
    
    # Check tokens
    tokens = user.get("calendar_config", {}) if user else {}
    if not tokens.get("refresh_token"):
        await message.answer("❌ אין הרשאות ליומן. שלח /auth תחילה.")
        await state.set_state(AdminTestStates.MAIN_MENU)
        return
    
    # Initialize test data
    test_event_name = "[TEST] CRUD Test Event"
    start_time = datetime.now() + timedelta(hours=1)
    end_time = start_time + timedelta(hours=1)
    
    await state.update_data(
        crud_test_event_name=test_event_name,
        crud_start_time=start_time.isoformat(),
        crud_end_time=end_time.isoformat(),
        crud_step="create"
    )
    
    # Create event
    event_data = {
        "summary": test_event_name,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "category": "work"
    }
    
    result = calendar_service.add_event(
        user_tokens=tokens,
        event_data=event_data,
        user_id=str(user_id)
    )
    
    if result.get("status") == "success":
        event_id = result.get("event", {}).get("id")
        await state.update_data(crud_event_id=event_id)
        await state.set_state(AdminTestStates.CRUD_READ)
        
        msg = (
            "✅ *שלב 1: CREATE*\n"
            f"נוצר אירוע: {test_event_name}\n"
            f"ID: {event_id}\n\n"
            "ממשיך לשלב הבא..."
        )
        firestore_service.save_message(user_id, "assistant", msg)
        await message.answer(msg, parse_mode="Markdown")
        
        # Auto-advance to READ
        await asyncio.sleep(1)
        await handle_crud_read(message, state, user)
    else:
        error_msg = f"❌ שגיאה ב-CREATE: {result.get('message', 'Unknown error')}"
        await message.answer(error_msg)
        await state.set_state(AdminTestStates.MAIN_MENU)


async def handle_crud_read(message: Message, state: FSMContext, user: Optional[UserData]):
    """Handle CRUD read step."""
    user_id = message.from_user.id
    data = await state.get_data()
    event_id = data.get("crud_event_id")
    event_name = data.get("crud_test_event_name", "[TEST] CRUD Test Event")
    
    if not event_id:
        await message.answer("❌ אין event_id. חוזר לתפריט.")
        await state.set_state(AdminTestStates.MAIN_MENU)
        return
    
    tokens = user.get("calendar_config", {}) if user else {}
    
    # Search for the event
    result = calendar_service.search_events(
        user_tokens=tokens,
        query=event_name,
        user_id=str(user_id)
    )
    
    if result.get("status") == "success":
        events = result.get("events", [])
        if events:
            event = events[0]
            summary = event.get("summary", "ללא שם")
            event_id_found = event.get("id")
            
            await state.set_state(AdminTestStates.CRUD_UPDATE)
            
            msg = (
                "✅ *שלב 2: READ*\n"
                f"נמצא אירוע: {summary}\n"
                f"ID: {event_id_found}\n\n"
                "ממשיך לשלב הבא..."
            )
            firestore_service.save_message(user_id, "assistant", msg)
            await message.answer(msg, parse_mode="Markdown")
            
            # Auto-advance to UPDATE
            await asyncio.sleep(1)
            await handle_crud_update(message, state, user)
        else:
            await message.answer("❌ האירוע לא נמצא ב-READ.")
            await state.set_state(AdminTestStates.MAIN_MENU)
    else:
        await message.answer(f"❌ שגיאה ב-READ: {result.get('message', 'Unknown error')}")
        await state.set_state(AdminTestStates.MAIN_MENU)


@router.message(StateFilter(AdminTestStates.CRUD_READ))
async def handle_crud_read_message(message: Message, state: FSMContext, user: Optional[UserData]):
    """Handle user message during CRUD read state."""
    await handle_crud_read(message, state, user)


async def handle_crud_update(message: Message, state: FSMContext, user: Optional[UserData]):
    """Handle CRUD update step."""
    user_id = message.from_user.id
    data = await state.get_data()
    event_id = data.get("crud_event_id")
    old_name = data.get("crud_test_event_name", "[TEST] CRUD Test Event")
    new_name = "[TEST] CRUD Test Event Updated"
    
    if not event_id:
        await message.answer("❌ אין event_id. חוזר לתפריט.")
        await state.set_state(AdminTestStates.MAIN_MENU)
        return
    
    tokens = user.get("calendar_config", {}) if user else {}
    
    # Update event
    updates = {"summary": new_name}
    result = calendar_service.update_event(
        user_tokens=tokens,
        event_id=event_id,
        updates=updates,
        user_id=str(user_id)
    )
    
    if result.get("status") == "success":
        await state.update_data(crud_test_event_name=new_name)
        await state.set_state(AdminTestStates.CRUD_DELETE)
        
        msg = (
            "✅ *שלב 3: UPDATE*\n"
            f"עודכן מ: {old_name}\n"
            f"ל: {new_name}\n\n"
            "ממשיך לשלב הבא..."
        )
        firestore_service.save_message(user_id, "assistant", msg)
        await message.answer(msg, parse_mode="Markdown")
        
        # Auto-advance to DELETE
        await asyncio.sleep(1)
        await handle_crud_delete(message, state, user)
    else:
        await message.answer(f"❌ שגיאה ב-UPDATE: {result.get('message', 'Unknown error')}")
        await state.set_state(AdminTestStates.MAIN_MENU)


@router.message(StateFilter(AdminTestStates.CRUD_UPDATE))
async def handle_crud_update_message(message: Message, state: FSMContext, user: Optional[UserData]):
    """Handle user message during CRUD update state."""
    await handle_crud_update(message, state, user)


async def handle_crud_delete(message: Message, state: FSMContext, user: Optional[UserData]):
    """Handle CRUD delete step."""
    user_id = message.from_user.id
    data = await state.get_data()
    event_id = data.get("crud_event_id")
    event_name = data.get("crud_test_event_name", "[TEST] CRUD Test Event Updated")
    
    if not event_id:
        await message.answer("❌ אין event_id. חוזר לתפריט.")
        await state.set_state(AdminTestStates.MAIN_MENU)
        return
    
    tokens = user.get("calendar_config", {}) if user else {}
    
    # Delete event
    result = calendar_service.delete_event(
        user_tokens=tokens,
        event_id=event_id,
        user_id=str(user_id)
    )
    
    if result.get("status") == "success":
        await state.set_state(AdminTestStates.MAIN_MENU)
        
        msg = (
            "✅ *שלב 4: DELETE*\n"
            f"נמחק אירוע: {event_name}\n\n"
            "🎉 *CRUD Obstacle Course הושלם בהצלחה!*\n\n"
            "חזור לתפריט הראשי."
        )
        firestore_service.save_message(user_id, "assistant", msg)
        await message.answer(msg, parse_mode="Markdown")
    else:
        await message.answer(f"❌ שגיאה ב-DELETE: {result.get('message', 'Unknown error')}")
        await state.set_state(AdminTestStates.MAIN_MENU)


@router.message(StateFilter(AdminTestStates.CRUD_DELETE))
async def handle_crud_delete_message(message: Message, state: FSMContext, user: Optional[UserData]):
    """Handle user message during CRUD delete state."""
    await handle_crud_delete(message, state, user)


# =============================================================================
# Test 2: Onboarding Simulation
# =============================================================================

async def start_onboarding_sim(message: Message, state: FSMContext, user: Optional[UserData]):
    """Start onboarding simulation test."""
    user_id = message.from_user.id
    
    await state.update_data(onboarding_step=0)
    
    msg = (
        "🧪 *Onboarding Simulation*\n\n"
        "זהו סימולציה של תהליך האונבורדינג.\n"
        "הנתונים לא יישמרו למשתמש האמיתי.\n\n"
        "מתחיל סימולציה..."
    )
    firestore_service.save_message(user_id, "assistant", msg)
    await message.answer(msg, parse_mode="Markdown")
    
    # Simulate onboarding steps
    steps = [
        ("ניקname", "הכנס כינוי"),
        ("בוט", "הכנס שם לבוט"),
        ("זכר", "הכנס מגדר"),
        ("כן", "הפעל תזכורות?"),
        ("לא", "הפעל daily check?"),
        ("כן", "הפעל daily briefing?"),
        ("עבודה=כתום", "הגדר צבעים"),
        ("דני=dan@example.com", "הוסף אנשי קשר")
    ]
    
    await state.update_data(onboarding_steps=steps, onboarding_current=0)
    
    # Show first step
    await asyncio.sleep(1)
    await handle_onboarding_step(message, state, user)


@router.message(StateFilter(AdminTestStates.ONBOARDING_SIM))
async def handle_onboarding_sim(message: Message, state: FSMContext, user: Optional[UserData]):
    """Handle onboarding simulation steps."""
    await handle_onboarding_step(message, state, user)


async def handle_onboarding_step(message: Message, state: FSMContext, user: Optional[UserData]):
    """Process onboarding simulation step."""
    user_id = message.from_user.id
    data = await state.get_data()
    steps = data.get("onboarding_steps", [])
    current = data.get("onboarding_current", 0)
    
    if current >= len(steps):
        # Complete
        await state.set_state(AdminTestStates.MAIN_MENU)
        msg = (
            "✅ *Onboarding Simulation הושלמה!*\n\n"
            "כל השלבים עברו בהצלחה.\n"
            "חזור לתפריט הראשי."
        )
        firestore_service.save_message(user_id, "assistant", msg)
        await message.answer(msg, parse_mode="Markdown")
        return
    
    step_input, step_prompt = steps[current]
    
    # Simulate processing
    await asyncio.sleep(0.5)
    
    await state.update_data(onboarding_current=current + 1)
    
    if current + 1 < len(steps):
        next_input, next_prompt = steps[current + 1]
        msg = (
            f"✅ שלב {current + 1}: {step_prompt}\n"
            f"קלט: {step_input}\n\n"
            f"➡️ שלב {current + 2}: {next_prompt}"
        )
    else:
        msg = (
            f"✅ שלב {current + 1}: {step_prompt}\n"
            f"קלט: {step_input}\n\n"
            "✅ כל השלבים הושלמו!"
        )
    
    firestore_service.save_message(user_id, "assistant", msg)
    await message.answer(msg)


# =============================================================================
# Test 3: Voice Loop
# =============================================================================

async def start_voice_loop(message: Message, state: FSMContext, user: Optional[UserData]):
    """Start voice loop test."""
    user_id = message.from_user.id
    
    await state.update_data(voice_count=0, voice_intents=[])
    
    msg = (
        "🧪 *Voice Loop Test*\n\n"
        "שלח 3 הודעות קוליות רצופות.\n"
        "אבדוק את תהליך ההתמרה והסיווג.\n\n"
        "ממתין להודעה קולית ראשונה..."
    )
    firestore_service.save_message(user_id, "assistant", msg)
    await message.answer(msg, parse_mode="Markdown")


@router.message(StateFilter(AdminTestStates.VOICE_LOOP))
async def handle_voice_loop(message: Message, state: FSMContext, user: Optional[UserData]):
    """Handle voice messages in voice loop test."""
    user_id = message.from_user.id
    data = await state.get_data()
    count = data.get("voice_count", 0)
    intents = data.get("voice_intents", [])
    
    # Check if it's a voice message
    if not message.voice:
        await message.answer("❌ אנא שלח הודעה קולית.")
        return
    
    count += 1
    intents.append(f"Voice message {count}")
    
    await state.update_data(voice_count=count, voice_intents=intents)
    
    if count < 3:
        msg = (
            f"✅ הודעה קולית {count} התקבלה!\n\n"
            f"ממתין להודעה {count + 1}/3..."
        )
    else:
        await state.set_state(AdminTestStates.MAIN_MENU)
        msg = (
            "✅ *Voice Loop Test הושלם!*\n\n"
            f"קיבלתי {count} הודעות קוליות:\n"
            "\n".join([f"• {intent}" for intent in intents]) + "\n\n"
            "חזור לתפריט הראשי."
        )
    
    firestore_service.save_message(user_id, "assistant", msg)
    await message.answer(msg, parse_mode="Markdown")


# =============================================================================
# Test 4: Search Loop
# =============================================================================

async def start_search_loop(message: Message, state: FSMContext, user: Optional[UserData]):
    """Start search loop test."""
    user_id = message.from_user.id
    
    tokens = user.get("calendar_config", {}) if user else {}
    if not tokens.get("refresh_token"):
        await message.answer("❌ אין הרשאות ליומן. שלח /auth תחילה.")
        await state.set_state(AdminTestStates.MAIN_MENU)
        return
    
    await state.update_data(search_count=0, search_results=[])
    
    # Predefined search queries
    queries = [
        "מה יש לי היום?",
        "מתי הפגישה הבאה?",
        "מה יש לי מחר?"
    ]
    
    await state.update_data(search_queries=queries, search_current=0)
    
    msg = (
        "🧪 *Search Loop Test*\n\n"
        "אבצע 3 חיפושים רצופים ביומן.\n"
        "מתחיל..."
    )
    firestore_service.save_message(user_id, "assistant", msg)
    await message.answer(msg, parse_mode="Markdown")
    
    # Execute searches
    await asyncio.sleep(1)
    await execute_search_queries(message, state, user)


async def execute_search_queries(message: Message, state: FSMContext, user: Optional[UserData]):
    """Execute search queries."""
    user_id = message.from_user.id
    data = await state.get_data()
    queries = data.get("search_queries", [])
    current = data.get("search_current", 0)
    results = data.get("search_results", [])
    
    if current >= len(queries):
        # Complete
        await state.set_state(AdminTestStates.MAIN_MENU)
        msg = (
            "✅ *Search Loop Test הושלם!*\n\n"
            "תוצאות החיפושים:\n" +
            "\n".join([f"• {r}" for r in results]) + "\n\n"
            "חזור לתפריט הראשי."
        )
        firestore_service.save_message(user_id, "assistant", msg)
        await message.answer(msg, parse_mode="Markdown")
        return
    
    query = queries[current]
    
    # Use LLM to classify intent
    current_time = get_formatted_current_time()
    result = await llm_service.parse_user_intent(
        text=query,
        current_time=current_time,
        user_preferences={},
        contacts={},
        history=None,
        agent_name="הבוט",
        user_nickname="חבר"
    )
    
    intent = result.get("intent", "unknown")
    results.append(f"Query {current + 1}: '{query}' → Intent: {intent}")
    
    await state.update_data(search_current=current + 1, search_results=results)
    
    msg = f"✅ חיפוש {current + 1}/3: '{query}' → {intent}"
    firestore_service.save_message(user_id, "assistant", msg)
    await message.answer(msg)
    
    # Continue to next query
    await asyncio.sleep(1)
    await execute_search_queries(message, state, user)


@router.message(StateFilter(AdminTestStates.SEARCH_LOOP))
async def handle_search_loop_message(message: Message, state: FSMContext, user: Optional[UserData]):
    """Handle user message during search loop (shouldn't happen, but handle gracefully)."""
    await message.answer("⏳ מבצע חיפושים... אנא המתן.")


# =============================================================================
# Test 5: Dry-Run Event
# =============================================================================

async def start_dry_run_event(message: Message, state: FSMContext, user: Optional[UserData]):
    """Start dry-run event test."""
    user_id = message.from_user.id
    
    await state.update_data(dry_run_step="waiting_input")
    
    msg = (
        "🧪 *Dry-Run Event Test*\n\n"
        "שלח בקשה ליצירת אירוע.\n"
        "אבדוק את תהליך הניתוח ללא שמירה.\n\n"
        "דוגמה: 'תקבע פגישה מחר ב-10:00'"
    )
    firestore_service.save_message(user_id, "assistant", msg)
    await message.answer(msg, parse_mode="Markdown")


@router.message(StateFilter(AdminTestStates.DRY_RUN_EVENT))
async def handle_dry_run_event(message: Message, state: FSMContext, user: Optional[UserData]):
    """Handle dry-run event input."""
    user_id = message.from_user.id
    data = await state.get_data()
    step = data.get("dry_run_step", "waiting_input")
    
    if step == "waiting_input":
        # Parse event using LLM
        text = message.text or ""
        current_time = get_formatted_current_time()
        
        personal_info = user.get("personal_info", {}) if user else {}
        agent_name = personal_info.get("agent_nickname") or "הבוט"
        user_nickname = personal_info.get("nickname") or "חבר"
        
        result = await llm_service.parse_user_intent(
            text=text,
            current_time=current_time,
            user_preferences={},
            contacts=user.get("contacts", {}) if user else {},
            history=None,
            agent_name=agent_name,
            user_nickname=user_nickname
        )
        
        intent = result.get("intent", "unknown")
        payload = result.get("payload", {})
        
        if intent == "create_event":
            # Show parsed event structure
            summary = payload.get("summary", "ללא שם")
            start_time = payload.get("start_time", "לא צוין")
            end_time = payload.get("end_time", "לא צוין")
            category = payload.get("category", "לא צוין")
            
            await state.update_data(
                dry_run_step="waiting_confirmation",
                dry_run_payload=payload
            )
            
            msg = (
                "✅ *אירוע נבדק (Dry-Run)*\n\n"
                f"📝 שם: {summary}\n"
                f"⏰ התחלה: {start_time}\n"
                f"⏰ סיום: {end_time}\n"
                f"📂 קטגוריה: {category}\n\n"
                "לשמור את האירוע? (כן/לא)"
            )
            firestore_service.save_message(user_id, "assistant", msg)
            await message.answer(msg, parse_mode="Markdown")
        else:
            await state.set_state(AdminTestStates.MAIN_MENU)
            msg = f"❌ Intent לא תואם: {intent} (צפוי: create_event)"
            firestore_service.save_message(user_id, "assistant", msg)
            await message.answer(msg)
    
    elif step == "waiting_confirmation":
        # Handle confirmation
        text = (message.text or "").lower().strip()
        payload = data.get("dry_run_payload", {})
        
        if text in ["כן", "yes", "save", "שמור"]:
            # Actually create the event
            if user:
                await create_event_from_payload(message, user, payload, "אירוע נוצר מ-Dry-Run")
            
            await state.set_state(AdminTestStates.MAIN_MENU)
            msg = "✅ האירוע נשמר! חזור לתפריט הראשי."
        elif text in ["לא", "no", "skip", "דלג"]:
            await state.set_state(AdminTestStates.MAIN_MENU)
            msg = (
                "✅ Dry-Run הושלם - האירוע לא נשמר.\n"
                "חזור לתפריט הראשי."
            )
        else:
            msg = "❌ לא הבנתי. כתוב 'כן' לשמירה או 'לא' לדילוג."
            firestore_service.save_message(user_id, "assistant", msg)
            await message.answer(msg)
            return
        
        firestore_service.save_message(user_id, "assistant", msg)
        await message.answer(msg)