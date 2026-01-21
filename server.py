"""
OAuth2 callback server for Agentic Calendar 2.0
Handles Google OAuth callbacks and user creation.

This server runs alongside the Telegram bot to receive OAuth callbacks.
"""

import asyncio
from aiohttp import web
from aiogram import Bot

from config import (
    OAUTH_SERVER_HOST,
    OAUTH_SERVER_PORT,
    TELEGRAM_BOT_TOKEN
)
from services.auth_service import auth_service
from services.firestore_service import firestore_service
from models.user import create_default_user


# Global bot instance for sending messages
_bot: Bot = None


def set_bot_instance(bot: Bot) -> None:
    """Set the global bot instance for sending callback messages."""
    global _bot
    _bot = bot


async def oauth_callback(request: web.Request) -> web.Response:
    """
    Handle OAuth2 callback from Google.
    
    Flow:
    1. Validate callback parameters
    2. Exchange code for tokens
    3. Create/update user in Firestore
    4. Send Telegram confirmation
    5. Check for pending commands
    
    Args:
        request: aiohttp request with 'code' and 'state' query params
        
    Returns:
        HTML response page
    """
    # Extract query parameters
    code = request.query.get('code')
    state = request.query.get('state')  # This is the user_id
    error = request.query.get('error')
    
    # Handle user cancellation or error
    if error:
        print(f"[OAuth Callback] Error from Google: {error}")
        return web.Response(
            content_type='text/html',
            text=_generate_html_page(
                success=False,
                message="ההתחברות בוטלה או נכשלה. אפשר לנסות שוב עם /auth"
            )
        )
    
    # Validate required parameters
    if not code or not state:
        print(f"[OAuth Callback] Missing code or state parameter")
        return web.Response(
            content_type='text/html',
            text=_generate_html_page(
                success=False,
                message="חסרים פרמטרים בבקשה. נסה שוב עם /auth"
            )
        )
    
    try:
        user_id = int(state)
    except ValueError:
        print(f"[OAuth Callback] Invalid state (user_id): {state}")
        return web.Response(
            content_type='text/html',
            text=_generate_html_page(
                success=False,
                message="שגיאה בזיהוי המשתמש. נסה שוב עם /auth"
            )
        )
    
    try:
        # Exchange code for tokens
        access_token, refresh_token, token_expiry = auth_service.exchange_code(code)
        print(f"[OAuth Callback] Got tokens for user {user_id}")
        
        # Check if user exists (re-auth) or is new
        existing_user = firestore_service.get_user(user_id)
        
        if existing_user:
            # Re-authentication - just update tokens, DON'T reset onboarding
            print(f"[OAuth Callback] Re-auth for existing user {user_id}")
            firestore_service.update_tokens(
                user_id=user_id,
                access_token=access_token,
                refresh_token=refresh_token,
                token_expiry=token_expiry
            )
            telegram_message = (
                "✅ התחברת מחדש בהצלחה!\n\n"
                "אני מוכן לעזור לך עם היומן שלך 📅"
            )
        else:
            # New user - create document with tokens
            print(f"[OAuth Callback] Creating new user {user_id}")
            user_data = create_default_user(user_id)
            
            # Add tokens to calendar_config
            user_data["calendar_config"]["access_token"] = access_token
            user_data["calendar_config"]["refresh_token"] = refresh_token
            user_data["calendar_config"]["token_expiry"] = token_expiry
            
            # Save to Firestore
            firestore_service._user_ref(user_id).set(user_data)
            
            telegram_message = (
                "🎉 התחברת בהצלחה!\n\n"
                "עכשיו אוכל לגשת ליומן שלך.\n"
                "שלח /start כדי להמשיך."
            )
        
        # Send Telegram confirmation
        if _bot:
            try:
                await _bot.send_message(user_id, telegram_message)
                print(f"[OAuth Callback] Sent confirmation to user {user_id}")
            except Exception as e:
                print(f"[OAuth Callback] Failed to send Telegram message: {e}")
        
        # Check for pending command (for auth recovery flow)
        pending_cmd = firestore_service.get_pending_command(user_id)
        if pending_cmd:
            print(f"[OAuth Callback] User {user_id} has pending command: {pending_cmd}")
            # Clear the pending command
            firestore_service.clear_pending_command(user_id)
            
            # Notify user about the pending command
            if _bot:
                try:
                    await _bot.send_message(
                        user_id,
                        f"🔄 שחזרתי את הבקשה הקודמת שלך:\n\n"
                        f"«{pending_cmd}»\n\n"
                        "(בהמשך אטפל בה אוטומטית)"
                    )
                except Exception as e:
                    print(f"[OAuth Callback] Failed to send pending command message: {e}")
        
        return web.Response(
            content_type='text/html',
            text=_generate_html_page(
                success=True,
                message="ההתחברות הצליחה! אפשר לחזור לטלגרם 🎉"
            )
        )
        
    except Exception as e:
        print(f"[OAuth Callback] Error processing callback: {e}")
        import traceback
        traceback.print_exc()
        
        return web.Response(
            content_type='text/html',
            text=_generate_html_page(
                success=False,
                message=f"שגיאה בהתחברות. נסה שוב עם /auth"
            )
        )


def _generate_html_page(success: bool, message: str) -> str:
    """
    Generate a simple HTML response page.
    
    Args:
        success: Whether the operation succeeded
        message: Message to display (Hebrew)
        
    Returns:
        HTML string
    """
    color = "#4CAF50" if success else "#f44336"
    icon = "✅" if success else "❌"
    
    return f"""
    <!DOCTYPE html>
    <html dir="rtl" lang="he">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Agentic Calendar - התחברות</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                margin: 0;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            }}
            .card {{
                background: white;
                padding: 3rem;
                border-radius: 1rem;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                text-align: center;
                max-width: 400px;
            }}
            .icon {{
                font-size: 4rem;
                margin-bottom: 1rem;
            }}
            .message {{
                font-size: 1.25rem;
                color: #333;
                margin-bottom: 1.5rem;
            }}
            .status {{
                display: inline-block;
                padding: 0.5rem 1.5rem;
                border-radius: 2rem;
                background: {color};
                color: white;
                font-weight: bold;
            }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="icon">{icon}</div>
            <p class="message">{message}</p>
            <span class="status">{"הצלחה" if success else "שגיאה"}</span>
        </div>
    </body>
    </html>
    """


async def create_oauth_server() -> web.AppRunner:
    """
    Create and configure the OAuth callback server.
    
    Returns:
        Configured aiohttp AppRunner
    """
    app = web.Application()
    app.router.add_get('/oauth2callback', oauth_callback)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, OAUTH_SERVER_HOST, OAUTH_SERVER_PORT)
    await site.start()
    
    print(f"[OAuth Server] Running on http://{OAUTH_SERVER_HOST}:{OAUTH_SERVER_PORT}")
    print(f"[OAuth Server] Callback URL: http://{OAUTH_SERVER_HOST}:{OAUTH_SERVER_PORT}/oauth2callback")
    
    return runner
