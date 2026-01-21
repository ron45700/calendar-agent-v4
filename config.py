"""
Configuration module for Agentic Calendar 2.0
Loads environment variables and defines constants.
"""

import os
from dotenv import load_dotenv

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

# =============================================================================
# Server Configuration
# =============================================================================
OAUTH_SERVER_HOST = os.getenv("OAUTH_SERVER_HOST", "localhost")
OAUTH_SERVER_PORT = int(os.getenv("OAUTH_SERVER_PORT", "8080"))

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
