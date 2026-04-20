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

from fastapi import APIRouter, Cookie, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import audit, auth
from .config import config
from .file_store import FileStore
from .models import Message
from .utils import setup_logging
from .whitelist import ProjectWhitelist

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
whitelist: ProjectWhitelist | None = None
gateway_client = None
file_store: FileStore | None = None


def set_whitelist(wl: ProjectWhitelist) -> None:
    """Set the whitelist instance for user routes."""
    global whitelist
    whitelist = wl


def set_gateway_client(client) -> None:
    """Set the gateway client instance for user routes."""
    global gateway_client
    gateway_client = client


def set_file_store(fs: FileStore) -> None:
    """Set the file store instance for user routes."""
    global file_store
    file_store = fs


def get_current_user(session_token: str | None) -> str | None:
    """Get the username for a valid session token, or None if invalid."""
    return auth.verify_user_session(session_token)


def require_auth(
    session_token: str | None,
) -> tuple[RedirectResponse | None, str | None]:
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
                status_code=status.HTTP_303_SEE_OTHER,
            ),
            None,
        )
    return (None, username)


# =============================================================================
# Authentication Routes
# =============================================================================


@router.get("/login", response_class=HTMLResponse, name="user_login")
async def user_login_page(request: Request, error: str = "", message: str = ""):
    """Login page."""
    return templates.TemplateResponse(
        request,
        "user/login.html",
        {
            "title": "Login",
            "error": error,
            "message": message,
            **get_branding(),
        },
    )


