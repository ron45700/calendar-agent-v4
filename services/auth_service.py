"""
Google OAuth2 service for Agentic Calendar 2.0
Handles authorization URL generation, token exchange, and refresh.
"""

from typing import Optional, Tuple, Dict, Any
from datetime import datetime, timedelta
import json

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request

from config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
    GOOGLE_SCOPES
)


class AuthService:
    """
    Service for Google OAuth2 authentication.
    Handles URL generation, code exchange, and token refresh.
    """
    
    def __init__(self):
        """Initialize the auth service with client credentials."""
        self.client_config = {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [GOOGLE_REDIRECT_URI]
            }
        }
    
    def generate_auth_url(self, user_id: int) -> str:
        """
        Generate Google OAuth2 authorization URL.
        
        Args:
            user_id: Telegram user ID (used as state parameter)
            
        Returns:
            Authorization URL for the user to visit
        """
        flow = Flow.from_client_config(
            self.client_config,
            scopes=GOOGLE_SCOPES,
            redirect_uri=GOOGLE_REDIRECT_URI
        )
        
        # Generate URL with offline access and consent prompt
        # access_type='offline' ensures we get a refresh token
        # prompt='consent' forces consent screen to get refresh token even on re-auth
        auth_url, _ = flow.authorization_url(
            access_type='offline',
            prompt='consent',
            state=str(user_id),  # Pass user_id as state for callback
            include_granted_scopes='true'
        )
        
        print(f"[AuthService] Generated auth URL for user {user_id}")
        return auth_url
    
    def exchange_code(self, code: str) -> Tuple[str, str, datetime]:
        """
        Exchange authorization code for access and refresh tokens.
        
        Args:
            code: Authorization code from OAuth callback
            
        Returns:
            Tuple of (access_token, refresh_token, expiry_datetime)
            
        Raises:
            Exception: If code exchange fails
        """
        flow = Flow.from_client_config(
            self.client_config,
            scopes=GOOGLE_SCOPES,
            redirect_uri=GOOGLE_REDIRECT_URI
        )
        
        # Exchange code for credentials
        flow.fetch_token(code=code)
        credentials = flow.credentials
        
        print(f"[AuthService] Exchanged code for tokens, expiry: {credentials.expiry}")
        
        return (
            credentials.token,
            credentials.refresh_token,
            credentials.expiry
        )
    
    def refresh_access_token(
        self, 
        access_token: str,
        refresh_token: str,
        token_expiry: Optional[datetime] = None
    ) -> Tuple[str, Optional[str], datetime]:
        """
        Refresh an expired access token using the refresh token.
        
        Args:
            access_token: Current (possibly expired) access token
            refresh_token: Refresh token for getting new access token
            token_expiry: Current token expiry (optional)
            
        Returns:
            Tuple of (new_access_token, new_refresh_token or None, new_expiry)
            
        Raises:
            Exception: If refresh fails (user may need to re-authenticate)
        """
        credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            expiry=token_expiry
        )
        
        # Attempt refresh
        request = Request()
        credentials.refresh(request)
        
        print(f"[AuthService] Refreshed token, new expiry: {credentials.expiry}")
        
        return (
            credentials.token,
            credentials.refresh_token,  # May be None (Google doesn't always return new one)
            credentials.expiry
        )
    
    def get_valid_credentials(
        self,
        access_token: str,
        refresh_token: str,
        token_expiry: Optional[datetime] = None
    ) -> Optional[Credentials]:
        """
        Get valid credentials, refreshing if necessary.
        
        Args:
            access_token: Current access token
            refresh_token: Refresh token
            token_expiry: Token expiry datetime
            
        Returns:
            Valid Credentials object, or None if refresh failed
        """
        credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            expiry=token_expiry
        )
        
        # Check if token is expired or about to expire (within 5 minutes)
        if credentials.expired or (
            credentials.expiry and 
            credentials.expiry < datetime.utcnow() + timedelta(minutes=5)
        ):
            try:
                request = Request()
                credentials.refresh(request)
                print("[AuthService] Credentials refreshed successfully")
            except Exception as e:
                print(f"[AuthService] Failed to refresh credentials: {e}")
                return None
        
        return credentials


# Singleton instance for easy import
auth_service = AuthService()
