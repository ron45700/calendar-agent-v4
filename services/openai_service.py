"""
OpenAI service for Agentic Calendar 2.0
Handles Whisper transcription and Chat Completions.
"""

import os
import tempfile
from pathlib import Path
from typing import Optional, List, Dict

from openai import OpenAI

from config import OPENAI_API_KEY


class OpenAIService:
    """
    Service for OpenAI API operations.
    Handles audio transcription (Whisper) and chat completions.
    """
    
    # Default model for chat completions
    CHAT_MODEL = "gpt-4o-mini"
    
    def __init__(self):
        """Initialize OpenAI client."""
        self._client: Optional[OpenAI] = None
    
    @property
    def client(self) -> OpenAI:
        """Lazy initialization of OpenAI client."""
        if self._client is None:
            if not OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY not set in environment variables")
            self._client = OpenAI(api_key=OPENAI_API_KEY)
        return self._client
    
    # =========================================================================
    # Whisper Transcription
    # =========================================================================
    
    def transcribe_audio(self, file_path: str, language: str = "he") -> str:
        """
        Transcribe audio file using OpenAI Whisper API.
        
        Args:
            file_path: Path to the audio file (.ogg, .mp3, .wav, etc.)
            language: Language code for transcription (default: Hebrew)
            
        Returns:
            Transcribed text
            
        Raises:
            Exception: If transcription fails
        """
        print(f"[OpenAI] Transcribing audio file: {file_path}")
        
        with open(file_path, "rb") as audio_file:
            transcript = self.client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language=language
            )
        
        text = transcript.text.strip()
        print(f"[OpenAI] Transcription result: {text[:100]}...")
        
        return text
    
    async def transcribe_audio_async(self, file_path: str, language: str = "he") -> str:
        """
        Async wrapper for transcribe_audio.
        Uses sync API internally (OpenAI SDK handles it).
        
        Args:
            file_path: Path to the audio file
            language: Language code for transcription
            
        Returns:
            Transcribed text
        """
        # OpenAI Python SDK is sync, but we can still call it in async context
        # For true async, consider using httpx directly or run_in_executor
        return self.transcribe_audio(file_path, language)
    
    # =========================================================================
    # Chat Completions
    # =========================================================================
    
    def get_chat_response(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024
    ) -> str:
        """
        Get a chat response from GPT.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
                     Example: [{"role": "user", "content": "Hello"}]
            system_prompt: The system prompt to prepend
            model: Model to use (default: gpt-4o-mini)
            temperature: Creativity (0-1, default: 0.7)
            max_tokens: Max response length
            
        Returns:
            Assistant's response text
        """
        model = model or self.CHAT_MODEL
        
        # Build full message list with system prompt first
        full_messages = [
            {"role": "system", "content": system_prompt}
        ] + messages
        
        print(f"[OpenAI] Chat request with {len(messages)} user messages")
        
        response = self.client.chat.completions.create(
            model=model,
            messages=full_messages,
            temperature=temperature,
            max_tokens=max_tokens
        )
        
        assistant_message = response.choices[0].message.content.strip()
        print(f"[OpenAI] Chat response: {assistant_message[:100]}...")
        
        return assistant_message
    
    async def get_chat_response_async(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024
    ) -> str:
        """
        Async wrapper for get_chat_response.
        
        Args:
            messages: List of message dicts
            system_prompt: The system prompt
            model: Model to use
            temperature: Creativity level
            max_tokens: Max response length
            
        Returns:
            Assistant's response text
        """
        return self.get_chat_response(
            messages=messages,
            system_prompt=system_prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens
        )


# Singleton instance for easy import
openai_service = OpenAIService()