@router.post("/login", name="user_login_submit")
async def user_login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    """Handle login form submission."""
    username = username.strip()

    if auth.verify_user_credentials(username, password):
        session_token = auth.create_user_session(username)
        logger.info(f"User logged in: {username}")

        # Check if password change is required
        if auth.user_must_change_password(username):
            response = RedirectResponse(
                url="/user/change-password?required=1",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        else:
            response = RedirectResponse(url="/user/", status_code=status.HTTP_303_SEE_OTHER)

        response.set_cookie(
            key="session_token",
            value=session_token,
            httponly=True,
            samesite="lax",
            max_age=8 * 60 * 60,  # 8 hours
        )
        return response

    reason = auth.classify_login_failure(username, role="user")
    audit.record_failed_login(username=username, source="corporate-user", reason=reason)
    return RedirectResponse(
        url="/user/login?error=Invalid+username+or+password",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/logout", name="user_logout")
async def user_logout(session_token: str | None = Cookie(None)):
    """Logout and invalidate session."""
    if session_token:
        auth.invalidate_session(session_token)
        logger.info("User logged out")
    response = RedirectResponse(
        url="/user/login?message=You+have+been+logged+out",
        status_code=status.HTTP_303_SEE_OTHER,
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
    session_token: str | None = Cookie(None),
):
    """Password change page."""
    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    is_required = required == "1" or auth.user_must_change_password(username)

    return templates.TemplateResponse(
        request,
        "user/change_password.html",
        {
            "title": "Change Password",
            "username": username,
            "is_required": is_required,
            "error": error,
            "message": message,
            **get_branding(),
        },
    )


@router.post("/change-password", name="user_change_password_submit")
async def user_change_password_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    session_token: str | None = Cookie(None),
):
    """Handle password change form submission."""
    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    # Verify current password
    if not auth.verify_user_credentials(username, current_password):
        return RedirectResponse(
            url="/user/change-password?error=Current+password+is+incorrect",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Verify new password matches confirmation
    if new_password != confirm_password:
        return RedirectResponse(
            url="/user/change-password?error=New+passwords+do+not+match",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Verify new password is different
    if current_password == new_password:
        return RedirectResponse(
            url="/user/change-password?error=New+password+must+be+different+from+current+password",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Update password
    success, msg = auth.update_user_password(username, new_password, clear_must_change=True)

    if success:
        logger.info(f"User changed password: {username}")
        return RedirectResponse(
            url="/user/?message=Password+changed+successfully",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=f"/user/change-password?error={msg.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# =============================================================================
# User Web Pages (Protected)
# =============================================================================


@router.get("/", response_class=HTMLResponse, name="user_home")
async def user_home(request: Request, message: str = "", session_token: str | None = Cookie(None)):
    """User home page."""
    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    # Check if password change is required
    if auth.user_must_change_password(username):
        return RedirectResponse(
            url="/user/change-password?required=1",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return templates.TemplateResponse(
        request,
        "user/home.html",
        {
            "title": "User Portal",
            "username": username,
            "message": message,
            **get_branding(),
        },
    )


@router.get("/send", response_class=HTMLResponse, name="user_send_message")
async def user_send_message_page(
    request: Request,
    message: str = "",
    error: str = "",
    session_token: str | None = Cookie(None),
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
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Dropdown shows the intersection of:
    #   - whitelist entries that are enabled
    #   - the user's personal allowed_projects list (explicit grant required)
    user_allowed = set(auth.get_user_allowed_projects(username))
    projects = []
    if whitelist:
        projects = [
            (code, enabled)
            for code, enabled in whitelist.list_projects()
            if enabled and code in user_allowed
        ]

    # Generate default values
    default_id = str(uuid.uuid4())
    now = datetime.now()
    default_timestamp = now.strftime("%Y-%m-%dT%H:%M:%S")

    return templates.TemplateResponse(
        request,
        "user/send_message.html",
        {
            "title": "Send Message",
            "username": username,
            "projects": projects,
            "default_id": default_id,
            "default_timestamp": default_timestamp,
            "message": message,
            "error": error,
            **get_branding(),
        },
    )


@router.post("/send", name="user_send_message_submit")
async def user_send_message_submit(
    request: Request,
    message_id: str = Form(...),
    project: str = Form(...),
    test_id: str = Form(...),
    area: str = Form(...),
    timestamp: str = Form(...),
    test_status: str = Form(...),
    data_json: str = Form("{}"),
    auto_send: str | None = Form(None),
    session_token: str | None = Cookie(None),
):
    """Handle message form submission with cert wrapping and auto-send support."""
    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    if auth.user_must_change_password(username):
        return RedirectResponse(
            url="/user/change-password?required=1",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    from .cert_client import CertGatewayError, CertGatewayUnavailableError
    from .gateway_client import GatewayError, GatewayUnavailableError

    send_log = []
    should_auto_send = auto_send == "on"

    def log_entry(level: str, message: str):
        send_log.append(
            {
                "time": datetime.now().strftime("%H:%M:%S"),
                "level": level,
                "message": message,
            }
        )

    # Parse the data JSON
    try:
        data_dict = json.loads(data_json) if data_json.strip() else {}
        if not isinstance(data_dict, dict):
            raise ValueError("Data must be a JSON object")
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in Data field: {e}")
        return RedirectResponse(
            url=f"/user/send?error=Invalid+JSON+in+Data+field:+{str(e)[:50]}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except ValueError as e:
        return RedirectResponse(
            url=f"/user/send?error={str(e)}", status_code=status.HTTP_303_SEE_OTHER
        )

    # Build message using alias keys (matching low-side schema)
    message_data = {
        "ID": message_id.strip(),
        "Project": project.upper().strip(),
        "TestID": test_id.strip(),
        "Area": area.strip(),
        "Date": timestamp.strip(),
        "Status": test_status.strip(),
        "Data": data_dict,
    }

    # Validate message schema
    try:
        validated_message = Message.model_validate(message_data)
        log_entry("info", f"Message validated: {validated_message.ID}")
    except Exception as e:
        logger.warning(f"Message validation failed: {e}")
        error_msg = str(e)[:100].replace(" ", "+")
        return RedirectResponse(
            url=f"/user/send?error=Validation+failed:+{error_msg}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Check the global whitelist
    if whitelist and not whitelist.is_project_allowed(validated_message.Project):
        logger.warning(f"Project not whitelisted: {validated_message.Project}")
        return RedirectResponse(
            url=f"/user/send?error=Project+{validated_message.Project}+is+not+authorized",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Check per-user project access (admin must grant each project explicitly)
    if not auth.user_can_send_to_project(username, validated_message.Project):
        logger.warning(f"User {username} not authorised for project {validated_message.Project}")
        return RedirectResponse(
            url=f"/user/send?error=You+are+not+authorised+to+send+to+{validated_message.Project}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    log_entry("info", f"Project {validated_message.Project} authorized")

    if not gateway_client:
        return RedirectResponse(
            url="/user/send?error=Gateway+client+not+configured",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Send to gateway (cert wrapping + auto_send handled inside gateway_client)
    try:
        log_entry("info", "Requesting certificate wrapping from cert gateway...")
        result = await gateway_client.send_message(
            validated_message.model_dump(by_alias=True), auto_send=should_auto_send
        )

        if should_auto_send:
            log_entry(
                "success",
                f"Message sent successfully to gateway! ID: {validated_message.ID}",
            )
            logger.info(f"User {username} sent message: {validated_message.ID}")
        else:
            # Save to pending queue
            if file_store and "wrapped" in result:
                file_store.write_pending(
                    validated_message.model_dump(by_alias=True), result["wrapped"]
                )
                log_entry("info", "Message saved to pending queue")
            log_entry(
                "success",
                f"Message cert-wrapped and queued (auto-send disabled). ID: {validated_message.ID}",
            )
            logger.info(f"User {username} cert-wrapped message (queued): {validated_message.ID}")

        # Render the page with send log instead of redirect
        projects = []
        if whitelist:
            projects = [(code, enabled) for code, enabled in whitelist.list_projects() if enabled]

        return templates.TemplateResponse(
            request,
            "user/send_message.html",
            {
                "title": "Send Message",
                "username": username,
                "projects": projects,
                "default_id": str(uuid.uuid4()),
                "default_timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "message": f"Message {'sent' if should_auto_send else 'wrapped'} successfully! ID: {validated_message.ID}",
                "error": "",
                "send_log": send_log,
                **get_branding(),
            },
        )

    except (CertGatewayUnavailableError, CertGatewayError) as e:
        log_entry("error", f"Certificate gateway error: {e}")
        logger.error(f"Cert gateway error: {e}")

        projects = []
        if whitelist:
            projects = [(code, enabled) for code, enabled in whitelist.list_projects() if enabled]

        return templates.TemplateResponse(
            request,
            "user/send_message.html",
            {
                "title": "Send Message",
                "username": username,
                "projects": projects,
                "default_id": message_id,
                "default_timestamp": timestamp,
                "message": "",
                "error": "Certificate gateway unavailable. Message not sent.",
                "send_log": send_log,
                **get_branding(),
            },
        )

    except GatewayUnavailableError as e:
        log_entry("error", f"DMZ Gateway unavailable: {e}")
        logger.error(f"Gateway unavailable: {e}")

        projects = []
        if whitelist:
            projects = [(code, enabled) for code, enabled in whitelist.list_projects() if enabled]

        return templates.TemplateResponse(
            request,
            "user/send_message.html",
            {
                "title": "Send Message",
                "username": username,
                "projects": projects,
                "default_id": message_id,
                "default_timestamp": timestamp,
                "message": "",
                "error": "Gateway unavailable. Please try again later.",
                "send_log": send_log,
                **get_branding(),
            },
        )

    except GatewayError as e:
        log_entry("error", f"Gateway rejected the message: {e}")
        logger.error(f"Gateway error: {e}")

        projects = []
        if whitelist:
            projects = [(code, enabled) for code, enabled in whitelist.list_projects() if enabled]

        return templates.TemplateResponse(
            request,
            "user/send_message.html",
            {
                "title": "Send Message",
                "username": username,
                "projects": projects,
                "default_id": message_id,
                "default_timestamp": timestamp,
                "message": "",
                "error": "Gateway rejected the message.",
                "send_log": send_log,
                **get_branding(),
            },
        )


@router.get("/history", response_class=HTMLResponse, name="user_history")
async def user_history(
    request: Request,
    project: str = "",
    search: str = "",
    page: int = 1,
    message: str = "",
    error: str = "",
    session_token: str | None = Cookie(None),
):
    """Message history page with filtering, search, and pagination (20 per page)."""
    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    if auth.user_must_change_password(username):
        return RedirectResponse(
            url="/user/change-password?required=1",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    PER_PAGE = 20
    all_messages = []
    projects_list = []
    if file_store:
        all_messages = file_store.get_all_messages(
            project_filter=project.upper().strip() if project else "",
            search_query=search.strip() if search else "",
            limit=10000,
        )
        projects_list = file_store.list_projects()

    total = len(all_messages)
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * PER_PAGE
    messages = all_messages[start : start + PER_PAGE]

    # Also get enabled projects from whitelist for the filter dropdown
    whitelist_projects = []
    if whitelist:
        whitelist_projects = [code for code, enabled in whitelist.list_projects()]

    # Merge both lists (projects with messages + whitelisted)
    all_projects = sorted(set(projects_list + whitelist_projects))

    return templates.TemplateResponse(
        request,
        "user/history.html",
        {
            "title": "Message History",
            "username": username,
            "messages": messages,
            "projects": all_projects,
            "current_project": project,
            "current_search": search,
            "message_count": total,
            "page": page,
            "total_pages": total_pages,
            "per_page": PER_PAGE,
            "page_start": start + 1 if messages else 0,
            "page_end": start + len(messages),
            "retention_days": config.history_retention_days,
            "message": message,
            "error": error,
            **get_branding(),
        },
    )


@router.post("/history/clear", name="user_history_clear")
async def user_history_clear(
    request: Request,
    older_than_days: int = Form(0),
    session_token: str | None = Cookie(None),
):
    """Delete stored history messages older than N days (0 = all)."""
    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    if not file_store:
        return RedirectResponse(
            url="/user/history?error=Service+not+configured",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    days = max(0, int(older_than_days))
    deleted = file_store.clear_messages(older_than_days=days)
    scope = "all" if days == 0 else f"older+than+{days}+day(s)"
    logger.info(f"User {username} cleared history: older_than_days={days}, deleted={deleted}")
    return RedirectResponse(
        url=f"/user/history?message=Cleared+{deleted}+message(s)+({scope})",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# =============================================================================
# Pending Message Queue
# =============================================================================


@router.get("/pending", response_class=HTMLResponse, name="user_pending")
async def user_pending(
    request: Request,
    message: str = "",
    error: str = "",
    session_token: str | None = Cookie(None),
):
    """Pending messages queue — messages awaiting manual send."""
    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    if auth.user_must_change_password(username):
        return RedirectResponse(
            url="/user/change-password?required=1",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    pending = []
    if file_store:
        pending = file_store.list_pending()

    return templates.TemplateResponse(
        request,
        "user/pending.html",
        {
            "title": "Pending Queue",
            "username": username,
            "pending": pending,
            "pending_count": len(pending),
            "message": message,
            "error": error,
            **get_branding(),
        },
    )


@router.post("/pending/bulk-send", name="user_bulk_send_pending")
async def user_bulk_send_pending(
    request: Request,
    message_ids: list[str] = Form(default=[]),
    session_token: str | None = Cookie(None),
):
    """Send multiple selected pending messages."""
    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    from .file_store import FileStoreError
    from .gateway_client import GatewayError, GatewayUnavailableError

    if not file_store or not gateway_client:
        return RedirectResponse(
            url="/user/pending?error=Service+not+configured",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if not message_ids:
        return RedirectResponse(
            url="/user/pending?error=No+messages+selected",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    sent = 0
    failed = 0
    for msg_id in message_ids:
        try:
            record = file_store.get_pending(msg_id)
            wrapped = record.get("wrapped", record.get("message", {}))
            await gateway_client.send_wrapped(wrapped)
            file_store.remove_pending(msg_id)
            sent += 1
            logger.info(f"User {username} sent pending message: {msg_id}")
        except (GatewayUnavailableError, GatewayError) as e:
            failed += 1
            logger.error(f"Failed to send pending message {msg_id}: {e}")
        except FileStoreError:
            failed += 1

    if failed == 0:
        return RedirectResponse(
            url=f"/user/pending?message={sent}+message(s)+sent+successfully",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/user/pending?error={sent}+sent,+{failed}+failed.+Check+gateway+availability.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/pending/bulk-discard", name="user_bulk_discard_pending")
async def user_bulk_discard_pending(
    request: Request,
    message_ids: list[str] = Form(default=[]),
    session_token: str | None = Cookie(None),
):
    """Discard multiple selected pending messages."""
    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    if not message_ids:
        return RedirectResponse(
            url="/user/pending?error=No+messages+selected",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    discarded = 0
    for msg_id in message_ids:
        if file_store and file_store.remove_pending(msg_id):
            discarded += 1
            logger.info(f"User {username} discarded pending message: {msg_id}")

    return RedirectResponse(
        url=f"/user/pending?message={discarded}+message(s)+discarded",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get(
    "/pending/{message_id}/edit-token",
    response_class=HTMLResponse,
    name="user_edit_pending_token",
)
async def user_edit_pending_token_form(
    request: Request,
    message_id: str,
    error: str = "",
    message: str = "",
    session_token: str | None = Cookie(None),
):
    """
    Show a form pre-filled with the current JWT token + expiry so the
    user can edit them. Saving returns to /user/pending where the admin
    can then click Send Now to observe how the gateway reacts.
    """
    from .file_store import FileStoreError

    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    if not file_store:
        return RedirectResponse(
            url="/user/pending?error=Service+not+configured",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        record = file_store.get_pending(message_id)
    except FileStoreError:
        return RedirectResponse(
            url="/user/pending?error=Pending+message+not+found",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    wrapped = record.get("wrapped", {}) or {}
    inner = record.get("message", {}) or {}
    return templates.TemplateResponse(
        request,
        "user/edit_token.html",
        {
            "title": "Edit JWT Token",
            "username": username,
            "message_id": message_id,
            "message_inner": inner,
            "token": wrapped.get("token", ""),
            "expires_at": wrapped.get("expires_at", ""),
            "token_edits": record.get("token_edits", []),
            "error": error,
            "message": message,
            **get_branding(),
        },
    )


@router.post("/pending/{message_id}/edit-token", name="user_edit_pending_token_submit")
async def user_edit_pending_token_submit(
    request: Request,
    message_id: str,
    token: str = Form(...),
    expires_at: str = Form(""),
    session_token: str | None = Cookie(None),
):
    """
    Save the edited JWT token (and optional new expiry) back to the
    pending record so the next Send Now uses the modified cert.
    """
    from .file_store import FileStoreError

    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    if not file_store:
        return RedirectResponse(
            url="/user/pending?error=Service+not+configured",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        file_store.update_pending_token(
            message_id=message_id,
            token=token,
            expires_at=expires_at.strip() or None,
        )
    except FileStoreError as e:
        logger.warning(f"Failed to update pending token for {message_id}: {e}")
        return RedirectResponse(
            url=f"/user/pending/{message_id}/edit-token?error={str(e)[:80].replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    logger.info(f"User {username} edited JWT token on pending message {message_id}")
    return RedirectResponse(
        url="/user/pending?message=Token+updated.+Click+Send+Now+to+forward+the+modified+cert.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/pending/{message_id}/send", name="user_send_pending")
async def user_send_pending(
    request: Request, message_id: str, session_token: str | None = Cookie(None)
):
    """Send a pending message from the queue."""
    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    from .file_store import FileStoreError
    from .gateway_client import GatewayError, GatewayUnavailableError

    if not file_store or not gateway_client:
        return RedirectResponse(
            url="/user/pending?error=Service+not+configured",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        record = file_store.get_pending(message_id)
    except FileStoreError:
        return RedirectResponse(
            url="/user/pending?error=Pending+message+not+found",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    wrapped = record.get("wrapped", record.get("message", {}))
    try:
        await gateway_client.send_wrapped(wrapped)
        file_store.remove_pending(message_id)
        logger.info(f"User {username} sent pending message: {message_id}")
        return RedirectResponse(
            url=f"/user/pending?message=Message+sent+successfully!+ID:+{message_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except GatewayUnavailableError:
        return RedirectResponse(
            url="/user/pending?error=Gateway+unavailable.+Please+try+again+later.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except GatewayError:
        return RedirectResponse(
            url="/user/pending?error=Gateway+rejected+the+message.",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/pending/{message_id}/discard", name="user_discard_pending")
async def user_discard_pending(
    request: Request, message_id: str, session_token: str | None = Cookie(None)
):
    """Discard a pending message from the queue."""
    redirect, username = require_auth(session_token)
    if redirect:
        return redirect

    if file_store and file_store.remove_pending(message_id):
        logger.info(f"User {username} discarded pending message: {message_id}")
        return RedirectResponse(
            url=f"/user/pending?message=Message+discarded:+{message_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url="/user/pending?error=Pending+message+not+found",
        status_code=status.HTTP_303_SEE_OTHER,
    )
