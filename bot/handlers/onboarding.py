"""
Onboarding FSM handlers for Agentic Calendar 2.0
Multi-step conversational questionnaire for new user setup.

Flow: Confirmation → Nickname → Agent Name → Gender → Reminders → Daily Check → Daily Briefing → Colors → Contacts → Complete
"""

from typing import Optional
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from models.user import UserData
from services.firestore_service import firestore_service
from services.calendar_service import calendar_service
from bot.states import OnboardingStates
from bot.keyboards import (
    get_gender_keyboard,
    get_yes_no_keyboard,
    get_onboarding_confirm_keyboard
)


# Create router for onboarding handlers
router = Router(name="onboarding_router")

# Cancel/exit keywords for FSM escape
CANCEL_KEYWORDS = {"בטל", "ביטול", "עצור", "עזוב", "cancel", "stop", "exit", "quit"}


async def is_cancel_request(message: Message, state: FSMContext) -> bool:
    """Check if user wants to cancel the onboarding flow."""
    text = (message.text or "").strip().lower()
    if text in CANCEL_KEYWORDS:
        await state.clear()
        await message.answer(
            "❌ ההגדרות בוטלו.\n"
            "אפשר להתחיל מחדש בכל עת עם /settings"
        )
        return True
    return False


# =============================================================================
# Onboarding Trigger (from commands.py callback OR keyword)
# =============================================================================

@router.callback_query(F.data == "onboarding_start")
async def onboarding_start_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """
    Handle "Start Setup" button from /start command.
    Shows confirmation message before starting.
    """
    await callback.answer()
    await callback.message.edit_text("✅ בוא נתחיל!")
    
    # Show the chatty confirmation
    await send_onboarding_intro(callback.message, state)


@router.message(F.text.in_(["שאלון", "התחל שאלון", "onboarding"]))
async def onboarding_keyword_trigger(message: Message, state: FSMContext, user: Optional[UserData]) -> None:
    """
    Trigger onboarding when user types "שאלון" keyword.
    """
    if not user:
        await message.answer(
            "❌ אתה צריך להתחבר קודם.\n"
            "שלח /auth כדי להתחבר עם Google."
        )
        return
    
    # Clear any existing state and start fresh
    await state.clear()
    await send_onboarding_intro(message, state)


async def send_onboarding_intro(message: Message, state: FSMContext) -> None:
    """
    Send the onboarding intro with confirmation buttons.
    """
    await state.set_state(OnboardingStates.WAITING_FOR_CONFIRMATION)
    await message.answer(
        "🎉 איזה כיף שנרשמת!\n\n"
        "זורם לעשות שאלון קליל שיעזור לי להתאים את הדברים אישית אליך?\n\n"
        "_(זה לוקח פחות מדקה, מבטיח!)_",
        parse_mode="Markdown",
        reply_markup=get_onboarding_confirm_keyboard()
    )


# =============================================================================
# Step 0: Confirmation
# =============================================================================

@router.callback_query(OnboardingStates.WAITING_FOR_CONFIRMATION, F.data == "onboarding_confirm_yes")
async def onboarding_confirm_yes(callback: CallbackQuery, state: FSMContext) -> None:
    """
    User agreed to start onboarding.
    """
    await callback.answer("יאללה! 🚀")
    await callback.message.edit_text("✅ יאללה בוא נתחיל!")
    
    # Move to nickname step
    await state.set_state(OnboardingStates.WAITING_FOR_NICKNAME)
    await callback.message.answer(
        "🙋 *אז קודם כל, איך תרצה שאקרא לך?*\n\n"
        "_(שם, כינוי, מה שבא לך)_",
        parse_mode="Markdown"
    )


