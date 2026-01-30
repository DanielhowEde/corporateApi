"""
User web interface for Corporate DMZ API.

Provides a simple web UI for users to:
- Login with username/password
- Change password (required on first login)
- Send messages manually through the DMZ Gateway
- View message history (future)

Authentication:
- User accounts managed by admin via /admin/users
- Password change required on first login
"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Cookie, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import config
from .models import Message
from .whitelist import ProjectWhitelist
from .utils import setup_logging
from . import auth

logger = setup_logging("user_interface")

# Set up templates directory
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_branding() -> dict:
    """Get branding context for templates."""
    return {
        "company_name": config.company_name,
        "service_name": config.service_name,
        "network_label": config.network_label,
        "full_name": config.full_name,
    }

# Create router
router = APIRouter(prefix="/user", tags=["User Interface"])

# Instances (will be set by main.py)
whitelist: Optional[ProjectWhitelist] = None
gateway_client = None


def set_whitelist(wl: ProjectWhitelist) -> None:
    """Set the whitelist instance for user routes."""
    global whitelist
    whitelist = wl


def set_gateway_client(client) -> None:
    """Set the gateway client instance for user routes."""
    global gateway_client
    gateway_client = client


def get_current_user(session_token: Optional[str]) -> Optional[str]:
    """Get the username for a valid session token, or None if invalid."""
    return auth.verify_user_session(session_token)


def require_auth(session_token: Optional[str]) -> tuple[Optional[RedirectResponse], Optional[str]]:
    """
    Check if user is authenticated.
    Returns (redirect_response, username).
    If authenticated: (None, username)
    If not authenticated: (RedirectResponse, None)
    """
    username = get_current_user(session_token)
    if not username:
        return (
            RedirectResponse(
                url="/user/login?error=Please+login+to+continue",
                status_code=status.HTTP_303_SEE_OTHER
            ),
            None
        )
    return (None, username)


# =============================================================================
# Authentication Routes
# =============================================================================

@router.get("/login", response_class=HTMLResponse, name="user_login")
async def user_login_page(request: Request, error: str = "", message: str = ""):
    """Login page."""
    return templates.TemplateResponse("user/login.html", {
        "request": request,
        "title": "Login",
        "error": error,
        "message": message,
        **get_branding()
    })


@router.post("/login", name="user_login_submit")
async def user_login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    """Handle login form submission."""
    username = username.strip()

    if auth.verify_user_credentials(username, password):
        session_token = auth.create_user_session(username)
        logger.info(f"User logged in: {username}")

        # Check if password change is required
        if auth.user_must_change_password(username):
            response = RedirectResponse(
                url="/user/change-password?required=1",
                status_code=status.HTTP_303_SEE_OTHER
            )
        else:
            response = RedirectResponse(
                url="/user/",
                status_code=status.HTTP_303_SEE_OTHER
            )

        response.set_cookie(
            key="session_token",
            value=session_token,
            httponly=True,
            samesite="lax",
            max_age=8 * 60 * 60  # 8 hours
        )
        return response
    else:
        logger.warning(f"Failed login attempt for user: {username}")
        return RedirectResponse(
            url="/user/login?error=Invalid+username+or+password",
            status_code=status.HTTP_303_SEE_OTHER
        )


@router.get("/logout", name="user_logout")
async def user_logout(session_token: Optional[str] = Cookie(None)):
    """Logout and invalidate session."""
    if session_token:
        auth.invalidate_session(session_token)
        logger.info("User logged out")
    response = RedirectResponse(
        url="/user/login?message=You+have+been+logged+out",
        status_code=status.HTTP_303_SEE_OTHER
    )
    response.delete_cookie("session_token")
    return response


# =============================================================================
# Password Change
# =============================================================================

@router.get("/change-password", response_class=HTMLResponse, name="user_change_password")
async def user_change_password_page(
    request: Request,
    required: str = "",
    error: str = "",
    message: str = "",
    session_token: Optional[str] = Cookie(None)
):
    """Password change page."""
    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    is_required = required == "1" or auth.user_must_change_password(username)

    return templates.TemplateResponse("user/change_password.html", {
        "request": request,
        "title": "Change Password",
        "username": username,
        "is_required": is_required,
        "error": error,
        "message": message,
        **get_branding()
    })


@router.post("/change-password", name="user_change_password_submit")
async def user_change_password_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    session_token: Optional[str] = Cookie(None)
):
    """Handle password change form submission."""
    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    # Verify current password
    if not auth.verify_user_credentials(username, current_password):
        return RedirectResponse(
            url="/user/change-password?error=Current+password+is+incorrect",
            status_code=status.HTTP_303_SEE_OTHER
        )

    # Verify new password matches confirmation
    if new_password != confirm_password:
        return RedirectResponse(
            url="/user/change-password?error=New+passwords+do+not+match",
            status_code=status.HTTP_303_SEE_OTHER
        )

    # Verify new password is different
    if current_password == new_password:
        return RedirectResponse(
            url="/user/change-password?error=New+password+must+be+different+from+current+password",
            status_code=status.HTTP_303_SEE_OTHER
        )

    # Update password
    success, msg = auth.update_user_password(username, new_password, clear_must_change=True)

    if success:
        logger.info(f"User changed password: {username}")
        return RedirectResponse(
            url="/user/?message=Password+changed+successfully",
            status_code=status.HTTP_303_SEE_OTHER
        )

    return RedirectResponse(
        url=f"/user/change-password?error={msg.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER
    )


# =============================================================================
# User Web Pages (Protected)
# =============================================================================

@router.get("/", response_class=HTMLResponse, name="user_home")
async def user_home(
    request: Request,
    message: str = "",
    session_token: Optional[str] = Cookie(None)
):
    """User home page."""
    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    # Check if password change is required
    if auth.user_must_change_password(username):
        return RedirectResponse(
            url="/user/change-password?required=1",
            status_code=status.HTTP_303_SEE_OTHER
        )

    return templates.TemplateResponse("user/home.html", {
        "request": request,
        "title": "User Portal",
        "username": username,
        "message": message,
        **get_branding()
    })


@router.get("/send", response_class=HTMLResponse, name="user_send_message")
async def user_send_message_page(
    request: Request,
    message: str = "",
    error: str = "",
    session_token: Optional[str] = Cookie(None)
):
    """
    Message sending page - allows manual message composition and sending.
    """
    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    # Check if password change is required
    if auth.user_must_change_password(username):
        return RedirectResponse(
            url="/user/change-password?required=1",
            status_code=status.HTTP_303_SEE_OTHER
        )

    # Get list of enabled projects for dropdown
    projects = []
    if whitelist:
        projects = [(code, enabled) for code, enabled in whitelist.list_projects() if enabled]

    # Generate default values
    default_id = str(uuid.uuid4())
    now = datetime.now()
    default_date = now.strftime("%d%m%YT%H:%M:%S")

    return templates.TemplateResponse("user/send_message.html", {
        "request": request,
        "title": "Send Message",
        "username": username,
        "projects": projects,
        "default_id": default_id,
        "default_date": default_date,
        "message": message,
        "error": error,
        **get_branding()
    })


@router.post("/send", name="user_send_message_submit")
async def user_send_message_submit(
    request: Request,
    message_id: str = Form(...),
    project: str = Form(...),
    test_id: str = Form(...),
    area: str = Form(...),
    msg_status: str = Form(...),
    date: str = Form(...),
    data_json: str = Form("{}"),
    session_token: Optional[str] = Cookie(None)
):
    """Handle message form submission."""
    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    # Check if password change is required
    if auth.user_must_change_password(username):
        return RedirectResponse(
            url="/user/change-password?required=1",
            status_code=status.HTTP_303_SEE_OTHER
        )

    from .gateway_client import GatewayError, GatewayUnavailableError

    # Parse the data JSON
    try:
        data_dict = json.loads(data_json) if data_json.strip() else {}
        if not isinstance(data_dict, dict):
            raise ValueError("Data must be a JSON object")
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in Data field: {e}")
        return RedirectResponse(
            url=f"/user/send?error=Invalid+JSON+in+Data+field:+{str(e)[:50]}",
            status_code=status.HTTP_303_SEE_OTHER
        )
    except ValueError as e:
        return RedirectResponse(
            url=f"/user/send?error={str(e)}",
            status_code=status.HTTP_303_SEE_OTHER
        )

    # Build message
    message_data = {
        "ID": message_id.strip(),
        "Project": project.upper().strip(),
        "TestID": test_id.strip(),
        "Area": area.strip(),
        "Status": msg_status.strip(),
        "Date": date.strip(),
        "Data": data_dict
    }

    # Validate message schema
    try:
        validated_message = Message(**message_data)
    except Exception as e:
        logger.warning(f"Message validation failed: {e}")
        error_msg = str(e)[:100].replace(" ", "+")
        return RedirectResponse(
            url=f"/user/send?error=Validation+failed:+{error_msg}",
            status_code=status.HTTP_303_SEE_OTHER
        )

    # Check whitelist
    if whitelist and not whitelist.is_project_allowed(validated_message.Project):
        logger.warning(f"Project not whitelisted: {validated_message.Project}")
        return RedirectResponse(
            url=f"/user/send?error=Project+{validated_message.Project}+is+not+authorized",
            status_code=status.HTTP_303_SEE_OTHER
        )

    # Send to gateway
    if not gateway_client:
        return RedirectResponse(
            url=f"/user/send?error=Gateway+client+not+configured",
            status_code=status.HTTP_303_SEE_OTHER
        )

    try:
        await gateway_client.send_message(validated_message.model_dump())
        logger.info(f"User {username} sent message: {validated_message.ID}")
        return RedirectResponse(
            url=f"/user/send?message=Message+sent+successfully!+ID:+{validated_message.ID}",
            status_code=status.HTTP_303_SEE_OTHER
        )
    except GatewayUnavailableError as e:
        logger.error(f"Gateway unavailable: {e}")
        return RedirectResponse(
            url=f"/user/send?error=Gateway+unavailable.+Please+try+again+later.",
            status_code=status.HTTP_303_SEE_OTHER
        )
    except GatewayError as e:
        logger.error(f"Gateway error: {e}")
        return RedirectResponse(
            url=f"/user/send?error=Gateway+rejected+the+message.",
            status_code=status.HTTP_303_SEE_OTHER
        )


@router.get("/history", response_class=HTMLResponse, name="user_history")
async def user_history(request: Request, session_token: Optional[str] = Cookie(None)):
    """
    Message history page (placeholder for future).
    """
    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    # Check if password change is required
    if auth.user_must_change_password(username):
        return RedirectResponse(
            url="/user/change-password?required=1",
            status_code=status.HTTP_303_SEE_OTHER
        )

    return templates.TemplateResponse("user/history.html", {
        "request": request,
        "title": "Message History",
        "username": username,
        "note": "Message history tracking is planned for a future release.",
        **get_branding()
    })
