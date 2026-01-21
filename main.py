"""
Agentic Calendar 2.0 - Main Entry Point
Starts the Telegram bot and OAuth callback server.
"""

import asyncio
import logging
from dotenv import load_dotenv

# Load environment variables FIRST, before importing config
load_dotenv()

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import TELEGRAM_BOT_TOKEN
from bot import router, UserMiddleware
from server import create_oauth_server, set_bot_instance


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """
    Initialize and start the bot and OAuth server.
    """
    # Validate configuration
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set in environment variables!")
        return
    
    logger.info("Starting Agentic Calendar 2.0...")
    
    # Initialize bot with default properties
    bot = Bot(
        token=TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Set bot instance for OAuth callback server
    set_bot_instance(bot)
    
    # Create dispatcher
    dp = Dispatcher()
    
    # Register middleware (runs before every handler)
    dp.message.middleware(UserMiddleware())
    dp.callback_query.middleware(UserMiddleware())
    
    # Include router with handlers
    dp.include_router(router)
    
    # Start OAuth callback server
    oauth_runner = await create_oauth_server()
    
    # Log startup info
    bot_info = await bot.get_me()
    logger.info(f"Bot started: @{bot_info.username}")
    logger.info("Polling for updates... Press Ctrl+C to stop.")
    
    # Start polling
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        # Cleanup
        await oauth_runner.cleanup()
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())
