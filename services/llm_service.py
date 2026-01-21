"""
LLM Service for Agentic Calendar 2.0
Intelligent Agent - Intent Classification & Routing

Classifies user input into:
- create_event: Schedule calendar events
- set_reminder: Ad-hoc reminders (pings)
- reschedule_event: Move/postpone events
- edit_preferences: Settings changes
- chat: General conversation
"""

import json
from datetime import datetime
from typing import Optional, List, Dict, Any

from services.openai_service import openai_service
from services.prompts import ROUTER_SYSTEM_PROMPT, INTENT_FUNCTION_SCHEMA


class LLMService:
    """
    Intelligent Agent Service for intent classification and routing.
    Uses OpenAI function calling for structured intent extraction.
    """
    
    def __init__(self):
        """Initialize LLM service."""
        pass
    
    async def parse_user_intent(
        self,
        text: str,
        current_time: str,
        user_preferences: Optional[Dict[str, Any]] = None,
        contacts: Optional[Dict[str, str]] = None,
        history: Optional[List[Dict[str, str]]] = None,
        agent_name: str = "הבוט",
        user_nickname: str = "חבר"
    ) -> Dict[str, Any]:
        """
        Classify user intent and extract structured data.
        
        Args:
            text: User's natural language input
            current_time: Current datetime string for resolving relative times
            user_preferences: User's preference settings (colors, reminders, etc.)
            contacts: User's contact dict {name: email}
            history: Conversation history for context
            agent_name: Bot's name chosen by user
            user_nickname: User's nickname
            
        Returns:
            Dict with intent, response_text, and payload
        """
        # Format contacts for the prompt
        contact_names = list(contacts.keys()) if contacts else []
        contacts_str = ", ".join(contact_names) if contact_names else "אין אנשי קשר"
        
        # Format preferences
        prefs_str = json.dumps(user_preferences, ensure_ascii=False) if user_preferences else "{}"
        
        # Build system prompt from template
        system_prompt = ROUTER_SYSTEM_PROMPT.format(
            agent_name=agent_name,
            user_nickname=user_nickname,
            current_time=current_time,
            contacts=contacts_str,
            user_preferences=prefs_str
        )
        
        # Build messages with history
        messages = []
        if history:
            messages.extend(history[-10:])
        messages.append({"role": "user", "content": text})
        
        try:
            # Call OpenAI with function calling
            response = openai_service.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    *messages
                ],
                functions=[INTENT_FUNCTION_SCHEMA],
                function_call={"name": "classify_user_intent"},
                temperature=0.4
            )
            
            # Extract function call result
            message = response.choices[0].message
            
            if message.function_call:
                result = json.loads(message.function_call.arguments)
                print(f"[LLM] Intent: {result.get('intent')} | Payload: {result.get('payload', {})}")
                
                # Ensure payload exists
                if "payload" not in result:
                    result["payload"] = {}
                
                # Resolve attendee names to emails for create_event
                if result.get("intent") == "create_event" and contacts:
                    attendees = result.get("payload", {}).get("attendees", [])
                    if attendees:
                        resolved = []
                        for name in attendees:
                            for contact_name, email in contacts.items():
                                if name.lower() in contact_name.lower() or contact_name.lower() in name.lower():
                                    resolved.append({"name": contact_name, "email": email})
                                    break
                        result["payload"]["resolved_attendees"] = resolved
                
                return result
            else:
                # Fallback to chat intent
                return {
                    "intent": "chat",
                    "response_text": "לא הבנתי לגמרי, אפשר לנסח אחרת?",
                    "payload": {}
                }
                
        except Exception as e:
            print(f"[LLM] Error classifying intent: {e}")
            import traceback
            traceback.print_exc()
            
            return {
                "intent": "chat",
                "response_text": "אופס, משהו השתבש. נסה שוב?",
                "payload": {}
            }
    
    async def confirm_event_details(
        self,
        event_data: Dict[str, Any],
        agent_name: str = "הבוט"
    ) -> str:
        """
        Generate a confirmation message for the parsed event.
        
        Args:
            event_data: Parsed event data (from payload)
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
        is_task = event_data.get("is_task", False)
        
        # Format time for display
        try:
            start_dt = datetime.fromisoformat(start_time)
            end_dt = datetime.fromisoformat(end_time)
            time_str = f"{start_dt.strftime('%d/%m/%Y %H:%M')} - {end_dt.strftime('%H:%M')}"
        except:
            time_str = f"{start_time} - {end_time}"
        
        # Build confirmation message
        if is_task:
            msg = f"📋 *{summary}* (משימה)\n"
        else:
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
