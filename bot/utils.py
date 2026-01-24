"""
Utility functions for Agentic Calendar 2.0 bot.
"""

import random
from datetime import datetime


# =============================================================================
# Thinking Phrases (Hebrew - DO NOT TRANSLATE)
# =============================================================================

THINKING_PHRASES = [
    "על זה ברנש",
    "עוד רגע פאפסיטו",
    "עובד על זה יא לחוץ",
    "לעבד או לא לעבד ? זו השאלה האמיתית",
    "מתלבט אם לעזור...",
    "חושב , יושב , אוהב , שואב , כואב , רוכב , עורב",
    "לעבד או לאבד",
    "חומוס צ'יפס סלט"   
]


def get_random_thinking_phrase() -> str:
    """
    Get a random thinking phrase for the "processing" message.
    
    Returns:
        Random Hebrew phrase from THINKING_PHRASES
    """
    return random.choice(THINKING_PHRASES)


def get_formatted_current_time() -> str:
    """
    Get current time formatted for the system prompt.
    
    Returns:
        Formatted datetime string in Hebrew-friendly format
    """
    now = datetime.now()
    
    # Hebrew day names
    day_names = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
    day_name = day_names[now.weekday()]
    
    # Format: יום שני, 20 בינואר 2026, 21:30
    return f"יום {day_name}, {now.day}/{now.month}/{now.year}, {now.strftime('%H:%M')}"
