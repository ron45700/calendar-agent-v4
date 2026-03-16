"""
Agentic Calendar 2.0 - Main Entry Point
Smart Hybrid Mode: Auto-switches between Webhooks (Cloud Run) and Polling (Local).

CRITICAL: This implementation properly handles the Webhook/Polling conflict by:
- Webhook Mode: Binds port 8080 FIRST, then sets webhook in background
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

async def run_webhook_mode(bot: Bot, dp: Dispatcher) -> None:
    """
    Run in Webhook mode for Cloud Run.

    Cloud Run requires the container to bind PORT within the startup timeout.
    Strategy:
      1. Build the aiohttp app (no blocking calls).
      2. Bind TCP port 8080 via site.start() FIRST.
      3. THEN fire bot.set_webhook() in a background task.
    This guarantees port 8080 is always listening before set_webhook() is attempted.
    """
    logger.info("=" * 50)
    logger.info("🌐 WEBHOOK MODE (Cloud Run)")
    logger.info("=" * 50)
    
    # Set bot instance for OAuth callback
    set_bot_instance(bot)
    
    # Create aiohttp application
    app = web.Application()
    
    # Health check routes (Cloud Run REQUIRES these to be reachable immediately)
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    
    # OAuth2 callback route
    app.router.add_get("/oauth2callback", oauth_callback)
    
    # Telegram webhook handler
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)
    
    # Wire aiogram dispatcher into aiohttp lifecycle (message handling only)
    # NOTE: we do NOT register dp.startup hooks here to avoid any blocking
    # before site.start() is called.
    setup_application(app, dp, bot=bot)
    
    # Store bot reference in app
    app["bot"] = bot
    
    # Cloud Scheduler trigger route
    app.router.add_post("/tasks/daily-briefing", daily_briefing_handler)
    
    # =========================================================================
    # STEP 1: Bind port 8080 FIRST.
    # Cloud Run health check hits GET / immediately after startup.
    # This MUST succeed before we do any external network calls.
    # =========================================================================
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    
    logger.info(f"✅ Port {PORT} bound on 0.0.0.0 — Cloud Run health check will pass")
    logger.info(f"📡 Webhook path: {WEBHOOK_PATH}")
    logger.info(f"🔑 OAuth callback: /oauth2callback")
    logger.info("🤖 Bot is running!")
    
    # =========================================================================
    # STEP 2: Register Telegram webhook AFTER port is already bound.
    # Any delay in set_webhook() (network, Telegram rate-limiting, etc.)
    # no longer threatens the Cloud Run startup timeout.
    # =========================================================================
    webhook_url = f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}"
    
    async def _register_webhook():
        try:
            await asyncio.sleep(2)  # Small delay to let the event loop stabilise
            logger.info(f"⏳ Registering Telegram webhook: {webhook_url}")
            await bot.set_webhook(
                url=webhook_url,
                allowed_updates=["message", "callback_query"],
                drop_pending_updates=True
            )
            logger.info("✅ Telegram webhook registered successfully!")
        except Exception as e:
            logger.error(f"❌ set_webhook failed: {e}")
    
    asyncio.ensure_future(_register_webhook())
    
    # Keep server alive
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("🛑 Shutting down...")
        try:
            await bot.delete_webhook()
        except Exception:
            pass
        await runner.cleanup()
        try:
            await bot.session.close()
        except Exception:
            pass
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
    
    # Force IPv4 resolution for Cloud Run (prevents IPv6 DNS drops/hangs).
    # We subclass AiohttpSession to avoid version-specific constructor differences.
    import socket
    import aiohttp
    from aiogram.client.session.aiohttp import AiohttpSession

    class IPv4AiohttpSession(AiohttpSession):
        async def create_session(self):
            """Override to force IPv4-only TCP connector."""
            connector = aiohttp.TCPConnector(family=socket.AF_INET)
            return aiohttp.ClientSession(connector=connector)

    session = IPv4AiohttpSession()

    # Initialize bot
    bot = Bot(
        token=TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session
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