@router.callback_query(OnboardingStates.WAITING_FOR_CONFIRMATION, F.data == "onboarding_confirm_later")
async def onboarding_confirm_later(callback: CallbackQuery, state: FSMContext) -> None:
    """
    User wants to do onboarding later.
    """
    user_id = callback.from_user.id
    first_name = callback.from_user.first_name or "חבר"
    
    await callback.answer()
    await callback.message.edit_text("😴 אין בעיה, נדבר אחר כך!")
    
    # Set minimal defaults so user can still use the bot
    firestore_service.update_user(user_id, {
        "personal_info.nickname": first_name,
        "personal_info.agent_nickname": "הבוט",
        "personal_info.gender": "neutral",
        "enable_reminders": False,
        "enable_daily_check": False,
        "onboarding_completed": True
    })
    
    await state.clear()
    
    await callback.message.answer(
        f"סבבה {first_name}! אני פה אם תצטרך משהו.\n\n"
        "כשתרצה לעשות את השאלון, פשוט כתוב לי 'שאלון' או שלח /settings 🛠️"
    )


# =============================================================================
# Step 1: User Nickname
# =============================================================================

@router.message(OnboardingStates.WAITING_FOR_NICKNAME)
async def onboarding_nickname(message: Message, state: FSMContext) -> None:
    """
    Step 1: Capture user's nickname.
    """
    nickname = message.text.strip()
    
    # Check for cancel
    if await is_cancel_request(message, state):
        return
    
    if not nickname or len(nickname) > 50:
        await message.answer(
            "❌ אופס, השם ארוך מדי או ריק.\n"
            "נסה שוב - כתוב שם או כינוי (עד 50 תווים):"
        )
        return
    
    # Save to FSM storage
    await state.update_data(nickname=nickname)
    
    # Move to agent name step
    await state.set_state(OnboardingStates.WAITING_FOR_AGENT_NAME)
    await message.answer(
        f"מעולה {nickname}! 👋\n\n"
        "🤖 *ואיך בא לך לקרוא לי?*\n\n"
        "_(תן לי שם, למשל: ג'רוויס, אלפרד, או סתם 'הבוט')_",
        parse_mode="Markdown"
    )


# =============================================================================
# Step 2: Agent Nickname
# =============================================================================

@router.message(OnboardingStates.WAITING_FOR_AGENT_NAME)
async def onboarding_agent_name(message: Message, state: FSMContext) -> None:
    """
    Step 2: Capture agent's nickname preference.
    """
    agent_name = message.text.strip()
    
    # Check for cancel
    if await is_cancel_request(message, state):
        return
    
    if not agent_name or len(agent_name) > 50:
        await message.answer("❌ השם ארוך מדי או ריק. נסה שוב:")
        return

    # Save to FSM storage
    await state.update_data(agent_nickname=agent_name)
    
    await message.answer(f"✅ מעולה, מעכשיו אני *{agent_name}*!", parse_mode="Markdown")

    # Move to gender step
    await state.set_state(OnboardingStates.WAITING_FOR_GENDER)
    await message.answer(
        "⚧ *כדי שאדע איך לפנות אליך - אתה גבר או אישה?*\n\n"
        "_(זה רק לניסוח נכון בעברית)_",
        parse_mode="Markdown",
        reply_markup=get_gender_keyboard()
    )


# =============================================================================
# Step 3: Gender
# =============================================================================

@router.callback_query(OnboardingStates.WAITING_FOR_GENDER, F.data.startswith("gender_"))
async def onboarding_gender(callback: CallbackQuery, state: FSMContext) -> None:
    """
    Step 3: Capture gender selection.
    """
    gender = callback.data.replace("gender_", "")  # "male" or "female"
    
    # Save to FSM storage
    await state.update_data(gender=gender)
    
    # Acknowledge callback
    await callback.answer()
    
    # Edit message to show selection
    gender_text = "זכר" if gender == "male" else "נקבה"
    await callback.message.edit_text(f"✅ בחרת: {gender_text}")

    # Move to Reminder Mode step
    await state.set_state(OnboardingStates.WAITING_FOR_REMINDER_MODE)
    await callback.message.answer(
        "🔔 *מצב תזכורות*\n\n"
        "האם תרצה שאפעיל עבורך את *'מצב תזכורות'*?\n\n"
        "כשתבקש ממני להזכיר לך משהו, אקבע זאת ביומן עם הקידומת "
        "_'תזכורת:'_ ובצבע כתום — כדי שתוכל להבחין בין תזכורות לאירועים רגילים.",
        parse_mode="Markdown",
        reply_markup=get_yes_no_keyboard("reminder_mode")
    )


