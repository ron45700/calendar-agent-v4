"""
Google Calendar Service for Agentic Calendar 2.0
Handles Google Calendar API operations using user OAuth tokens.
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Tuple

from google.oauth2.credentials import Credentials
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.cloud import firestore

from config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
from utils.performance import measure_time


# =============================================================================
# Google Calendar Color IDs
# =============================================================================

# Google Calendar color IDs (1-11)
CALENDAR_COLORS = {
    "lavender": 1,      # Lavender
    "sage": 2,          # Sage
    "grape": 3,         # Grape
    "flamingo": 4,      # Flamingo
    "banana": 5,        # Banana
    "tangerine": 6,     # Tangerine
    "peacock": 7,       # Peacock (Cyan) - Default
    "graphite": 8,      # Graphite
    "blueberry": 9,     # Blueberry
    "basil": 10,        # Basil
    "tomato": 11        # Tomato
}

# Category to color mapping
CATEGORY_COLOR_MAP = {
    "work": 9,          # Blueberry
    "meeting": 7,       # Peacock (Cyan)
    "personal": 5,      # Banana
    "family": 4,        # Flamingo
    "health": 10,       # Basil
    "sport": 6,         # Tangerine
    "study": 3,         # Grape
    "fun": 11,          # Tomato
    "other": 8          # Graphite
}

DEFAULT_COLOR_ID = 7  # Peacock (Cyan)

# Error types for standardized returns
ERROR_AUTH_REQUIRED = "auth_required"
ERROR_GENERIC = "generic"


class CalendarService:
    """
    Service for Google Calendar API operations.
    Uses OAuth2 user credentials for calendar access.
    """
    
    def __init__(self):
        """Initialize Calendar service."""
        self._firestore_client = None
    
    @property
    def firestore_client(self):
        """Lazy-load Firestore client."""
        if self._firestore_client is None:
            self._firestore_client = firestore.Client()
        return self._firestore_client
    
    @measure_time
    def _get_calendar_service(
        self, 
        user_tokens: Dict[str, str],
        user_id: Optional[str] = None
    ) -> Tuple[Optional[Any], Optional[str]]:
        """
        Build Google Calendar API service from user tokens.
        
        Args:
            user_tokens: Dict containing access_token, refresh_token, etc.
            user_id: User ID for credential cleanup on failure
            
        Returns:
            Tuple of (service, error_status):
            - (service, None) on success
            - (None, "auth_required") if tokens are invalid/expired
        """
        try:
            credentials = Credentials(
                token=user_tokens.get("access_token"),
                refresh_token=user_tokens.get("refresh_token"),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=GOOGLE_CLIENT_ID,
                client_secret=GOOGLE_CLIENT_SECRET
            )
            
            # Check if credentials need refresh
            if credentials.expired or not credentials.valid:
                print("[Calendar] Credentials expired, attempting refresh...")
                try:
                    credentials.refresh(Request())
                    print("[Calendar] Token refresh successful")
                except RefreshError as e:
                    print(f"[Calendar] ⚠️ RefreshError: {e}")
                    print("[Calendar] Token is invalid/revoked. Clearing credentials.")
                    
                    # Delete invalid credentials from Firestore
                    if user_id:
                        self._clear_user_credentials(user_id)
                    
                    return None, ERROR_AUTH_REQUIRED
            
            return build("calendar", "v3", credentials=credentials), None
            
        except RefreshError as e:
            # Catch RefreshError during build() as well
            print(f"[Calendar] ⚠️ RefreshError during service build: {e}")
            if user_id:
                self._clear_user_credentials(user_id)
            return None, ERROR_AUTH_REQUIRED
            
        except Exception as e:
            print(f"[Calendar] Error building service: {e}")
            # Return sanitized error type, not raw exception
            return None, ERROR_GENERIC
    
    def _clear_user_credentials(self, user_id: str) -> None:
        """
        Delete invalid credentials from Firestore.
        
        Args:
            user_id: The Telegram user ID
        """
        try:
            print(f"[Calendar] Deleting invalid credentials for user {user_id}")
            self.firestore_client.collection('users').document(str(user_id)).update({
                'google_calendar_token': firestore.DELETE_FIELD
            })
            print(f"[Calendar] ✅ Credentials cleared for user {user_id}")
        except Exception as e:
            print(f"[Calendar] Error clearing credentials: {e}")
    
    @measure_time
    def add_event(
        self,
        user_tokens: Dict[str, str],
        event_data: Dict[str, Any],
        color_id: Optional[int] = None,
        calendar_id: str = "primary",
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Add a new event to user's Google Calendar.
        
        Args:
            user_tokens: User's OAuth tokens from Firestore
            event_data: Event data with summary, start_time, end_time, etc.
            color_id: Google Calendar color ID (1-11)
            calendar_id: Calendar ID (default: primary)
            user_id: User ID for auth cleanup on failure
            
        Returns:
            Dict with either:
            - {"success": True, "event": created_event} on success
            - {"success": False, "error": "auth_required"} if auth failed
            - {"success": False, "error": "message"} on other errors
        """
        # Get calendar service with auth check
        service, error = self._get_calendar_service(user_tokens, user_id)
        
        if service is None:
            print(f"[Calendar] Auth failed: {error}")
            if error == ERROR_AUTH_REQUIRED:
                return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login"}
            else:
                return {"status": "error", "type": ERROR_GENERIC, "message": "Failed to connect to calendar"}
        
        try:
            # Build event body
            event_body = {
                "summary": event_data.get("summary", "New Event"),
                "start": self._format_datetime(event_data.get("start_time"), event_data.get("is_all_day", False)),
                "end": self._format_datetime(event_data.get("end_time"), event_data.get("is_all_day", False))
            }
            
            # Optional fields
            if event_data.get("description"):
                event_body["description"] = event_data["description"]
            
            if event_data.get("location"):
                event_body["location"] = event_data["location"]
            
            # Color ID
            if color_id:
                event_body["colorId"] = str(color_id)
            elif event_data.get("category"):
                # Map category to color
                category = event_data["category"]
                event_body["colorId"] = str(CATEGORY_COLOR_MAP.get(category, DEFAULT_COLOR_ID))
            else:
                event_body["colorId"] = str(DEFAULT_COLOR_ID)
            
            # Attendees
            if event_data.get("resolved_attendees"):
                event_body["attendees"] = [
                    {"email": att["email"], "displayName": att.get("name", "")}
                    for att in event_data["resolved_attendees"]
                ]
            
            # Reminders
            event_body["reminders"] = {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 30},
                    {"method": "popup", "minutes": 10}
                ]
            }
            
            print(f"[Calendar] Creating event: {event_body.get('summary')}")
            
            # Insert event
            created_event = service.events().insert(
                calendarId=calendar_id,
                body=event_body,
                sendUpdates="all" if event_data.get("resolved_attendees") else "none"
            ).execute()
            
            print(f"[Calendar] ✅ Event created: {created_event.get('id')}")
            return {"status": "success", "event": created_event}
            
        except HttpError as e:
            print(f"[Calendar] HTTP Error: {e}")
            # Check if it's an auth error (401/403)
            if e.resp.status in [401, 403]:
                if user_id:
                    self._clear_user_credentials(user_id)
                return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login"}
            # Sanitize - don't expose raw error in message
            return {"status": "error", "type": ERROR_GENERIC, "message": "Calendar API error"}
        except Exception as e:
            print(f"[Calendar] Error creating event: {e}")
            import traceback
            traceback.print_exc()
            # Sanitize - don't expose raw error
            return {"status": "error", "type": ERROR_GENERIC, "message": "Unexpected error creating event"}
    
    def _format_datetime(self, dt_string: str, is_all_day: bool = False) -> Dict[str, str]:
        """
        Format datetime string for Google Calendar API.
        
        Args:
            dt_string: ISO 8601 datetime string
            is_all_day: Whether this is an all-day event
            
        Returns:
            Dict with date or dateTime and timeZone
        """
        try:
            dt = datetime.fromisoformat(dt_string.replace("Z", "+00:00"))
            
            if is_all_day:
                return {"date": dt.strftime("%Y-%m-%d")}
            else:
                return {
                    "dateTime": dt.isoformat(),
                    "timeZone": "Asia/Jerusalem"
                }
        except Exception as e:
            print(f"[Calendar] Error formatting datetime {dt_string}: {e}")
            # Fallback
            return {"dateTime": dt_string, "timeZone": "Asia/Jerusalem"}
    
    def get_upcoming_events(
        self,
        user_tokens: Dict[str, str],
        max_results: int = 10,
        calendar_id: str = "primary",
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get upcoming events from user's calendar.
        
        Args:
            user_tokens: User's OAuth tokens
            max_results: Maximum number of events to return
            calendar_id: Calendar ID (default: primary)
            user_id: User ID for auth cleanup on failure
            
        Returns:
            Dict with either:
            - {"success": True, "events": [...]} on success
            - {"success": False, "error": "auth_required"} if auth failed
        """
        service, error = self._get_calendar_service(user_tokens, user_id)
        
        if service is None:
            if error == ERROR_AUTH_REQUIRED:
                return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login", "events": []}
            return {"status": "error", "type": ERROR_GENERIC, "message": "Failed to connect", "events": []}
        
        try:
            now = datetime.utcnow().isoformat() + "Z"
            
            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=now,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime"
            ).execute()
            
            events = events_result.get("items", [])
            print(f"[Calendar] Found {len(events)} upcoming events")
            return {"status": "success", "events": events}
            
        except HttpError as e:
            print(f"[Calendar] HTTP Error: {e}")
            if e.resp.status in [401, 403]:
                if user_id:
                    self._clear_user_credentials(user_id)
                return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login", "events": []}
            return {"status": "error", "type": ERROR_GENERIC, "message": "Calendar API error", "events": []}
        except Exception as e:
            print(f"[Calendar] Error fetching events: {e}")
            return {"status": "error", "type": ERROR_GENERIC, "message": "Error fetching events", "events": []}
    
    def delete_event(
        self,
        user_tokens: Dict[str, str],
        event_id: str,
        calendar_id: str = "primary",
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Delete an event from user's calendar.
        
        Args:
            user_tokens: User's OAuth tokens
            event_id: Event ID to delete
            calendar_id: Calendar ID (default: primary)
            user_id: User ID for auth cleanup on failure
            
        Returns:
            Dict with success status and error if applicable
        """
        service, error = self._get_calendar_service(user_tokens, user_id)
        
        if service is None:
            if error == ERROR_AUTH_REQUIRED:
                return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login"}
            return {"status": "error", "type": ERROR_GENERIC, "message": "Failed to connect"}
        
        try:
            service.events().delete(
                calendarId=calendar_id,
                eventId=event_id
            ).execute()
            
            print(f"[Calendar] Deleted event: {event_id}")
            return {"status": "success"}
            
        except HttpError as e:
            print(f"[Calendar] HTTP Error deleting event: {e}")
            if e.resp.status in [401, 403]:
                if user_id:
                    self._clear_user_credentials(user_id)
                return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login"}
            return {"status": "error", "type": ERROR_GENERIC, "message": "Error deleting event"}
        except Exception as e:
            print(f"[Calendar] Error deleting event: {e}")
            return {"status": "error", "type": ERROR_GENERIC, "message": "Error deleting event"}
    
    def get_color_id_for_category(self, category: str, user_color_map: Optional[Dict] = None) -> int:
        """
        Get color ID for a category, considering user preferences.
        
        Args:
            category: Event category
            user_color_map: User's custom color preferences
            
        Returns:
            Google Calendar color ID (1-11)
        """
        # First check user's custom mapping
        if user_color_map and category in user_color_map:
            return user_color_map[category]
        
        # Fall back to default mapping
        return CATEGORY_COLOR_MAP.get(category, DEFAULT_COLOR_ID)


# Singleton instance
calendar_service = CalendarService()

# Export auth status constant
__all__ = ["calendar_service", "CalendarService", "AUTH_REQUIRED"]
