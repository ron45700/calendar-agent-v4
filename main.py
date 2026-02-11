"""
Agentic Calendar 2.0 - Main Entry Point
Smart Hybrid Mode: Auto-switches between Webhooks (Cloud Run) and Polling (Local).

CRITICAL: This implementation properly handles the Webhook/Polling conflict by:
- Webhook Mode: Sets webhook on startup, includes OAuth callback route
- Polling Mode: ALWAYS deletes webhook before starting polling

Detection Logic: Checks BASE_WEBHOOK_URL environment variable.
"""

import asyncio
import os
import sys
import logging
from dotenv import load_dotenv
from aiohttp import web

# Load environment variables FIRST
load_dotenv()

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from config import TELEGRAM_BOT_TOKEN
from bot import router, UserMiddleware
from server import oauth_callback, set_bot_instance


# =============================================================================
# Logging Configuration (stdout for Cloud Run)
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


# =============================================================================
# Environment Configuration
# =============================================================================

BASE_WEBHOOK_URL = os.getenv("BASE_WEBHOOK_URL")  # e.g., "https://calendar-agent-xxx.run.app"
WEBHOOK_PATH = f"/webhook/{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else "/webhook"
PORT = int(os.getenv("PORT", "8080"))


# =============================================================================
# Health Check Handler
# =============================================================================

async def health_check(request: web.Request) -> web.Response:
    """Health check endpoint for Cloud Run - returns 200 OK."""
    return web.Response(text="OK", status=200)


async def daily_briefing_handler(request: web.Request) -> web.Response:
    """
    Endpoint for Cloud Scheduler to trigger daily morning briefing.
    POST /tasks/daily-briefing
    """
    from bot.jobs import send_daily_briefing_job
    
    bot = request.app.get("bot")
    if not bot:
        return web.json_response({"error": "Bot not initialized"}, status=500)
    
    logger.info("[Route] Daily briefing triggered")
    result = await send_daily_briefing_job(bot)
    return web.json_response(result, status=200)


# =============================================================================
# Webhook Mode (Cloud Run / Production)
# =============================================================================

async def on_startup_webhook(bot: Bot) -> None:
    """Called on startup in webhook mode - sets the webhook URL."""
    webhook_url = f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}"
    await bot.set_webhook(
        url=webhook_url,
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True
    )
    logger.info(f"✅ Webhook set: {webhook_url}")


async def on_shutdown_webhook(bot: Bot) -> None:
    """Called on shutdown in webhook mode - deletes webhook."""
    await bot.delete_webhook()
    logger.info("🔄 Webhook deleted on shutdown")


async def run_webhook_mode(bot: Bot, dp: Dispatcher) -> None:
    """
    Run in Webhook mode for Cloud Run.
    Sets up aiohttp server with:
    - Health check endpoints (/, /health)
    - Telegram webhook endpoint (/webhook/{token})
    - OAuth2 callback endpoint (/oauth2callback)
    """
    logger.info("=" * 50)
    logger.info("🌐 WEBHOOK MODE (Cloud Run)")
    logger.info("=" * 50)
    
    # Set bot instance for OAuth callback to send messages
    set_bot_instance(bot)
    
    # Register startup/shutdown handlers
    dp.startup.register(on_startup_webhook)
    dp.shutdown.register(on_shutdown_webhook)
    
    # Create aiohttp application
    app = web.Application()
    
    # Health check routes (Cloud Run requirement)
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    
    # OAuth2 callback route (Google OAuth redirect)
    app.router.add_get("/oauth2callback", oauth_callback)
    logger.info("📝 Registered /oauth2callback route")
    
    # Webhook handler for Telegram updates
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)
    
    # Setup application with aiogram integration
    setup_application(app, dp, bot=bot)
    
    # Store bot in app for route handlers
    app["bot"] = bot
    
    # Task routes (Cloud Scheduler triggers)
    app.router.add_post("/tasks/daily-briefing", daily_briefing_handler)
    logger.info("📋 Registered /tasks/daily-briefing route")
    
    # Start aiohttp server
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    
    logger.info(f"✅ Server listening on 0.0.0.0:{PORT}")
    logger.info(f"📡 Webhook path: {WEBHOOK_PATH}")
    logger.info(f"🔑 OAuth callback: /oauth2callback")
    logger.info("🤖 Bot running. Press Ctrl+C to stop.")
    
    # Keep server running forever
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("🛑 Shutting down...")
        await runner.cleanup()
        await bot.session.close()
        logger.info("👋 Goodbye!")


# =============================================================================
# Polling Mode (Local Development)
# =============================================================================

async def run_polling_mode(bot: Bot, dp: Dispatcher) -> None:
    """
    Run in Polling mode for local development.
    CRITICAL: Deletes webhook first to prevent conflict errors.
    Also starts a separate OAuth callback server.
    """
    from server import create_oauth_server
    
    logger.info("=" * 50)
    logger.info("💻 POLLING MODE (Local Development)")
    logger.info("=" * 50)
    
    # Set bot instance for OAuth callback
    set_bot_instance(bot)
    
    # CRITICAL: Delete any existing webhook to prevent conflict
    logger.info("🔄 Deleting any existing webhook...")
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("✅ Webhook cleared, safe to poll")
    
    # Start OAuth callback server on separate port
    oauth_runner = await create_oauth_server()
    logger.info("✅ OAuth server started")
    
    # Get bot info
    bot_info = await bot.get_me()
    logger.info(f"🤖 Bot: @{bot_info.username}")
    logger.info("📡 Polling for updates. Press Ctrl+C to stop.")
    
    # Start polling
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types()
        )
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("🛑 Shutting down...")
        await oauth_runner.cleanup()
        await bot.session.close()
        logger.info("👋 Goodbye!")


# =============================================================================
# Main Entry Point
# =============================================================================

async def main() -> None:
    """
    Initialize bot and run in appropriate mode.
    Auto-detects mode based on BASE_WEBHOOK_URL environment variable.
    """
    # Validate token
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN not set!")
        sys.exit(1)
    
    logger.info("🚀 Starting Agentic Calendar 2.0...")
    
    # Initialize bot
    bot = Bot(
        token=TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Create dispatcher
    dp = Dispatcher()
    
    # Register middleware
    dp.message.middleware(UserMiddleware())
    dp.callback_query.middleware(UserMiddleware())
    
    # Include handlers
    dp.include_router(router)
    
    # Auto-detect and run in appropriate mode
    if BASE_WEBHOOK_URL:
        logger.info(f"📍 BASE_WEBHOOK_URL detected: {BASE_WEBHOOK_URL}")
        await run_webhook_mode(bot, dp)
    else:
        logger.warning("⚠️ BASE_WEBHOOK_URL not set - running in local mode")
        await run_polling_mode(bot, dp)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⚡ Interrupted by user")
