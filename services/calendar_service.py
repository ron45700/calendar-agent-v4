"""
Google Calendar Service for Agentic Calendar 2.0
Handles Google Calendar API operations using user OAuth tokens.
"""

from datetime import datetime, timedelta, time
from typing import Optional, Dict, List, Any, Tuple
from zoneinfo import ZoneInfo

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
    "work": 6,          # Tangerine (Orange)
    "meeting": 6,       # Tangerine
    "personal": 6,      # Tangerine
    "family": 6,        # Tangerine
    "health": 6,        # Tangerine
    "sport": 6,         # Tangerine
    "study": 6,         # Tangerine
    "fun": 6,           # Tangerine
    "general": 6,       # Tangerine - default fallback
    "other": 6          # Tangerine
}

DEFAULT_COLOR_ID = 6  # Tangerine (Orange)

# Color ID to Emoji mapping for briefing display
COLOR_ID_EMOJI = {
    "1": "🪻",   # Lavender
    "2": "🟢",   # Sage
    "3": "🟣",   # Grape
    "4": "🩷",   # Flamingo
    "5": "🟡",   # Banana
    "6": "🟠",   # Tangerine
    "7": "🔵",   # Peacock
    "8": "⚫",   # Graphite
    "9": "🫐",   # Blueberry
    "10": "🌿",  # Basil
    "11": "🔴",  # Tomato
}
DEFAULT_EVENT_EMOJI = "📅"

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

# Error types for standardized returns
ERROR_AUTH_REQUIRED = "auth_required"
ERROR_GENERIC = "generic"

