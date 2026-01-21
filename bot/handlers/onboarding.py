"""
Onboarding FSM handlers for Agentic Calendar 2.0
Multi-step conversational questionnaire for new user setup.

Flow: Confirmation → Nickname → Agent Name → Gender → Reminders → Daily Check → Colors → Contacts → Complete
"""

from typing import Optional
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from models.user import UserData
from services.firestore_service import firestore_service
from bot.states import OnboardingStates
from bot.keyboards import (
    get_gender_keyboard,
    get_yes_no_keyboard,
    get_time_selection_keyboard,
    get_onboarding_confirm_keyboard
)


# Create router for onboarding handlers
router = Router(name="onboarding_router")


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
    
    # Move to reminders step
    await state.set_state(OnboardingStates.WAITING_FOR_REMINDERS)
    await callback.message.answer(
        "🔔 *תזכורות*\n\n"
        "האם תרצה להפעיל שירות שבו תוכל לבקש ממני להזכיר לך דברים בשעה מסוימת?",
        parse_mode="Markdown",
        reply_markup=get_yes_no_keyboard("reminders")
    )


# =============================================================================
# Step 4: Reminders
# =============================================================================

@router.callback_query(OnboardingStates.WAITING_FOR_REMINDERS, F.data.startswith("reminders_"))
async def onboarding_reminders(callback: CallbackQuery, state: FSMContext) -> None:
    """
    Step 4: Enable/disable reminders.
    """
    enable = callback.data == "reminders_yes"
    
    # Save to FSM storage
    await state.update_data(enable_reminders=enable)
    
    # Acknowledge callback
    await callback.answer()
    
    # Edit message to show selection
    status = "כן ✅" if enable else "לא ❌"
    await callback.message.edit_text(f"תזכורות: {status}")
    
    # Move to daily check step
    await state.set_state(OnboardingStates.WAITING_FOR_DAILY_CHECK)
    await callback.message.answer(
        "📋 *בדיקה יומית*\n\n"
        "האם תרצה להפעיל שירות שבו תוכל להגיד לי 'תרשום לי משימה', "
        "ואני אבדוק איתך מאוחר יותר אם ביצעת אותן?",
        parse_mode="Markdown",
        reply_markup=get_yes_no_keyboard("daily_check")
    )


# =============================================================================
# Step 5: Daily Check (with optional time selection)
# =============================================================================

@router.callback_query(OnboardingStates.WAITING_FOR_DAILY_CHECK, F.data.startswith("daily_check_"))
async def onboarding_daily_check(callback: CallbackQuery, state: FSMContext) -> None:
    """
    Step 5: Enable/disable daily check.
    If enabled, asks for preferred time.
    """
    enable = callback.data == "daily_check_yes"
    
    # Acknowledge callback
    await callback.answer()
    
    if enable:
        # Save to FSM storage (will be confirmed after time selection)
        await state.update_data(enable_daily_check=True)
        
        # Edit message
        await callback.message.edit_text("בדיקה יומית: כן ✅")
        
        # Ask for time
        await state.set_state(OnboardingStates.WAITING_FOR_DAILY_TIME)
        await callback.message.answer(
            "⏰ *באיזו שעה נוח לך?*\n\n"
            "בחר את השעה ביום שבה אשלח לך הודעה ואבדוק איתך האם ביצעת את המשימות.",
            parse_mode="Markdown",
            reply_markup=get_time_selection_keyboard()
        )
    else:
        # Skip time selection
        await state.update_data(enable_daily_check=False, daily_check_hour=None)
        
        # Edit message
        await callback.message.edit_text("בדיקה יומית: לא ❌")
        
        # Move to colors step
        await state.set_state(OnboardingStates.WAITING_FOR_COLORS)
        await send_colors_prompt(callback.message)


@router.callback_query(OnboardingStates.WAITING_FOR_DAILY_TIME, F.data.startswith("daily_time_"))
async def onboarding_daily_time(callback: CallbackQuery, state: FSMContext) -> None:
    """
    Step 5b: Capture daily check time selection.
    """
    time_data = callback.data.replace("daily_time_", "")
    
    # Acknowledge callback
    await callback.answer()
    
    if time_data == "cancel":
        # User cancelled - disable daily check
        await state.update_data(enable_daily_check=False, daily_check_hour=None)
        await callback.message.edit_text("בדיקה יומית: בוטל ❌")
    else:
        # Save selected hour
        hour = int(time_data)
        await state.update_data(enable_daily_check=True, daily_check_hour=hour)
        await callback.message.edit_text(f"⏰ בדיקה יומית: {hour:02d}:00 ✅")
    
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
        "enable_reminders": enable_reminders,
        "enable_daily_check": enable_daily_check,
        "calendar_config.daily_check_hour": daily_check_hour,
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
        "אני פה לכל מה שצריך - יומן, תזכורות, משימות.\n\n"
        "יאללה, מה עושים? 🚀",
        parse_mode="Markdown"
    )