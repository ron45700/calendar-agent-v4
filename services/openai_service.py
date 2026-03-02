"""
OpenAI service for Agentic Calendar 2.0
Handles Whisper transcription and Chat Completions.
"""

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Optional, List, Dict

import httpx
import openai
from openai import OpenAI, AsyncOpenAI

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
        self._async_client: Optional[AsyncOpenAI] = None
    
    @property
    def client(self) -> OpenAI:
        """Lazy initialization of OpenAI client."""
        if self._client is None:
            if not OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY not set in environment variables")
            self._client = OpenAI(api_key=OPENAI_API_KEY)
        return self._client

    @property
    def async_client(self) -> AsyncOpenAI:
        """Lazy initialization of async OpenAI client.

        Fix #1 — Force IPv4: Cloud Run has inconsistent IPv6 connectivity.
        Default httpx resolves IPv6 first, causing 5-20s DNS hangs.
        local_address="0.0.0.0" forces the transport to bind to an IPv4
        address, bypassing AAAA lookups entirely.

        Fix #3 — max_retries=0: The SDK's built-in retry multiplies the
        25s timeout (2 retries = 75s worst-case before user sees error).
        Our llm_service.py already catches APITimeoutError and returns a
        friendly message, so SDK-level retries are redundant here.
        """
        if self._async_client is None:
            if not OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY not set in environment variables")
            # Force IPv4 transport to avoid Cloud Run IPv6 DNS hang
            transport = httpx.AsyncHTTPTransport(
                local_address="0.0.0.0",  # Bind to IPv4 interface only
                retries=0,                # No httpx-level retries (SDK controls retries)
            )
            http_client = httpx.AsyncClient(
                transport=transport,
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                    keepalive_expiry=30,
                ),
                # Short connect timeout: fail fast if IPv6 somehow still tries
                timeout=httpx.Timeout(25.0, connect=5.0),
            )
            self._async_client = AsyncOpenAI(
                api_key=OPENAI_API_KEY,
                http_client=http_client,
                timeout=25.0,   # SDK-native timeout — raises openai.APITimeoutError
                max_retries=0,  # Fix #3: Disable silent SDK retries (75s worst-case → 25s)
            )
        return self._async_client
    
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
        True async Whisper transcription.

        Fix #2 — Non-blocking: The sync OpenAI client blocks the thread it runs
        on. In an asyncio event loop (aiogram on Cloud Run with 1 CPU), calling
        it directly inside `async def` freezes ALL other requests for the full
        Whisper round-trip (~1-4s). run_in_executor offloads it to a thread pool
        worker, keeping the event loop free to handle other messages concurrently.

        Args:
            file_path: Path to the audio file
            language: Language code for transcription

        Returns:
            Transcribed text
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,  # Default ThreadPoolExecutor
            lambda: self.transcribe_audio(file_path, language)
        )
    
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