# Auth error detection patterns (for catching wrapped exceptions)
AUTH_ERROR_PATTERNS = [
    "invalid_grant",
    "Token has been expired",
    "Token has been revoked",
    "invalid_token",
    "access_denied",
    "unauthorized"
]


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
            error_str = str(e).lower()
            print(f"[Calendar] Error building service: {e}")
            
            # CRITICAL: Check if this is actually an auth error wrapped in generic Exception
            if self._is_auth_error(error_str):
                print(f"[Calendar] ⚠️ Detected auth error in exception: {e}")
                if user_id:
                    self._clear_user_credentials(user_id)
                return None, ERROR_AUTH_REQUIRED
            
            return None, ERROR_GENERIC
    
    def _is_auth_error(self, error_str: str) -> bool:
        """
        Check if an error string indicates an authentication/authorization failure.
        
        Args:
            error_str: Lowercase error message string
            
        Returns:
            True if this looks like an auth error
        """
        return any(pattern in error_str for pattern in AUTH_ERROR_PATTERNS)
    
    def _clear_user_credentials(self, user_id: str) -> None:
        """
        Delete invalid credentials from Firestore.
        
        Clears calendar_config token fields so /auth correctly detects
        that the user needs to re-authenticate.
        
        Args:
            user_id: The Telegram user ID
        """
        try:
            print(f"[Calendar] 🗑️ Purging invalid tokens for user {user_id}")
            self.firestore_client.collection('users').document(str(user_id)).update({
                'calendar_config.access_token': firestore.DELETE_FIELD,
                'calendar_config.refresh_token': firestore.DELETE_FIELD,
                'calendar_config.token_expiry': firestore.DELETE_FIELD,
            })
            print(f"[Calendar] ✅ Credentials cleared for user {user_id} - /auth will now work")
        except Exception as e:
            print(f"[Calendar] ❌ Error clearing credentials: {e}")
    
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
            
        except RefreshError as e:
            # Explicitly catch RefreshError during API call
            print(f"[Calendar] ⚠️ RefreshError during API call: {e}")
            if user_id:
                self._clear_user_credentials(user_id)
            return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login"}
            
        except Exception as e:
            error_str = str(e).lower()
            print(f"[Calendar] Error creating event: {e}")
            import traceback
            traceback.print_exc()
            
            # CRITICAL: Check if this is actually an auth error wrapped in generic Exception
            if self._is_auth_error(error_str):
                print(f"[Calendar] ⚠️ Detected auth error in exception: {e}")
                if user_id:
                    self._clear_user_credentials(user_id)
                return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login"}
            
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
            
        except RefreshError as e:
            print(f"[Calendar] ⚠️ RefreshError fetching events: {e}")
            if user_id:
                self._clear_user_credentials(user_id)
            return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login", "events": []}
            
        except Exception as e:
            error_str = str(e).lower()
            print(f"[Calendar] Error fetching events: {e}")
            
            # Check if this is an auth error wrapped in generic Exception
            if self._is_auth_error(error_str):
                print(f"[Calendar] ⚠️ Detected auth error in exception: {e}")
                if user_id:
                    self._clear_user_credentials(user_id)
                return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login", "events": []}
            
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
            
        except RefreshError as e:
            print(f"[Calendar] ⚠️ RefreshError deleting event: {e}")
            if user_id:
                self._clear_user_credentials(user_id)
            return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login"}
            
        except Exception as e:
            error_str = str(e).lower()
            print(f"[Calendar] Error deleting event: {e}")
            
            # Check if this is an auth error wrapped in generic Exception
            if self._is_auth_error(error_str):
                print(f"[Calendar] ⚠️ Detected auth error in exception: {e}")
                if user_id:
                    self._clear_user_credentials(user_id)
                return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login"}
            
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
    
    # =========================================================================
    # Search Events (for Update / Delete flows)
    # =========================================================================
    
    def search_events(
        self,
        user_tokens: Dict[str, str],
        query: str,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 10,
        calendar_id: str = "primary",
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Search user's calendar for events matching a query string or time range.
        
        Uses Google Calendar's native 'q' parameter for text search,
        then applies local fuzzy matching for better Hebrew support.
        
        Args:
            user_tokens: User's OAuth tokens
            query: Search text (event title / keyword)
            time_min: ISO 8601 lower bound (default: now)
            time_max: ISO 8601 upper bound (default: 30 days from now)
            max_results: Max events to return
            calendar_id: Calendar ID (default: primary)
            user_id: User ID for auth cleanup on failure
            
        Returns:
            {status: "success", events: [...]} or error dict
        """
        service, error = self._get_calendar_service(user_tokens, user_id)
        
        if service is None:
            if error == ERROR_AUTH_REQUIRED:
                return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login", "events": []}
            return {"status": "error", "type": ERROR_GENERIC, "message": "Failed to connect", "events": []}
        
        try:
            # Default time window: now → 30 days ahead
            if not time_min:
                now_israel = datetime.now(ISRAEL_TZ)
                # Search from start of today so we catch same-day events
                today_start = datetime.combine(now_israel.date(), time.min, tzinfo=ISRAEL_TZ)
                time_min = today_start.isoformat()
            if not time_max:
                now_israel = datetime.now(ISRAEL_TZ)
                future = now_israel + timedelta(days=30)
                time_max = future.isoformat()
            
            print(f"[Calendar] Searching events: q='{query}', {time_min} → {time_max}")
            
            # First: use Google's native text search (fast, server-side)
            events_result = service.events().list(
                calendarId=calendar_id,
                q=query,
                timeMin=time_min,
                timeMax=time_max,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime"
            ).execute()
            
            events = events_result.get("items", [])
            
            # If Google's q param returned nothing, fall back to local fuzzy match
            # (Google's q is weak with Hebrew partial matches)
            if not events and query:
                print(f"[Calendar] Google q search empty, trying local fuzzy match...")
                all_result = service.events().list(
                    calendarId=calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=50,
                    singleEvents=True,
                    orderBy="startTime"
                ).execute()
                
                all_events = all_result.get("items", [])
                query_lower = query.lower()
                events = [
                    e for e in all_events
                    if query_lower in e.get("summary", "").lower()
                ]
            
            print(f"[Calendar] Found {len(events)} matching events")
            return {"status": "success", "events": events}
            
        except HttpError as e:
            print(f"[Calendar] HTTP Error searching events: {e}")
            if e.resp.status in [401, 403]:
                if user_id:
                    self._clear_user_credentials(user_id)
                return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login", "events": []}
            return {"status": "error", "type": ERROR_GENERIC, "message": "Calendar API error", "events": []}
            
        except RefreshError as e:
            print(f"[Calendar] ⚠️ RefreshError searching events: {e}")
            if user_id:
                self._clear_user_credentials(user_id)
            return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login", "events": []}
            
        except Exception as e:
            error_str = str(e).lower()
            print(f"[Calendar] Error searching events: {e}")
            if self._is_auth_error(error_str):
                if user_id:
                    self._clear_user_credentials(user_id)
                return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login", "events": []}
            return {"status": "error", "type": ERROR_GENERIC, "message": "Error searching events", "events": []}
    
    # =========================================================================
    # Update Event
    # =========================================================================
    
    def update_event(
        self,
        user_tokens: Dict[str, str],
        event_id: str,
        updates: Dict[str, Any],
        calendar_id: str = "primary",
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update an existing event on user's Google Calendar.
        
        Uses PATCH semantics: only fields present in `updates` are changed.
        
        Supported update keys:
            - summary: New event title
            - start_time: New ISO 8601 start time
            - end_time: New ISO 8601 end time
            - location: New location
            - description: New description
            - color_id: New Google Calendar color ID (1-11)
            - attendees: List of {email, displayName} dicts (replaces all)
        
        Args:
            user_tokens: User's OAuth tokens
            event_id: Google Calendar event ID to update
            updates: Dict of fields to change
            calendar_id: Calendar ID (default: primary)
            user_id: User ID for auth cleanup on failure
            
        Returns:
            {status: "success", event: updated_event} or error dict
        """
        service, error = self._get_calendar_service(user_tokens, user_id)
        
        if service is None:
            if error == ERROR_AUTH_REQUIRED:
                return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login"}
            return {"status": "error", "type": ERROR_GENERIC, "message": "Failed to connect"}
        
        try:
            # Build patch body from updates dict
            patch_body = {}
            
            if "summary" in updates:
                patch_body["summary"] = updates["summary"]
            
            if "start_time" in updates:
                is_all_day = updates.get("is_all_day", False)
                patch_body["start"] = self._format_datetime(updates["start_time"], is_all_day)
            
            if "end_time" in updates:
                is_all_day = updates.get("is_all_day", False)
                patch_body["end"] = self._format_datetime(updates["end_time"], is_all_day)
            
            if "location" in updates:
                patch_body["location"] = updates["location"]
            
            if "description" in updates:
                patch_body["description"] = updates["description"]
            
            if "color_id" in updates:
                patch_body["colorId"] = str(updates["color_id"])
            
            if "attendees" in updates:
                patch_body["attendees"] = updates["attendees"]
            
            if not patch_body:
                return {"status": "error", "type": ERROR_GENERIC, "message": "No valid fields to update"}
            
            print(f"[Calendar] Updating event {event_id}: {list(patch_body.keys())}")
            
            # Use patch() for partial update (not put() which replaces everything)
            updated_event = service.events().patch(
                calendarId=calendar_id,
                eventId=event_id,
                body=patch_body,
                sendUpdates="all" if "attendees" in updates else "none"
            ).execute()
            
            print(f"[Calendar] ✅ Event updated: {updated_event.get('id')}")
            return {"status": "success", "event": updated_event}
            
        except HttpError as e:
            print(f"[Calendar] HTTP Error updating event: {e}")
            if e.resp.status in [401, 403]:
                if user_id:
                    self._clear_user_credentials(user_id)
                return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login"}
            if e.resp.status == 404:
                return {"status": "error", "type": ERROR_GENERIC, "message": "Event not found"}
            return {"status": "error", "type": ERROR_GENERIC, "message": "Calendar API error"}
            
        except RefreshError as e:
            print(f"[Calendar] ⚠️ RefreshError updating event: {e}")
            if user_id:
                self._clear_user_credentials(user_id)
            return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login"}
            
        except Exception as e:
            error_str = str(e).lower()
            print(f"[Calendar] Error updating event: {e}")
            if self._is_auth_error(error_str):
                if user_id:
                    self._clear_user_credentials(user_id)
                return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login"}
            return {"status": "error", "type": ERROR_GENERIC, "message": "Error updating event"}
    
    def get_today_events(
        self,
        user_tokens: Dict[str, str],
        calendar_id: str = "primary",
        user_id: Optional[str] = None,
        max_results: int = 20
    ) -> Dict[str, Any]:
        """
        Get events for today (00:00 to 23:59 Israel time).
        
        CRITICAL: Uses Asia/Jerusalem timezone, NOT UTC,
        so Cloud Run (UTC) returns correct results for Israeli users.
        
        Args:
            user_tokens: User's OAuth tokens
            calendar_id: Calendar ID (default: primary)
            user_id: User ID for auth cleanup on failure
            max_results: Max events to return
            
        Returns:
            Standard dict: {status, events} or {status, type, message}
        """
        service, error = self._get_calendar_service(user_tokens, user_id)
        
        if service is None:
            if error == ERROR_AUTH_REQUIRED:
                return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login", "events": []}
            return {"status": "error", "type": ERROR_GENERIC, "message": "Failed to connect", "events": []}
        
        try:
            # Calculate today's boundaries in Israel timezone
            now_israel = datetime.now(ISRAEL_TZ)
            today_start = datetime.combine(now_israel.date(), time.min, tzinfo=ISRAEL_TZ)
            today_end = datetime.combine(now_israel.date(), time(23, 59, 59), tzinfo=ISRAEL_TZ)
            
            time_min = today_start.isoformat()
            time_max = today_end.isoformat()
            
            print(f"[Calendar] Fetching today's events: {time_min} → {time_max}")
            
            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime"
            ).execute()
            
            events = events_result.get("items", [])
            print(f"[Calendar] Found {len(events)} events for today")
            return {"status": "success", "events": events}
            
        except HttpError as e:
            print(f"[Calendar] HTTP Error: {e}")
            if e.resp.status in [401, 403]:
                if user_id:
                    self._clear_user_credentials(user_id)
                return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login", "events": []}
            return {"status": "error", "type": ERROR_GENERIC, "message": "Calendar API error", "events": []}
            
        except RefreshError as e:
            print(f"[Calendar] ⚠️ RefreshError: {e}")
            if user_id:
                self._clear_user_credentials(user_id)
            return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login", "events": []}
            
        except Exception as e:
            error_str = str(e).lower()
            print(f"[Calendar] Error fetching today's events: {e}")
            if self._is_auth_error(error_str):
                print(f"[Calendar] ⚠️ Detected auth error: {e}")
                if user_id:
                    self._clear_user_credentials(user_id)
                return {"status": "error", "type": ERROR_AUTH_REQUIRED, "message": "User needs to re-login", "events": []}
            return {"status": "error", "type": ERROR_GENERIC, "message": "Error fetching events", "events": []}
    
    def format_today_events(self, events: List[Dict[str, Any]]) -> Optional[str]:
        """
        Format raw Google Calendar events into a pretty briefing string.
        
        Format per event:
            {emoji} *{summary}* | {start} - {end}
        
        Args:
            events: List of Google Calendar event dicts
            
        Returns:
            Formatted string, or None if no events
        """
        if not events:
            return None
        
        lines = []
        for event in events:
            summary = event.get("summary", "אירוע ללא שם")
            color_id = event.get("colorId", "")
            emoji = COLOR_ID_EMOJI.get(str(color_id), DEFAULT_EVENT_EMOJI)
            
            # Parse start/end times
            start_raw = event.get("start", {})
            end_raw = event.get("end", {})
            
            if "dateTime" in start_raw:
                # Timed event
                start_dt = datetime.fromisoformat(start_raw["dateTime"])
                end_dt = datetime.fromisoformat(end_raw.get("dateTime", start_raw["dateTime"]))
                start_str = start_dt.strftime("%H:%M")
                end_str = end_dt.strftime("%H:%M")
                lines.append(f"{emoji} *{summary}* | {start_str} - {end_str}")
            elif "date" in start_raw:
                # All-day event
                lines.append(f"{emoji} *{summary}* | כל היום")
        
        if not lines:
            return None
        
        return "\n\n".join(lines)


# Singleton instance
calendar_service = CalendarService()

# Export auth status constant
__all__ = ["calendar_service", "CalendarService", "AUTH_REQUIRED"]
