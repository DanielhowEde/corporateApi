"""
Admin web interface for Corporate DMZ API.

Provides a simple web UI for:
- Admin login/logout
- Managing project whitelist
- Managing user accounts
- Viewing certificate status (placeholder)
"""
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Cookie, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import config
from .whitelist import ProjectWhitelist, WhitelistError
from .utils import setup_logging
from . import auth

logger = setup_logging("admin")

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
router = APIRouter(prefix="/admin", tags=["Admin"])

# Whitelist instance (will be set by main.py)
whitelist: Optional[ProjectWhitelist] = None


def set_whitelist(wl: ProjectWhitelist) -> None:
    """Set the whitelist instance for admin routes."""
    global whitelist
    whitelist = wl


def validate_project_code(code: str) -> bool:
    """Validate project code format."""
    return bool(re.match(r"^[A-Z0-9]{3}$", code.upper()))


def require_admin_auth(session_token: Optional[str]) -> bool:
    """Check if admin is authenticated."""
    return auth.verify_admin_session(session_token)


# =============================================================================
# Admin Authentication
# =============================================================================

@router.get("/login", response_class=HTMLResponse, name="admin_login")
async def admin_login_page(request: Request, error: str = ""):
    """Admin login page."""
    return templates.TemplateResponse("admin/login.html", {
        "request": request,
        "title": "Admin Login",
        "error": error,
        **get_branding()
    })