# =============================================================================
# Step 4: Reminder Mode
# =============================================================================

@router.callback_query(OnboardingStates.WAITING_FOR_REMINDER_MODE, F.data.startswith("reminder_mode_"))
async def onboarding_reminder_mode(callback: CallbackQuery, state: FSMContext) -> None:
    """
    Step 4: Enable or disable Reminder Mode.
    When ON, reminder requests get a 'תזכורת:' prefix and orange color in the calendar.
    """
    enable = callback.data == "reminder_mode_yes"

    await callback.answer()
    await state.update_data(reminder_mode=enable)

    status = "כן ✅" if enable else "לא ❌"
    await callback.message.edit_text(f"מצב תזכורות: {status}")

    # Move to daily briefing step
    await state.set_state(OnboardingStates.WAITING_FOR_DAILY_BRIEFING)
    await send_daily_briefing_prompt(callback.message)


# =============================================================================
# Step 5: Daily Briefing
# =============================================================================

async def send_daily_briefing_prompt(message) -> None:
    """Helper to send the daily briefing question."""
    await message.answer(
        "☀️ *דיווח בוקרי*\n\n"
        "בא לך שאשלח לך כל בוקר ב-8:00 הודעה מתומצת עם הלוז להיום? ☀️",
        parse_mode="Markdown",
        reply_markup=get_yes_no_keyboard("daily_briefing")
    )


@router.callback_query(OnboardingStates.WAITING_FOR_DAILY_BRIEFING, F.data.startswith("daily_briefing_"))
async def onboarding_daily_briefing(callback: CallbackQuery, state: FSMContext) -> None:
    """
    Step 5: Enable/disable daily morning briefing.
    If enabled, shows instant preview of today's schedule.
    """
    enable = callback.data == "daily_briefing_yes"
    
    await callback.answer()
    await state.update_data(daily_briefing=enable)
    
    if enable:
        await callback.message.edit_text("דיווח יומי: כן ✅")
        
        # INSTANT GRATIFICATION: Show today's schedule as a preview
        # Get user tokens for the preview
        user_id = callback.from_user.id
        user_data = firestore_service.get_user(user_id)
        
        if user_data:
            calendar_config = user_data.get("calendar_config", {})
            refresh_token = calendar_config.get("refresh_token")
            
            if refresh_token:
                user_tokens = {
                    "access_token": calendar_config.get("access_token"),
                    "refresh_token": refresh_token
                }
                
                try:
                    result = calendar_service.get_today_events(
                        user_tokens=user_tokens,
                        user_id=str(user_id)
                    )
                    
                    if result.get("status") == "success":
                        formatted = calendar_service.format_today_events(result.get("events", []))
                        if formatted:
                            await callback.message.answer(
                                "מעולה! הנה דוגמה למה שתקבל ממני כל בוקר: \u2600\ufe0f\n\n"
                                f"{formatted}",
                                parse_mode="Markdown"
                            )
                        else:
                            await callback.message.answer(
                                "✅ הדיווח היומי הופעל!\n"
                                "אין לך אירועים היום, אבל מחר תתחיל לקבל דיווחים! \ud83d\udcc5"
                            )
                except Exception as e:
                    print(f"[Onboarding] Briefing preview failed: {e}")
                    await callback.message.answer("✅ הדיווח היומי הופעל!")
    else:
        await callback.message.edit_text("דיווח יומי: לא ❌")
    
    # Move to colors step
    await state.set_state(OnboardingStates.WAITING_FOR_COLORS)
    await send_colors_prompt(callback.message)


async def send_colors_prompt(message: Message) -> None:
    """Helper to send the colors prompt."""
    await message.answer(
        "🎨 *צבעים לאירועים*\n\n"
        "בוא נארגן את היומן שלך!\n"
        "ספר לי אילו צבעים להשתמש לאיזה סוג אירוע.\n\n"
        "_לדוגמה:_\n"
        "_צהוב לספורט_\n"
        "_אדום לעבודה_\n"
        "_כחול ללימודים_\n\n"
        "או שלח 'דלג' לדלג על השלב הזה:",
        parse_mode="Markdown"
    )


# =============================================================================
# Step 6: Event Colors
# =============================================================================

