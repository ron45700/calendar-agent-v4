"""
Command handlers for Agentic Calendar 2.0
Handles /start, /auth, /me, /settings commands.
"""

from typing import Optional
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from models.user import UserData
from services.auth_service import auth_service
from services.firestore_service import firestore_service
from bot.states import OnboardingStates
from bot.keyboards import get_start_skip_keyboard, get_onboarding_confirm_keyboard


# Create router for command handlers
router = Router(name="commands_router")


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
    """
    Check if user needs to complete onboarding questionnaire.
    Only for NEW users who haven't completed it yet.
    Re-auth users (existing but expired tokens) should NOT see onboarding.
    """
    if not user:
        return False  # Not registered yet, can't onboard
    return not user.get("onboarding_completed", False)


# =============================================================================
# Command Handlers
# =============================================================================

@router.message(Command("start"))
async def cmd_start(message: Message, user: Optional[UserData], state: FSMContext) -> None:
    """
    Handle /start command.
    Works for both anonymous and registered users.
    Shows chatty onboarding intro for new users.
    """
    first_name = message.from_user.first_name or "שם"
    
    # Clear any existing state
    await state.clear()
    
    if not is_registered(user):
        # Anonymous user - not in DB yet
        await message.answer(
            f"היי {first_name}! 👋\n\n"
            "אני הסוכן החכם שלך לניהול יומן! 🤖\n\n"
            "כדי להתחיל, אני צריך שתתחבר עם חשבון Google.\n"
            "שלח /auth כדי להתחבר."
        )
        return
    
    # Registered user
    nickname = user.get("personal_info", {}).get("nickname") or first_name
    
    if needs_onboarding(user):
        # Show chatty onboarding intro with confirmation buttons
        await state.set_state(OnboardingStates.WAITING_FOR_CONFIRMATION)
        await message.answer(
            "🎉 איזה כיף שנרשמת!\n\n"
            "זורם לעשות שאלון קליל שיעזור לי להתאים את הדברים אישית אליך?\n\n"
            "_(זה לוקח פחות מדקה, מבטיח!)_",
            parse_mode="Markdown",
            reply_markup=get_onboarding_confirm_keyboard()
        )
    elif not has_valid_tokens(user):
        # Existing user but tokens expired/revoked - RE-AUTH (not onboarding!)
        await message.answer(
            f"היי {nickname}! 👋\n\n"
            "נראה שההרשאה שלך פגה. אין בעיה!\n"
            "שלח /auth כדי להתחבר מחדש."
        )
    else:
        # Fully set up user - use agent nickname if available
        agent_name = user.get("personal_info", {}).get("agent_nickname") or "הבוט"
        await message.answer(
            f"היי {nickname}! 👋\n\n"
            f"אני *{agent_name}*, מוכן לעזור לך לנהל את היומן שלך! 📅\n\n"
            "מה תרצה לעשות?",
            parse_mode="Markdown"
        )


@router.message(Command("settings"))
async def cmd_settings(message: Message, user: Optional[UserData], state: FSMContext) -> None:
    """
    Handle /settings command.
    Allows user to redo their profile and preferences (same flow as onboarding).
    """
    if not is_registered(user):
        await message.answer(
            "❌ אין לי מידע עליך עדיין.\n"
            "שלח /auth כדי להתחבר ולהתחיל."
        )
        return
    
    if not has_valid_tokens(user):
        await message.answer(
            "🔐 ההרשאה שלך פגה.\n"
            "שלח /auth כדי להתחבר מחדש."
        )
        return
    
    # Clear any existing state and start settings flow
    await state.clear()
    
    nickname = user.get("personal_info", {}).get("nickname") or message.from_user.first_name
    
    await message.answer(
        f"⚙️ *הגדרות* - היי {nickname}!\n\n"
        "בוא נעדכן את ההעדפות שלך.\n"
        "אם תרצה לשמור על ערך קיים, פשוט כתוב 'דלג'.\n\n"
        "*איך לקרוא לך?*\n"
        f"_(כרגע: {nickname})_",
        parse_mode="Markdown"
    )
    
    # Start the questionnaire FSM
    await state.set_state(OnboardingStates.WAITING_FOR_NICKNAME)


