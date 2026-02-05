"""
Performance Utilities for Agentic Calendar
Provides timing decorators for telemetry and debugging.
"""

import time
import functools
import logging
from typing import Callable, Any

# Get logger
logger = logging.getLogger(__name__)


def measure_time(func: Callable) -> Callable:
    """
    Decorator to measure and log function execution time.
    
    Logs the duration in milliseconds with the format:
    ⏱️ [Performance] {func_name} took {duration}ms
    
    Works with both sync and async functions.
    
    Usage:
        @measure_time
        def my_function():
            ...
            
        @measure_time
        async def my_async_function():
            ...
    """
    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs) -> Any:
        start_time = time.perf_counter()
        func_name = func.__qualname__
        
        print(f"⏱️ [Performance] {func_name} started...")
        
        try:
            result = await func(*args, **kwargs)
            return result
        finally:
            end_time = time.perf_counter()
            duration_ms = (end_time - start_time) * 1000
            print(f"⏱️ [Performance] {func_name} took {duration_ms:.2f}ms")
            logger.info(f"⏱️ [Performance] {func_name} took {duration_ms:.2f}ms")
    
    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs) -> Any:
        start_time = time.perf_counter()
        func_name = func.__qualname__
        
        print(f"⏱️ [Performance] {func_name} started...")
        
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            end_time = time.perf_counter()
            duration_ms = (end_time - start_time) * 1000
            print(f"⏱️ [Performance] {func_name} took {duration_ms:.2f}ms")
            logger.info(f"⏱️ [Performance] {func_name} took {duration_ms:.2f}ms")
    
    # Return appropriate wrapper based on function type
    if hasattr(func, '__code__') and func.__code__.co_flags & 0x80:
        # Async function (CO_COROUTINE flag)
        return async_wrapper
    
    # Check if it's an async function using inspect
    import asyncio
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    
    return sync_wrapper
