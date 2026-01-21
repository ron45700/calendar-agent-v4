"""
Prompts module for Agentic Calendar 2.0
Contains all system prompts for the LLM agent.
"""

from .router_prompt import ROUTER_SYSTEM_PROMPT, INTENT_FUNCTION_SCHEMA

__all__ = ["ROUTER_SYSTEM_PROMPT", "INTENT_FUNCTION_SCHEMA"]