@router.message(Command("auth"))
async def cmd_auth(message: Message, user: Optional[UserData]) -> None:
    """
    Handle /auth command.
    Generates OAuth URL for Google Calendar authentication.
    Works for both new users and re-authentication.
    """
    user_id = message.from_user.id
    
    if is_registered(user) and has_valid_tokens(user):
        # Already authenticated
        await message.answer(
            "✅ אתה כבר מחובר ל-Google Calendar!\n\n"
            "אם אתה רוצה להתחבר עם חשבון אחר, "
            "קודם בטל את ההרשאה הקיימת בהגדרות Google."
        )
        return
    
    # Generate OAuth URL
    auth_url = auth_service.generate_auth_url(user_id)
    
    await message.answer(
        "🔐 *התחברות ל-Google Calendar*\n\n"
        "לחץ על הלינק הבא כדי להתחבר:\n\n"
        f"[לחץ כאן להתחברות]({auth_url})\n\n"
        "_לאחר ההתחברות תקבל אישור כאן בצ'אט._",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


@router.message(Command("me"))
async def cmd_me(message: Message, user: Optional[UserData]) -> None:
    """
    Handle /me command.
    Shows user profile and settings.
    """
    if not is_registered(user):
        await message.answer(
            "❌ אין לי מידע עליך עדיין.\n"
            "שלח /auth כדי להתחבר ולהתחיל."
        )
        return
    
    personal_info = user.get("personal_info", {})
    calendar_config = user.get("calendar_config", {})
    
    nickname = personal_info.get("nickname") or "לא הוגדר"
    agent_nickname = personal_info.get("agent_nickname") or "הבוט"
    gender = personal_info.get("gender") or "לא הוגדר"
    gender_display = {"male": "זכר", "female": "נקבה", "neutral": "לא הוגדר"}.get(gender, gender)
    
    has_tokens = "✅" if calendar_config.get("refresh_token") else "❌"
    daily_check_hour = calendar_config.get("daily_check_hour")
    daily_check_display = f"{daily_check_hour}:00" if daily_check_hour else "לא מוגדר"
    
    enable_reminders = "✅" if user.get("enable_reminders") else "❌"
    enable_daily_check = "✅" if user.get("enable_daily_check") else "❌"
    onboarding = "✅" if user.get("onboarding_completed") else "❌"
    
    # Colors and contacts count
    color_map = calendar_config.get("color_map", {})
    colors_count = len(color_map) if color_map else 0
    contacts = user.get("contacts", {})
    contacts_count = len(contacts) if contacts else 0
    
    profile_text = (
        "👤 *הפרופיל שלך*\n\n"
        f"🆔 ID: `{user.get('user_id')}`\n"
        f"📛 כינוי שלך: {nickname}\n"
        f"🤖 שם הסוכן: {agent_nickname}\n"
        f"⚧ מגדר: {gender_display}\n\n"
        "*הגדרות:*\n"
        f"🔔 תזכורות: {enable_reminders}\n"
        f"📋 בדיקה יומית: {enable_daily_check}\n"
        f"⏰ שעת בדיקה: {daily_check_display}\n"
        f"🎨 צבעים מוגדרים: {colors_count}\n"
        f"👥 אנשי קשר: {contacts_count}\n\n"
        "*סטטוס:*\n"
        f"🔐 מחובר ל-Google: {has_tokens}\n"
        f"✨ הדרכה הושלמה: {onboarding}\n\n"
        "_לעדכון הגדרות שלח /settings_"
    )
    
    await message.answer(profile_text, parse_mode="Markdown")


@router.message(Command("toggle_briefing"))
async def cmd_toggle_briefing(message: Message, user: Optional[UserData]) -> None:
    """
    Handle /toggle_briefing command.
    Toggles the daily morning briefing on/off.
    """
    if not is_registered(user):
        await message.answer(
            "❌ אתה צריך להתחבר קודם.\n"
            "שלח /auth כדי להתחבר."
        )
        return
    
    user_id = message.from_user.id
    
    # Read current state
    current = user.get("preferences", {}).get("daily_briefing", False)
    new_value = not current
    
    # Update Firestore
    firestore_service.update_user(user_id, {
        "preferences.daily_briefing": new_value
    })
    
    if new_value:
        await message.answer(
            "☀️ הדיווח היומי הופעל בהצלחה! ✅\n\n"
            "כל בוקר ב-08:00 תקבל ממני סיכום של הלו\"ז שלך להיום."
        )
    else:
        await message.answer(
            "🌙 הדיווח היומי כובה בהצלחה! ✅\n\n"
            "לא אשלח יותר הודעות בוקר. אפשר להפעיל שוב בכל עת."
        )

