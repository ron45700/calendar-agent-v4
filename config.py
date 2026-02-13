"""
Configuration module for Agentic Calendar 2.0
Loads environment variables and defines constants.
Supports both file-based and environment variable credentials for Cloud Run.
"""

import os
import json
from typing import Optional
from dotenv import load_dotenv
from google.oauth2 import service_account

# Load environment variables from .env file
load_dotenv()

# =============================================================================
# Telegram Configuration
# =============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# =============================================================================
# Google OAuth2 Configuration
# =============================================================================
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8080/oauth2callback")

# Google Calendar API scopes
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events"
]

# =============================================================================
# OpenAI Configuration
# =============================================================================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# =============================================================================
# Firestore Configuration
# =============================================================================
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

# =============================================================================
# Server Configuration
# =============================================================================
OAUTH_SERVER_HOST = os.getenv("OAUTH_SERVER_HOST", "localhost")
OAUTH_SERVER_PORT = int(os.getenv("OAUTH_SERVER_PORT", "8080"))

# Web app URL for auth links (falls back to BASE_WEBHOOK_URL if not set)
WEBAPP_URL = os.getenv("WEBAPP_URL", os.getenv("BASE_WEBHOOK_URL", ""))

# =============================================================================
# Default Values
# =============================================================================
DEFAULT_REMINDER_HOUR = 9  # 9 AM

# Default color map for calendar events
DEFAULT_COLOR_MAP = {
    "work": "1",       # Lavender
    "personal": "2",   # Sage
    "meeting": "3",    # Grape
    "important": "4",  # Flamingo
    "travel": "5",     # Banana
    "health": "6",     # Tangerine
}

# =============================================================================
# Admin Test Suite Configuration
# =============================================================================
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "cks")  # Default: "cks" or "bol"
ADMIN_TEST_ENABLED = os.getenv("ADMIN_TEST_ENABLED", "true").lower() == "true"


# =============================================================================
# Credential Loading Helper
# =============================================================================

def get_google_credentials() -> Optional[service_account.Credentials]:
    """
    Load Google service account credentials with fallback mechanism.
    
    Priority:
    1. File path from GOOGLE_APPLICATION_CREDENTIALS
    2. JSON string from GOOGLE_CREDENTIALS_JSON environment variable
    3. None (will use default credentials in Cloud Run)
    
    Returns:
        service_account.Credentials or None
    """
    # Try 1: Load from file path
    if GOOGLE_APPLICATION_CREDENTIALS:
        try:
            if os.path.exists(GOOGLE_APPLICATION_CREDENTIALS):
                credentials = service_account.Credentials.from_service_account_file(
                    GOOGLE_APPLICATION_CREDENTIALS
                )
                print("[Config] Loaded credentials from file")
                return credentials
        except Exception as e:
            print(f"[Config] Failed to load from file: {e}")
    
    # Try 2: Load from JSON environment variable
    if GOOGLE_CREDENTIALS_JSON:
        try:
            info = json.loads(GOOGLE_CREDENTIALS_JSON)
            credentials = service_account.Credentials.from_service_account_info(info)
            print("[Config] Loaded credentials from GOOGLE_CREDENTIALS_JSON env var")
            return credentials
        except Exception as e:
            print(f"[Config] Failed to load from JSON env var: {e}")
    
    # Fallback: Return None (will use default credentials in Cloud Run)
    print("[Config] No explicit credentials found, will use default credentials")
    return None
