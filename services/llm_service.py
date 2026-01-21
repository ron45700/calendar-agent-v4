"""
LLM Service for Agentic Calendar 2.0
Handles natural language event parsing using OpenAI function calling.
"""

import json
from datetime import datetime
from typing import Optional, List, Dict, Any

from services.openai_service import openai_service


# =============================================================================
# Event Parsing Schema (OpenAI Function Calling)
# =============================================================================

EVENT_PARSING_FUNCTION = {
    "name": "create_calendar_event",
    "description": "Extract structured event data from natural language text for Google Calendar",
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "The event title/summary"
            },
            "start_time": {
                "type": "string",
                "description": "Event start time in ISO 8601 format (e.g., 2026-01-21T14:00:00)"
            },
            "end_time": {
                "type": "string",
                "description": "Event end time in ISO 8601 format. If duration not specified, default to 1 hour after start"
            },
            "description": {
                "type": "string",
                "description": "Event description (only if explicitly mentioned in the text)"
            },
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of attendee names mentioned in the text"
            },
            "category": {
                "type": "string",
                "enum": ["work", "meeting", "personal", "family", "health", "sport", "study", "fun", "other"],
                "description": "Suggested category for the event based on context"
            },
            "is_all_day": {
                "type": "boolean",
                "description": "True if the user said it is an all-day event"
            },
            "location": {
                "type": "string",
                "description": "Event location if mentioned"
            }
        },
        "required": ["summary", "start_time", "end_time", "category"]
    }
}

PARSING_SYSTEM_PROMPT = """You are an advanced NLU (Natural Language Understanding) engine for a Calendar Agent.
Your goal is to extract structured calendar event data from Hebrew user input.

**Context:**
- **Current Reference Time:** {current_time} (Timezone: Asia/Jerusalem).
- **User's Contacts:** {contacts} (List of known names).

**Extraction Rules:**
1.  **Summary:** Extract the event title from the text. Keep it in Hebrew unless the user wrote in English.
2.  **Timing (Crucial):**
    - Calculate `start_time` relative to the **Current Reference Time**.
    - Handle Hebrew relative terms accurately:
      - "מחר" (Tomorrow) -> Current Date + 1 day.
      - "עוד שעה" (In an hour) -> Current Time + 1 hour.
      - "יום שלישי הבא" (Next Tuesday).
    - If no specific hour is mentioned (e.g., "Birthday on the 5th"), mark `is_all_day` as true.
3.  **Duration:**
    - Calculate `end_time` based on the user's input (e.g., "for two hours").
    - **Default:** If no duration is specified, assume exactly **1 hour** from start time.
4.  **Attendees:**
    - Scan the text for names present in the **User's Contacts** list.
    - Only include names that strictly match (fuzzy match is allowed for nicknames if obvious).
5.  **Description:**
    - Only add a `description` if the user explicitly dictates details (e.g., "write in description that..."). Otherwise, leave empty.
6.  **Category:**
    - Classify the event into one of these types: "work", "personal", "family", "health", "social", "education".

**Output Requirements:**
- Return a valid JSON object.
- **Dates:** Must be in ISO 8601 format (YYYY-MM-DDTHH:MM:SS).
- **Timezone:** Ensure calculation considers Israel Standard Time (IST).

**Few-Shot Examples (Hebrew Input -> JSON Logic):**

Input: "פגישה עם דני מחר ב-15:00"
Logic: "מחר" means (Current Day + 1). Time is 15:00. Duration default 1h.
Output: {{"summary": "פגישה עם דני", "start_time": "2024-01-22T15:00:00", "end_time": "2024-01-22T16:00:00", "attendees": ["דני"], "is_all_day": false}}

Input: "יש לי חדר כושר עוד שעתיים"
Logic: "עוד שעתיים" means (Current Time + 2 hours). Duration default 1h. Category: health/sport.
Output: {{"summary": "חדר כושר", "start_time": "[Calculated]", "end_time": "[Calculated + 1h]", "category": "health"}}

Input: "יום הולדת לאמא ב-25 לחודש"
Logic: Specific date, no time specified.
Output: {{"summary": "יום הולדת לאמא", "start_time": "2024-01-25", "is_all_day": true, "category": "family"}}
"""

