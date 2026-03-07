"""
Daily Briefing Job for Agentic Calendar 2.0
Sends proactive morning schedule messages to opted-in users.

Triggered by Cloud Scheduler via POST /tasks/daily-briefing.
"""

import logging
import random
from typing import Optional
from aiogram import Bot

from services.firestore_service import firestore_service
from services.calendar_service import calendar_service, ERROR_AUTH_REQUIRED

logger = logging.getLogger(__name__)


# Pool of motivational messages for days with no events.
# Randomly selected so daily users don’t see the same text every morning.
EMPTY_SCHEDULE_MESSAGES = [
    "🌟 אין אירועים היום! יום מעולה להגדיר משהו חדש ולמלא את היום. מה תיקבע? 😊",
    "☕ יום פנוי! זה זמן מעולה לנשום, לתכנן, או פשוט לעשות משהו טוב לעצמך. היום שלך!",
    "🚀 לוח של ולוחות! אין לך אירועים היום — זה בדיוק הזמן המתאים להציב משהו שאתה דוחה לאורך זמן.",
    "🌱 יום פנוי = הזדמנות שלך! תקבע פגישה, צא לאוויר, או פשוט תנוח קצת.",
    "💪 אין אירועים היום, אבל היום הוא שלך! מרגיש כשרוצה לתכנן את השבוע הבא.",
    "✨ יום ללא לוח זאתה מתנה קטנה! אולי זה הזמן לעשות משהו שמתמיד דחית אתה 🙂",
]


async def send_daily_briefing_job(bot: Bot) -> dict:
    """
    Send daily morning briefing to all opted-in users.
    
    Logic:
    1. Query Firestore for users with preferences.daily_briefing == True
    2. For each user: fetch today's events, format, and send via Telegram
    3. Skip users with auth errors (expired tokens)
    4. Never crash the whole loop - each user is wrapped in try/except
    
    Args:
        bot: Telegram bot instance for sending messages
        
    Returns:
        Summary dict with counts: {sent, skipped, errors, total}
    """
    logger.info("[Briefing] 🌅 Starting daily briefing job...")
    
    # Query users with daily_briefing enabled
    try:
        users_ref = firestore_service.db.collection("users").where(
            "preferences.daily_briefing", "==", True
        ).stream()
        users = list(users_ref)
    except Exception as e:
        logger.error(f"[Briefing] ❌ Failed to query users: {e}")
        return {"sent": 0, "skipped": 0, "errors": 1, "total": 0}
    
    total = len(users)
    sent = 0
    skipped = 0
    errors = 0
    
    logger.info(f"[Briefing] Found {total} users with daily briefing enabled")
    
    for user_doc in users:
        user_id = user_doc.id
        user_data = user_doc.to_dict()
        
        try:
            # Get user tokens
            calendar_config = user_data.get("calendar_config", {})
            refresh_token = calendar_config.get("refresh_token")
            
            if not refresh_token:
                logger.warning(f"[Briefing] User {user_id}: No refresh token, skipping")
                skipped += 1
                continue
            
            user_tokens = {
                "access_token": calendar_config.get("access_token"),
                "refresh_token": refresh_token
            }
            
            # Fetch today's events
            result = calendar_service.get_today_events(
                user_tokens=user_tokens,
                user_id=user_id
            )
            
            # Handle auth errors - skip user silently
            if result.get("status") != "success":
                error_type = result.get("type", "")
                if error_type == ERROR_AUTH_REQUIRED:
                    logger.warning(f"[Briefing] User {user_id}: Auth expired, skipping")
                else:
                    logger.warning(f"[Briefing] User {user_id}: API error, skipping")
                skipped += 1
                continue
            
            # Format events
            events = result.get("events", [])
            formatted = calendar_service.format_today_events(events)
            
            if not formatted:
                # No events today — send a motivational message instead of staying silent
                nickname = user_data.get("personal_info", {}).get("nickname", "")
                greeting = f"בוקר טוב{'  ' + nickname if nickname else ''}! ☀️"
                motivation = random.choice(EMPTY_SCHEDULE_MESSAGES)
                empty_msg = f"{greeting}\n{motivation}"

                await bot.send_message(
                    chat_id=int(user_id),
                    text=empty_msg
                )
                logger.info(f"[Briefing] ✨ Sent motivational message to user {user_id} (no events today)")
                sent += 1
                continue
            
            # Build and send message
            nickname = user_data.get("personal_info", {}).get("nickname", "")
            greeting = f"בוקר טוב{' ' + nickname if nickname else ''}! ☀️"
            
            message = (
                f"{greeting}\n"
                f"הנה הלו\"ז שלך להיום:\n\n"
                f"{formatted}"
            )
            
            await bot.send_message(
                chat_id=int(user_id),
                text=message,
                parse_mode="Markdown"
            )
            
            logger.info(f"[Briefing] ✅ Sent to user {user_id} ({len(events)} events)")
            sent += 1
            
        except Exception as e:
            logger.error(f"[Briefing] ❌ Error for user {user_id}: {e}")
            errors += 1
    
    summary = {"sent": sent, "skipped": skipped, "errors": errors, "total": total}
    logger.info(f"[Briefing] 🏁 Job complete: {summary}")
    return summary