@router.message(OnboardingStates.WAITING_FOR_COLORS)
async def onboarding_colors(message: Message, state: FSMContext) -> None:
    """
    Step 6: Capture color preferences.
    """
    text = message.text.strip()
    
    # Check for cancel
    if await is_cancel_request(message, state):
        return
    
    if text.lower() in ["דלג", "skip", "לדלג"]:
        await state.update_data(colors_raw="")
        await message.answer("✅ דילגת על צבעים")
    else:
        await state.update_data(colors_raw=text)
        await message.answer(f"✅ שמרתי: {text[:50]}..." if len(text) > 50 else f"✅ שמרתי: {text}")
    
    # Move to contacts step
    await state.set_state(OnboardingStates.WAITING_FOR_CONTACTS)
    await message.answer(
        "👥 *אנשי קשר*\n\n"
        "אחרון חביב! מי החברים הקרובים שלך?\n"
        "אני צריך את המיילים שלהם כדי להזמין אותם לאירועים.\n\n"
        "⚠️ *חשוב:* כתוב את התשובה הזו בטקסט (לא הודעה קולית).\n\n"
        "_פורמט: שם: מייל_\n"
        "_לדוגמה:_\n"
        "_דני: dani@gmail.com_\n"
        "_שרה: sarah@example.com_\n\n"
        "או שלח 'דלג' לדלג:",
        parse_mode="Markdown"
    )


# =============================================================================
# Step 7: Contacts (Final Step)
# =============================================================================

@router.message(OnboardingStates.WAITING_FOR_CONTACTS)
async def onboarding_contacts(message: Message, state: FSMContext) -> None:
    """
    Step 7 (Final): Capture contacts and finalize onboarding.
    """
    text = message.text.strip()
    user_id = message.from_user.id
    
    # Check for cancel
    if await is_cancel_request(message, state):
        return
    
    # Parse contacts or skip
    contacts = {}
    if text.lower() not in ["דלג", "skip", "לדלג"]:
        lines = text.split("\n")
        for line in lines:
            if ":" in line:
                parts = line.split(":", 1)
                if len(parts) == 2:
                    name = parts[0].strip()
                    email = parts[1].strip()
                    if name and email and "@" in email:
                        contacts[name] = email
        
        await state.update_data(contacts=contacts)
        if contacts:
            await message.answer(f"✅ שמרתי {len(contacts)} אנשי קשר")
        else:
            await message.answer("✅ לא זיהיתי אנשי קשר, אפשר להוסיף מאוחר יותר")
    else:
        await message.answer("✅ דילגת על אנשי קשר")
    
    # Get all collected data
    data = await state.get_data()
    nickname = data.get("nickname", "חבר")
    agent_nickname = data.get("agent_nickname", "הבוט")
    gender = data.get("gender", "neutral")
    enable_reminders = data.get("enable_reminders", False)
    enable_daily_check = data.get("enable_daily_check", False)
    daily_check_hour = data.get("daily_check_hour")
    colors_raw = data.get("colors_raw", "")
    contacts_data = data.get("contacts", contacts)
    
    # Parse colors into color_map
    color_map = {}
    if colors_raw:
        color_map["_raw"] = colors_raw
    
    # Single Firestore update with all collected data
    firestore_service.update_user(user_id, {
        "personal_info.nickname": nickname,
        "personal_info.agent_nickname": agent_nickname,
        "personal_info.gender": gender,
        "preferences.reminder_mode": data.get("reminder_mode", False),
        "preferences.daily_briefing": data.get("daily_briefing", False),
        "calendar_config.color_map": color_map,
        "contacts": contacts_data,
        "onboarding_completed": True
    })
    
    print(f"[Onboarding] Completed for user {user_id}. Agent: {agent_nickname}, Nickname: {nickname}")
    
    # Clear FSM state
    await state.clear()
    
    # Send completion message
    await message.answer(
        f"🎉 מעולה {nickname}, סיימנו!\n\n"
        f"מעכשיו אתה יכול לקרוא לי *{agent_nickname}*.\n"
        "🚀 אני פה לכל מה שצריך - יומן, תזכורות, משימות.\n\n",
        parse_mode="Markdown"
    )