class LLMService:
    """
    Service for LLM-based natural language understanding.
    Uses OpenAI function calling for structured event extraction.
    """
    
    def __init__(self):
        """Initialize LLM service."""
        pass
    
    async def parse_event_from_text(
        self,
        text: str,
        current_time: str,
        contacts: Optional[Dict[str, str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Parse natural language text into structured event data.
        
        Args:
            text: User's natural language input (e.g., "פגישה עם דני מחר ב-15:00")
            current_time: Current datetime string for resolving relative times
            contacts: User's contact dict {name: email} for attendee resolution
            
        Returns:
            Structured event data dict or None if parsing failed
        """
        # Format contacts for the prompt
        contact_names = list(contacts.keys()) if contacts else []
        contacts_str = ", ".join(contact_names) if contact_names else "אין אנשי קשר שמורים"
        
        # Build system prompt
        system_prompt = PARSING_SYSTEM_PROMPT.format(
            current_time=current_time,
            contacts=contacts_str
        )
        
        # Build messages
        messages = [
            {"role": "user", "content": text}
        ]
        
        try:
            # Call OpenAI with function calling
            response = openai_service.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    *messages
                ],
                functions=[EVENT_PARSING_FUNCTION],
                function_call={"name": "create_calendar_event"},
                temperature=0.3  # Lower temperature for more deterministic parsing
            )
            
            # Extract function call result
            message = response.choices[0].message
            
            if message.function_call:
                function_args = json.loads(message.function_call.arguments)
                print(f"[LLM] Parsed event: {function_args}")
                
                # Resolve attendee names to emails
                if contacts and function_args.get("attendees"):
                    resolved_attendees = []
                    for name in function_args["attendees"]:
                        # Try to match contact name (case-insensitive)
                        for contact_name, email in contacts.items():
                            if name.lower() in contact_name.lower() or contact_name.lower() in name.lower():
                                resolved_attendees.append({
                                    "name": contact_name,
                                    "email": email
                                })
                                break
                    function_args["resolved_attendees"] = resolved_attendees
                
                return function_args
            else:
                print("[LLM] No function call in response")
                return None
                
        except Exception as e:
            print(f"[LLM] Error parsing event: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    async def confirm_event_details(
        self,
        event_data: Dict[str, Any],
        agent_name: str = "הבוט"
    ) -> str:
        """
        Generate a confirmation message for the parsed event.
        
        Args:
            event_data: Parsed event data
            agent_name: Bot's name for personalized response
            
        Returns:
            Hebrew confirmation message
        """
        summary = event_data.get("summary", "אירוע")
        start_time = event_data.get("start_time", "")
        end_time = event_data.get("end_time", "")
        attendees = event_data.get("attendees", [])
        category = event_data.get("category", "other")
        location = event_data.get("location", "")
        
        # Format time for display
        try:
            start_dt = datetime.fromisoformat(start_time)
            end_dt = datetime.fromisoformat(end_time)
            time_str = f"{start_dt.strftime('%d/%m/%Y %H:%M')} - {end_dt.strftime('%H:%M')}"
        except:
            time_str = f"{start_time} - {end_time}"
        
        # Build confirmation message
        msg = f"📅 *{summary}*\n"
        msg += f"⏰ {time_str}\n"
        
        if location:
            msg += f"📍 {location}\n"
        
        if attendees:
            msg += f"👥 משתתפים: {', '.join(attendees)}\n"
        
        # Category emoji mapping
        category_emoji = {
            "work": "💼",
            "meeting": "🤝",
            "personal": "👤",
            "family": "👨‍👩‍👧",
            "health": "🏥",
            "sport": "🏃",
            "study": "📚",
            "fun": "🎉",
            "other": "📌"
        }
        
        emoji = category_emoji.get(category, "📌")
        msg += f"\n{emoji} קטגוריה: {category}\n"
        
        return msg


# Singleton instance
llm_service = LLMService()
