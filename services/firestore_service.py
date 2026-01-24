"""
Firestore service for Agentic Calendar 2.0
Handles all database operations for user data.
Supports file-based and environment variable credentials for Cloud Run.
"""

import os
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from google.cloud import firestore
from google.oauth2 import service_account

from models.user import UserData, create_default_user

logger = logging.getLogger(__name__)


class FirestoreService:
    """
    Service class for Firestore database operations.
    Manages user documents in the 'users' collection.
    """
    
    USERS_COLLECTION = "users"
    
    def __init__(self):
        """Initialize Firestore client with service account credentials."""
        self._db: Optional[firestore.Client] = None
    
    @property
    def db(self) -> firestore.Client:
        """
        Lazy initialization of Firestore client with 3-step fallback:
        1. Local file (service-account.json)
        2. Environment variable (GOOGLE_CREDENTIALS_JSON)
        3. Default credentials (Cloud Run automatic)
        """
        if self._db is None:
            credentials = None
            
            # Step 1: Try local service account file
            sa_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service-account.json")
            if os.path.exists(sa_file):
                try:
                    credentials = service_account.Credentials.from_service_account_file(sa_file)
                    logger.info(f"[Firestore] ✅ Loaded credentials from file: {sa_file}")
                except Exception as e:
                    logger.warning(f"[Firestore] Failed to load from file: {e}")
            
            # Step 2: Try GOOGLE_CREDENTIALS_JSON environment variable
            if credentials is None:
                creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
                if creds_json:
                    try:
                        info = json.loads(creds_json)
                        credentials = service_account.Credentials.from_service_account_info(info)
                        logger.info("[Firestore] ✅ Loaded credentials from GOOGLE_CREDENTIALS_JSON env var")
                    except Exception as e:
                        logger.warning(f"[Firestore] Failed to parse GOOGLE_CREDENTIALS_JSON: {e}")
            
            # Step 3: Create client with credentials or default
            if credentials:
                self._db = firestore.Client(credentials=credentials)
            else:
                logger.warning("[Firestore] ⚠️ Using default credentials (Cloud Run or gcloud auth)")
                self._db = firestore.Client()
        
        return self._db
    
    def _user_ref(self, user_id: int) -> firestore.DocumentReference:
        """Get document reference for a user."""
        return self.db.collection(self.USERS_COLLECTION).document(str(user_id))
    
    # =========================================================================
    # User CRUD Operations
    # =========================================================================
    
    def get_user(self, user_id: int) -> Optional[UserData]:
        """
        Fetch a user document from Firestore.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            UserData if found, None otherwise
        """
        doc = self._user_ref(user_id).get()
        if doc.exists:
            return doc.to_dict()
        return None
    
    def create_user(self, user_id: int) -> UserData:
        """
        Create a new user with default values.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            The created UserData
        """
        user_data = create_default_user(user_id)
        self._user_ref(user_id).set(user_data)
        print(f"[Firestore] Created new user: {user_id}")
        return user_data
    
    def get_or_create_user(self, user_id: int) -> UserData:
        """
        Get existing user or create new one if not exists.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            UserData (existing or newly created)
        """
        user = self.get_user(user_id)
        if user is None:
            user = self.create_user(user_id)
        return user
    
    def update_user(self, user_id: int, data: Dict[str, Any]) -> None:
        """
        Partially update a user document.
        
        Args:
            user_id: Telegram user ID
            data: Dictionary of fields to update
        """
        data["updated_at"] = datetime.utcnow()
        self._user_ref(user_id).update(data)
        print(f"[Firestore] Updated user {user_id}: {list(data.keys())}")
    
    def delete_user(self, user_id: int) -> None:
        """
        Delete a user document.
        
        Args:
            user_id: Telegram user ID
        """
        self._user_ref(user_id).delete()
        print(f"[Firestore] Deleted user: {user_id}")
    
    def update_last_seen(self, user_id: int) -> None:
        """
        Update user's last_seen timestamp to current time.
        
        Args:
            user_id: Telegram user ID
        """
        self._user_ref(user_id).update({
            "last_seen": datetime.utcnow()
        })
    
    # =========================================================================
    # Token Operations
    # =========================================================================
    
    def update_tokens(
        self, 
        user_id: int, 
        access_token: str, 
        refresh_token: Optional[str] = None,
        token_expiry: Optional[datetime] = None
    ) -> None:
        """
        Update user's OAuth tokens.
        
        Args:
            user_id: Telegram user ID
            access_token: New access token
            refresh_token: New refresh token (optional, keeps existing if None)
            token_expiry: Token expiration datetime
        """
        update_data = {
            "calendar_config.access_token": access_token,
            "calendar_config.token_expiry": token_expiry,
        }
        if refresh_token is not None:
            update_data["calendar_config.refresh_token"] = refresh_token
        
        self.update_user(user_id, update_data)
    
    def get_tokens(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Get user's OAuth tokens.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Dict with access_token, refresh_token, token_expiry or None
        """
        user = self.get_user(user_id)
        if user and "calendar_config" in user:
            config = user["calendar_config"]
            return {
                "access_token": config.get("access_token"),
                "refresh_token": config.get("refresh_token"),
                "token_expiry": config.get("token_expiry")
            }
        return None
    
    # =========================================================================
    # Pending Command Operations (for auth retry)
    # =========================================================================
    
    def set_pending_command(self, user_id: int, command: str) -> None:
        """
        Store a command to be retried after re-authentication.
        
        Args:
            user_id: Telegram user ID
            command: The original command text to retry
        """
        self.update_user(user_id, {
            "pending_command.command": command,
            "pending_command.timestamp": datetime.utcnow()
        })
        print(f"[Firestore] Set pending command for user {user_id}")
    
    def get_pending_command(self, user_id: int) -> Optional[str]:
        """
        Get stored pending command.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Command text if exists, None otherwise
        """
        user = self.get_user(user_id)
        if user and "pending_command" in user:
            return user["pending_command"].get("command")
        return None
    
    def clear_pending_command(self, user_id: int) -> None:
        """
        Clear pending command after successful execution.
        
        Args:
            user_id: Telegram user ID
        """
        self.update_user(user_id, {
            "pending_command.command": None,
            "pending_command.timestamp": None
        })
        print(f"[Firestore] Cleared pending command for user {user_id}")
    
    # =========================================================================
    # State Management
    # =========================================================================
    
    def set_state(self, user_id: int, state: Optional[str]) -> None:
        """
        Update user's FSM state.
        
        Args:
            user_id: Telegram user ID
            state: New state name or None to clear
        """
        self.update_user(user_id, {"current_state": state})
    
    def get_state(self, user_id: int) -> Optional[str]:
        """
        Get user's current FSM state.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            State name or None
        """
        user = self.get_user(user_id)
        if user:
            return user.get("current_state")
        return None
    
    # =========================================================================
    # Message History Operations (Conversation Memory)
    # =========================================================================
    
    def _messages_collection(self, user_id: int):
        """Get reference to user's messages sub-collection."""
        return self._user_ref(user_id).collection("messages")
    
    def save_message(
        self,
        user_id: int,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Save a message to the user's conversation history.
        
        Args:
            user_id: Telegram user ID
            role: Message role - "user" or "assistant"
            content: Message content text
            metadata: Optional additional metadata (e.g., voice=True)
            
        Returns:
            The document ID of the saved message
        """
        message_data = {
            "role": role,
            "content": content,
            "timestamp": firestore.SERVER_TIMESTAMP,
            "created_at": datetime.utcnow()
        }
        
        if metadata:
            message_data["metadata"] = metadata
        
        # Add to sub-collection
        doc_ref = self._messages_collection(user_id).add(message_data)
        message_id = doc_ref[1].id
        
        print(f"[Firestore] Saved {role} message for user {user_id}: {content[:50]}...")
        return message_id
    
    def get_recent_messages(
        self,
        user_id: int,
        limit: int = 10
    ) -> list:
        """
        Get recent messages from user's conversation history.
        
        Args:
            user_id: Telegram user ID
            limit: Maximum number of messages to retrieve
            
        Returns:
            List of message dicts: [{'role': 'user', 'content': '...'}, ...]
            Ordered by timestamp ascending (oldest first)
        """
        messages_ref = self._messages_collection(user_id)
        
        # Get messages ordered by timestamp descending (newest first), then reverse
        query = messages_ref.order_by(
            "created_at", direction=firestore.Query.DESCENDING
        ).limit(limit)
        
        docs = query.stream()
        
        messages = []
        for doc in docs:
            data = doc.to_dict()
            messages.append({
                "role": data.get("role", "user"),
                "content": data.get("content", "")
            })
        
        # Reverse to get chronological order (oldest first)
        messages.reverse()
        
        print(f"[Firestore] Retrieved {len(messages)} messages for user {user_id}")
        return messages
    
    def clear_message_history(self, user_id: int) -> int:
        """
        Clear all messages from user's history.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Number of messages deleted
        """
        messages_ref = self._messages_collection(user_id)
        docs = messages_ref.stream()
        
        count = 0
        for doc in docs:
            doc.reference.delete()
            count += 1
        
        print(f"[Firestore] Cleared {count} messages for user {user_id}")
        return count


# Singleton instance for easy import
firestore_service = FirestoreService()
