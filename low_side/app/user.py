"""
User web portal for Low-Side DMZ API.

Allows low-side users to:
- Login with username/password (accounts synced from corporate)
- Change password (required on first login)
- Send messages to corporate via the DMZ Gateway
"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Cookie, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .utils import setup_logging
from . import auth

logger = setup_logging("low_side_user")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/user", tags=["User Portal"])

# Gateway client (set by main.py)
gateway_client = None


def set_gateway_client(client) -> None:
    """Set the gateway client instance."""
    global gateway_client
    gateway_client = client


def _require_auth(session_token: Optional[str]) -> tuple:
    """Return (redirect, username). If authenticated: (None, username)."""
    username = auth.verify_user_session(session_token)
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
# Authentication
# =============================================================================

@router.get("/login", response_class=HTMLResponse, name="ls_user_login")
async def login_page(request: Request, error: str = "", message: str = ""):
    """Login page."""
    return templates.TemplateResponse("user/login.html", {
        "request": request,
        "title": "Login",
        "error": error,
        "message": message,
    })


@router.post("/login", name="ls_user_login_submit")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    """Handle login form submission."""
    username = username.strip()

    if auth.verify_user_credentials(username, password):
        session_token = auth.create_user_session(username)
        logger.info(f"User logged in: {username}")

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
            max_age=8 * 60 * 60
        )
        return response

    logger.warning(f"Failed login attempt: {username}")
    return RedirectResponse(
        url="/user/login?error=Invalid+username+or+password",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/logout", name="ls_user_logout")
async def logout(session_token: Optional[str] = Cookie(None)):
    """Logout and invalidate session."""
    if session_token:
        auth.invalidate_session(session_token)
    response = RedirectResponse(
        url="/user/login?message=You+have+been+logged+out",
        status_code=status.HTTP_303_SEE_OTHER
    )
    response.delete_cookie("session_token")
    return response


# =============================================================================
# Password Change
# =============================================================================

@router.get("/change-password", response_class=HTMLResponse, name="ls_user_change_password")
async def change_password_page(
    request: Request,
    required: str = "",
    error: str = "",
    message: str = "",
    session_token: Optional[str] = Cookie(None)
):
    """Password change page."""
    redirect, username = _require_auth(session_token)
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
    })


@router.post("/change-password", name="ls_user_change_password_submit")
async def change_password_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    session_token: Optional[str] = Cookie(None)
):
    """Handle password change form submission."""
    redirect, username = _require_auth(session_token)
    if redirect:
        return redirect

    if not auth.verify_user_credentials(username, current_password):
        return RedirectResponse(
            url="/user/change-password?error=Current+password+is+incorrect",
            status_code=status.HTTP_303_SEE_OTHER
        )

    if new_password != confirm_password:
        return RedirectResponse(
            url="/user/change-password?error=New+passwords+do+not+match",
            status_code=status.HTTP_303_SEE_OTHER
        )

    if current_password == new_password:
        return RedirectResponse(
            url="/user/change-password?error=New+password+must+be+different+from+current+password",
            status_code=status.HTTP_303_SEE_OTHER
        )

    success, msg = auth.update_user_password(username, new_password)

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
# User Pages (Protected)
# =============================================================================

@router.get("/", response_class=HTMLResponse, name="ls_user_home")
async def home(
    request: Request,
    message: str = "",
    session_token: Optional[str] = Cookie(None)
):
    """User home page."""
    redirect, username = _require_auth(session_token)
    if redirect:
        return redirect

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
    })


@router.get("/send", response_class=HTMLResponse, name="ls_user_send")
async def send_message_page(
    request: Request,
    message: str = "",
    error: str = "",
    session_token: Optional[str] = Cookie(None)
):
    """Message sending page."""
    redirect, username = _require_auth(session_token)
    if redirect:
        return redirect

    if auth.user_must_change_password(username):
        return RedirectResponse(
            url="/user/change-password?required=1",
            status_code=status.HTTP_303_SEE_OTHER
        )

    default_id = str(uuid.uuid4())
    default_timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    return templates.TemplateResponse("user/send_message.html", {
        "request": request,
        "title": "Send Message",
        "username": username,
        "default_id": default_id,
        "default_timestamp": default_timestamp,
        "message": message,
        "error": error,
    })


@router.post("/send", name="ls_user_send_submit")
async def send_message_submit(
    request: Request,
    message_id: str = Form(...),
    project: str = Form(...),
    test_id: str = Form(...),
    timestamp: str = Form(...),
    test_status: str = Form(...),
    data_json: str = Form("{}"),
    session_token: Optional[str] = Cookie(None)
):
    """Handle message form submission."""
    from .gateway_client import GatewayError, GatewayUnavailableError
    from .models import Message
    from pydantic import ValidationError

    redirect, username = _require_auth(session_token)
    if redirect:
        return redirect

    if auth.user_must_change_password(username):
        return RedirectResponse(
            url="/user/change-password?required=1",
            status_code=status.HTTP_303_SEE_OTHER
        )

    # Parse data JSON
    try:
        data_dict = json.loads(data_json) if data_json.strip() else {}
        if not isinstance(data_dict, dict):
            raise ValueError("Data must be a JSON object")
    except json.JSONDecodeError as e:
        return RedirectResponse(
            url=f"/user/send?error=Invalid+JSON+in+Data+field:+{str(e)[:50]}",
            status_code=status.HTTP_303_SEE_OTHER
        )
    except ValueError as e:
        return RedirectResponse(
            url=f"/user/send?error={str(e)}",
            status_code=status.HTTP_303_SEE_OTHER
        )

    message_data = {
        "ID": message_id.strip(),
        "Project": project.upper().strip(),
        "Test ID": test_id.strip(),
        "Timestamp": timestamp.strip(),
        "Test Status": test_status.strip(),
        "Data": data_dict
    }

    try:
        validated_message = Message.model_validate(message_data)
    except (ValidationError, Exception) as e:
        error_msg = str(e)[:100].replace(" ", "+")
        return RedirectResponse(
            url=f"/user/send?error=Validation+failed:+{error_msg}",
            status_code=status.HTTP_303_SEE_OTHER
        )

    if not gateway_client:
        return RedirectResponse(
            url="/user/send?error=Gateway+client+not+configured",
            status_code=status.HTTP_303_SEE_OTHER
        )

    try:
        await gateway_client.send_message(validated_message.model_dump(by_alias=True))
        logger.info(f"User {username} sent message: {validated_message.ID}")
        return RedirectResponse(
            url=f"/user/send?message=Message+sent+successfully!+ID:+{validated_message.ID}",
            status_code=status.HTTP_303_SEE_OTHER
        )
    except GatewayUnavailableError:
        return RedirectResponse(
            url="/user/send?error=Gateway+unavailable.+Please+try+again+later.",
            status_code=status.HTTP_303_SEE_OTHER
        )
    except GatewayError:
        return RedirectResponse(
            url="/user/send?error=Gateway+rejected+the+message.",
            status_code=status.HTTP_303_SEE_OTHER
        )