@router.post("/login", name="admin_login_submit")
async def admin_login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    """Process admin login."""
    username = username.strip()
    if auth.verify_admin_credentials(username, password):
        token = auth.create_admin_session(username)
        logger.info(f"Admin logged in: {username}")
        response = RedirectResponse(
            url="/admin/",
            status_code=status.HTTP_303_SEE_OTHER
        )
        response.set_cookie(
            key="admin_session",
            value=token,
            httponly=True,
            samesite="lax",
            max_age=8 * 60 * 60  # 8 hours
        )
        return response

    logger.warning(f"Failed admin login attempt: {username}")
    return RedirectResponse(
        url="/admin/login?error=Invalid+username+or+password",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/logout", name="admin_logout")
async def admin_logout(admin_session: Optional[str] = Cookie(None)):
    """Admin logout."""
    if admin_session:
        auth.invalidate_session(admin_session)
        logger.info("Admin logged out")

    response = RedirectResponse(
        url="/admin/login",
        status_code=status.HTTP_303_SEE_OTHER
    )
    response.delete_cookie("admin_session")
    return response


# =============================================================================
# Admin Web Pages (Protected)
# =============================================================================

@router.get("/", response_class=HTMLResponse, name="admin_dashboard")
async def admin_dashboard(
    request: Request,
    admin_session: Optional[str] = Cookie(None)
):
    """Admin dashboard home page."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "title": "Admin Dashboard",
        **get_branding()
    })


@router.get("/projects", response_class=HTMLResponse, name="admin_projects")
async def admin_projects(
    request: Request,
    message: str = "",
    error: str = "",
    admin_session: Optional[str] = Cookie(None)
):
    """Project whitelist management page."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    projects = whitelist.list_projects() if whitelist else []
    return templates.TemplateResponse("admin/projects.html", {
        "request": request,
        "title": "Project Whitelist",
        "projects": projects,
        "message": message,
        "error": error,
        **get_branding()
    })


@router.post("/projects/add", name="admin_add_project")
async def admin_add_project(
    request: Request,
    project_code: str = Form(...),
    enabled: str = Form("off"),
    admin_session: Optional[str] = Cookie(None)
):
    """Add a new project to the whitelist."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    code = project_code.upper().strip()
    is_enabled = enabled.lower() in ("true", "on", "yes", "1")

    if not validate_project_code(code):
        return RedirectResponse(
            url=f"/admin/projects?error=Invalid+project+code.+Must+be+3+uppercase+alphanumeric+characters.",
            status_code=status.HTTP_303_SEE_OTHER
        )

    try:
        whitelist.add_project(code, enabled=is_enabled)
        logger.info(f"Admin added project: {code}")
        return RedirectResponse(
            url=f"/admin/projects?message=Project+{code}+added+successfully",
            status_code=status.HTTP_303_SEE_OTHER
        )
    except WhitelistError as e:
        logger.warning(f"Admin failed to add project: {code}, error={e}")
        return RedirectResponse(
            url=f"/admin/projects?error=Project+already+exists",
            status_code=status.HTTP_303_SEE_OTHER
        )


@router.post("/projects/{project_code}/enable", name="admin_enable_project")
async def admin_enable_project(
    project_code: str,
    admin_session: Optional[str] = Cookie(None)
):
    """Enable a project."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    code = project_code.upper()
    if whitelist.enable_project(code):
        logger.info(f"Admin enabled project: {code}")
        return RedirectResponse(
            url=f"/admin/projects?message=Project+{code}+enabled",
            status_code=status.HTTP_303_SEE_OTHER
        )
    return RedirectResponse(
        url=f"/admin/projects?error=Project+not+found",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/projects/{project_code}/disable", name="admin_disable_project")
async def admin_disable_project(
    project_code: str,
    admin_session: Optional[str] = Cookie(None)
):
    """Disable a project."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    code = project_code.upper()
    if whitelist.disable_project(code):
        logger.info(f"Admin disabled project: {code}")
        return RedirectResponse(
            url=f"/admin/projects?message=Project+{code}+disabled",
            status_code=status.HTTP_303_SEE_OTHER
        )
    return RedirectResponse(
        url=f"/admin/projects?error=Project+not+found",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/projects/{project_code}/remove", name="admin_remove_project")
async def admin_remove_project(
    project_code: str,
    admin_session: Optional[str] = Cookie(None)
):
    """Remove a project from the whitelist."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    code = project_code.upper()
    if whitelist.remove_project(code):
        logger.info(f"Admin removed project: {code}")
        return RedirectResponse(
            url=f"/admin/projects?message=Project+{code}+removed",
            status_code=status.HTTP_303_SEE_OTHER
        )
    return RedirectResponse(
        url=f"/admin/projects?error=Project+not+found",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/certs", response_class=HTMLResponse, name="admin_certs")
async def admin_certs(
    request: Request,
    message: str = "",
    error: str = "",
    admin_session: Optional[str] = Cookie(None)
):
    """
    Certificate management page.

    Note: Actual certificate management is handled by the reverse proxy
    and PKI infrastructure. This page provides visibility and documentation.
    """
    if not require_admin_auth(admin_session):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    # Placeholder cert info - in production, this would read from cert files or API
    certs = [
        {
            "name": "Server Certificate",
            "subject": "CN=corporate-api.example.com",
            "issuer": "CN=Internal CA",
            "expires": "2027-01-30",
            "status": "valid"
        },
        {
            "name": "Gateway Client Cert",
            "subject": "CN=gateway.dmz.example.com",
            "issuer": "CN=Internal CA",
            "expires": "2027-01-30",
            "status": "valid"
        }
    ]

    return templates.TemplateResponse("admin/certs.html", {
        "request": request,
        "title": "Certificate Management",
        "certs": certs,
        "message": message,
        "error": error,
        "note": "Certificate operations are managed by the PKI team. Contact security@example.com for cert renewal requests.",
        **get_branding()
    })


# =============================================================================
# User Management (Admin Functions)
# =============================================================================

@router.get("/users", response_class=HTMLResponse, name="admin_users")
async def admin_users(
    request: Request,
    message: str = "",
    error: str = "",
    admin_session: Optional[str] = Cookie(None)
):
    """User management page."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    users = auth.list_users()
    admins = auth.list_admins()

    return templates.TemplateResponse("admin/users.html", {
        "request": request,
        "title": "User Management",
        "users": users,
        "admins": admins,
        "user_count": len(users),
        "admin_count": len(admins),
        "message": message,
        "error": error,
        **get_branding()
    })


@router.post("/users/add", name="admin_add_user")
async def admin_add_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    enabled: str = Form("off"),
    admin_session: Optional[str] = Cookie(None)
):
    """Create a new user account."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    is_enabled = enabled.lower() in ("true", "on", "yes", "1")
    success, message = auth.create_user(username.strip(), password, is_enabled)

    if success:
        logger.info(f"Admin created user: {username}")
        return RedirectResponse(
            url=f"/admin/users?message={message.replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER
        )

    logger.warning(f"Admin failed to create user: {username}, error={message}")
    return RedirectResponse(
        url=f"/admin/users?error={message.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/users/{username}/enable", name="admin_enable_user")
async def admin_enable_user(
    username: str,
    admin_session: Optional[str] = Cookie(None)
):
    """Enable a user account."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    success, message = auth.enable_user(username)

    if success:
        logger.info(f"Admin enabled user: {username}")
        return RedirectResponse(
            url=f"/admin/users?message={message.replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER
        )

    return RedirectResponse(
        url=f"/admin/users?error={message.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/users/{username}/disable", name="admin_disable_user")
async def admin_disable_user(
    username: str,
    admin_session: Optional[str] = Cookie(None)
):
    """Disable a user account."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    success, message = auth.disable_user(username)

    if success:
        logger.info(f"Admin disabled user: {username}")
        return RedirectResponse(
            url=f"/admin/users?message={message.replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER
        )

    return RedirectResponse(
        url=f"/admin/users?error={message.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/users/{username}/delete", name="admin_delete_user")
async def admin_delete_user(
    username: str,
    admin_session: Optional[str] = Cookie(None)
):
    """Delete a user account."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    success, message = auth.delete_user(username)

    if success:
        logger.info(f"Admin deleted user: {username}")
        return RedirectResponse(
            url=f"/admin/users?message={message.replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER
        )

    return RedirectResponse(
        url=f"/admin/users?error={message.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/users/{username}/reset-password", name="admin_reset_user_password")
async def admin_reset_user_password(
    username: str,
    new_password: str = Form(...),
    admin_session: Optional[str] = Cookie(None)
):
    """Reset a user's password."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    success, message = auth.update_user_password(username, new_password)

    if success:
        logger.info(f"Admin reset password for user: {username}")
        return RedirectResponse(
            url=f"/admin/users?message={message.replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER
        )

    return RedirectResponse(
        url=f"/admin/users?error={message.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER
    )


# =============================================================================
# Admin User Management
# =============================================================================

@router.post("/admins/add", name="admin_add_admin")
async def admin_add_admin(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    enabled: str = Form("off"),
    admin_session: Optional[str] = Cookie(None)
):
    """Create a new admin account."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    is_enabled = enabled.lower() in ("true", "on", "yes", "1")
    success, message = auth.create_admin_user(username.strip(), password, is_enabled)

    if success:
        logger.info(f"Admin created admin user: {username}")
        return RedirectResponse(
            url=f"/admin/users?message={message.replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER
        )

    logger.warning(f"Admin failed to create admin user: {username}, error={message}")
    return RedirectResponse(
        url=f"/admin/users?error={message.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/admins/{username}/delete", name="admin_delete_admin")
async def admin_delete_admin(
    username: str,
    admin_session: Optional[str] = Cookie(None)
):
    """Delete an admin account (cannot delete the last admin)."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    success, message = auth.delete_admin_user(username)

    if success:
        logger.info(f"Admin deleted admin user: {username}")
        return RedirectResponse(
            url=f"/admin/users?message={message.replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER
        )

    return RedirectResponse(
        url=f"/admin/users?error={message.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/admins/{username}/reset-password", name="admin_reset_admin_password")
async def admin_reset_admin_password(
    username: str,
    new_password: str = Form(...),
    admin_session: Optional[str] = Cookie(None)
):
    """Reset an admin's password."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    success, message = auth.update_user_password(username, new_password)

    if success:
        logger.info(f"Admin reset password for admin: {username}")
        return RedirectResponse(
            url=f"/admin/users?message={message.replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER
        )

    return RedirectResponse(
        url=f"/admin/users?error={message.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER
    )
