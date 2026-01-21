"""
Inline keyboards for Agentic Calendar 2.0
Reusable keyboard builders for common UI patterns.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_gender_keyboard() -> InlineKeyboardMarkup:
    """
    Create gender selection keyboard.
    
    Returns:
        Inline keyboard with Male/Female buttons
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👨 זכר", callback_data="gender_male"),
        InlineKeyboardButton(text="👩 נקבה", callback_data="gender_female")
    )
    return builder.as_markup()


def get_yes_no_keyboard(prefix: str) -> InlineKeyboardMarkup:
    """
    Create Yes/No keyboard with custom callback prefix.
    
    Args:
        prefix: Prefix for callback data (e.g., "reminders" -> "reminders_yes", "reminders_no")
        
    Returns:
        Inline keyboard with Yes/No buttons
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ כן", callback_data=f"{prefix}_yes"),
        InlineKeyboardButton(text="❌ לא", callback_data=f"{prefix}_no")
    )
    return builder.as_markup()


def get_start_skip_keyboard() -> InlineKeyboardMarkup:
    """
    Create onboarding start/skip keyboard.
    Shown when user first logs in - option to set up profile or skip.
    
    Returns:
        Inline keyboard with Start Setup / Skip buttons
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🛠️ להתחיל הגדרות", callback_data="onboarding_start"),
        InlineKeyboardButton(text="🚀 לדלג בינתיים", callback_data="onboarding_skip")
    )
    return builder.as_markup()


def get_time_selection_keyboard() -> InlineKeyboardMarkup:
    """
    Create time selection keyboard for daily check.
    Shows hours from 07:00 to 22:00 with a cancel button.
    
    Returns:
        Inline keyboard with hour buttons in 2 columns + cancel
    """
    builder = InlineKeyboardBuilder()
    
    # Add hour buttons (07:00 - 22:00)
    hours = [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22]
    
    for hour in hours:
        time_str = f"{hour:02d}:00"
        builder.button(text=time_str, callback_data=f"daily_time_{hour}")
    
    # Adjust to 4 columns for hours
    builder.adjust(4)
    
    # Add cancel button on new row
    builder.row(
        InlineKeyboardButton(text="❌ ביטול", callback_data="daily_time_cancel")
    )
    
    return builder.as_markup()


def get_onboarding_confirm_keyboard() -> InlineKeyboardMarkup:
    """
    Create keyboard for onboarding confirmation.
    User chooses to start now or later.
    
    Returns:
        Inline keyboard with "יאללה" / "אחר כך" buttons
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="יאללה! 🚀", callback_data="onboarding_confirm_yes"),
        InlineKeyboardButton(text="אחר כך 😴", callback_data="onboarding_confirm_later")
    )
    return builder.as_markup()


def get_confirmation_keyboard() -> InlineKeyboardMarkup:
    """
    Create confirmation keyboard for final steps.
    
    Returns:
        Inline keyboard with Confirm/Cancel buttons
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ אישור", callback_data="confirm_yes"),
        InlineKeyboardButton(text="❌ ביטול", callback_data="confirm_no")
    )
    return builder.as_markup()
