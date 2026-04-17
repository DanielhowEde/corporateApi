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
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import config
from .key_manager import KeyManager, KeyManagerError
from .whitelist import ProjectWhitelist, WhitelistError
from .utils import setup_logging
from . import audit
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

# Gateway client (will be set by main.py)
gateway_client = None

# Key manager instance
key_manager = KeyManager()


def set_whitelist(wl: ProjectWhitelist) -> None:
    """Set the whitelist instance for admin routes."""
    global whitelist
    whitelist = wl


def set_gateway_client(client) -> None:
    """Set the gateway client for user sync."""
    global gateway_client
    gateway_client = client


async def _sync_user(username: str, action: str = "upsert") -> None:
    """Push user data to gateway for low-side sync (best-effort)."""
    if not gateway_client:
        return
    users = auth._load_users()
    user = users.get(username, {})
    payload = {
        "username": username,
        "action": action,
        "password_hash": user.get("password_hash", ""),
        "enabled": user.get("enabled", True),
        "must_change_password": user.get("must_change_password", True),
        "allowed_projects": user.get("allowed_projects", []),
    }
    await gateway_client.sync_user(payload)


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
    return templates.TemplateResponse(
        request,
        "admin/login.html",
        {"title": "Admin Login", "error": error, **get_branding()},
    )


@router.post("/login", name="admin_login_submit")
async def admin_login_submit(
    request: Request, username: str = Form(...), password: str = Form(...)
):
    """Process admin login."""
    username = username.strip()
    if auth.verify_admin_credentials(username, password):
        token = auth.create_admin_session(username)
        logger.info(f"Admin logged in: {username}")
        response = RedirectResponse(
            url="/admin/", status_code=status.HTTP_303_SEE_OTHER
        )
        response.set_cookie(
            key="admin_session",
            value=token,
            httponly=True,
            samesite="lax",
            max_age=8 * 60 * 60,  # 8 hours
        )
        return response

    reason = auth.classify_login_failure(username, role="admin")
    audit.record_failed_login(
        username=username, source="corporate-admin", reason=reason
    )
    return RedirectResponse(
        url="/admin/login?error=Invalid+username+or+password",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/logout", name="admin_logout")
async def admin_logout(admin_session: Optional[str] = Cookie(None)):
    """Admin logout."""
    if admin_session:
        auth.invalidate_session(admin_session)
        logger.info("Admin logged out")

    response = RedirectResponse(
        url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
    )
    response.delete_cookie("admin_session")
    return response


# =============================================================================
# Admin Web Pages (Protected)
# =============================================================================


@router.get("/", response_class=HTMLResponse, name="admin_dashboard")
async def admin_dashboard(
    request: Request, admin_session: Optional[str] = Cookie(None)
):
    """Admin dashboard home page."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {"title": "Admin Dashboard", **get_branding()},
    )


@router.get("/projects", response_class=HTMLResponse, name="admin_projects")
async def admin_projects(
    request: Request,
    message: str = "",
    error: str = "",
    admin_session: Optional[str] = Cookie(None),
):
    """Project whitelist management page."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    projects = whitelist.list_projects() if whitelist else []
    return templates.TemplateResponse(
        request,
        "admin/projects.html",
        {"title": "Project Whitelist",
            "projects": projects,
            "message": message,
            "error": error,
            **get_branding(),
        },
    )


@router.post("/projects/add", name="admin_add_project")
async def admin_add_project(
    request: Request,
    project_code: str = Form(...),
    enabled: str = Form("off"),
    admin_session: Optional[str] = Cookie(None),
):
    """Add a new project to the whitelist."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    code = project_code.upper().strip()
    is_enabled = enabled.lower() in ("true", "on", "yes", "1")

    if not validate_project_code(code):
        return RedirectResponse(
            url="/admin/projects?error=Invalid+project+code.+Must+be+3+uppercase+alphanumeric+characters.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        whitelist.add_project(code, enabled=is_enabled)
        logger.info(f"Admin added project: {code}")
        return RedirectResponse(
            url=f"/admin/projects?message=Project+{code}+added+successfully",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except WhitelistError as e:
        logger.warning(f"Admin failed to add project: {code}, error={e}")
        return RedirectResponse(
            url="/admin/projects?error=Project+already+exists",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/projects/{project_code}/enable", name="admin_enable_project")
async def admin_enable_project(
    project_code: str, admin_session: Optional[str] = Cookie(None)
):
    """Enable a project."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    code = project_code.upper()
    if whitelist.enable_project(code):
        logger.info(f"Admin enabled project: {code}")
        return RedirectResponse(
            url=f"/admin/projects?message=Project+{code}+enabled",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url="/admin/projects?error=Project+not+found",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/projects/{project_code}/disable", name="admin_disable_project")
async def admin_disable_project(
    project_code: str, admin_session: Optional[str] = Cookie(None)
):
    """Disable a project."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    code = project_code.upper()
    if whitelist.disable_project(code):
        logger.info(f"Admin disabled project: {code}")
        return RedirectResponse(
            url=f"/admin/projects?message=Project+{code}+disabled",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url="/admin/projects?error=Project+not+found",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/projects/{project_code}/remove", name="admin_remove_project")
async def admin_remove_project(
    project_code: str, admin_session: Optional[str] = Cookie(None)
):
    """Remove a project from the whitelist."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    code = project_code.upper()
    if whitelist.remove_project(code):
        logger.info(f"Admin removed project: {code}")
        return RedirectResponse(
            url=f"/admin/projects?message=Project+{code}+removed",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url="/admin/projects?error=Project+not+found",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/audit", response_class=HTMLResponse, name="admin_audit")
async def admin_audit_page(
    request: Request, admin_session: Optional[str] = Cookie(None)
):
    """Central audit log: recent failed login attempts across the estate."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    events = audit.list_recent_failed_logins(limit=200)
    return templates.TemplateResponse(
        request,
        "admin/audit.html",
        {"title": "Audit Log",
            "events": events,
            "event_count": len(events),
            **get_branding(),
        },
    )


@router.get("/certs", response_class=HTMLResponse, name="admin_certs")
async def admin_certs(
    request: Request,
    message: str = "",
    error: str = "",
    admin_session: Optional[str] = Cookie(None),
):
    """
    Certificate management page.

    Note: Actual certificate management is handled by the reverse proxy
    and PKI infrastructure. This page provides visibility and documentation.
    """
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    # Placeholder cert info - in production, this would read from cert files or API
    certs = [
        {
            "name": "Server Certificate",
            "subject": "CN=corporate-api.example.com",
            "issuer": "CN=Internal CA",
            "expires": "2027-01-30",
            "status": "valid",
        },
        {
            "name": "Gateway Client Cert",
            "subject": "CN=gateway.dmz.example.com",
            "issuer": "CN=Internal CA",
            "expires": "2027-01-30",
            "status": "valid",
        },
    ]

    return templates.TemplateResponse(
        request,
        "admin/certs.html",
        {"title": "Certificate Management",
            "certs": certs,
            "message": message,
            "error": error,
            "note": "Certificate operations are managed by the PKI team. Contact security@example.com for cert renewal requests.",
            **get_branding(),
        },
    )


# =============================================================================
# User Management (Admin Functions)
# =============================================================================


@router.get("/users", response_class=HTMLResponse, name="admin_users")
async def admin_users(
    request: Request,
    message: str = "",
    error: str = "",
    admin_session: Optional[str] = Cookie(None),
):
    """User management page."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    users = auth.list_users()
    admins = auth.list_admins()

    # Augment each user tuple with their allowed_projects list so the
    # template can render per-user project access controls.
    users_with_projects = [
        {
            "username": username,
            "enabled": enabled,
            "created": created,
            "allowed_projects": auth.get_user_allowed_projects(username),
        }
        for (username, enabled, created) in users
    ]

    # Full list of whitelist project codes (enabled or not) — admin may want
    # to grant access to disabled ones in advance.
    whitelist_projects = (
        [code for code, _enabled in whitelist.list_projects()] if whitelist else []
    )

    return templates.TemplateResponse(
        request,
        "admin/users.html",
        {"title": "User Management",
            "users": users_with_projects,
            "admins": admins,
            "user_count": len(users_with_projects),
            "admin_count": len(admins),
            "whitelist_projects": sorted(whitelist_projects),
            "message": message,
            "error": error,
            **get_branding(),
        },
    )


@router.post("/users/add", name="admin_add_user")
async def admin_add_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    enabled: str = Form("off"),
    admin_session: Optional[str] = Cookie(None),
):
    """Create a new user account."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    is_enabled = enabled.lower() in ("true", "on", "yes", "1")
    success, message = auth.create_user(username.strip(), password, is_enabled)

    if success:
        logger.info(f"Admin created user: {username}")
        await _sync_user(username.strip(), "upsert")
        return RedirectResponse(
            url=f"/admin/users?message={message.replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    logger.warning(f"Admin failed to create user: {username}, error={message}")
    return RedirectResponse(
        url=f"/admin/users?error={message.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/users/{username}/enable", name="admin_enable_user")
async def admin_enable_user(username: str, admin_session: Optional[str] = Cookie(None)):
    """Enable a user account."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    success, message = auth.enable_user(username)

    if success:
        logger.info(f"Admin enabled user: {username}")
        await _sync_user(username, "upsert")
        return RedirectResponse(
            url=f"/admin/users?message={message.replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=f"/admin/users?error={message.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/users/{username}/disable", name="admin_disable_user")
async def admin_disable_user(
    username: str, admin_session: Optional[str] = Cookie(None)
):
    """Disable a user account."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    success, message = auth.disable_user(username)

    if success:
        logger.info(f"Admin disabled user: {username}")
        await _sync_user(username, "upsert")
        return RedirectResponse(
            url=f"/admin/users?message={message.replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=f"/admin/users?error={message.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/users/{username}/delete", name="admin_delete_user")
async def admin_delete_user(username: str, admin_session: Optional[str] = Cookie(None)):
    """Delete a user account."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    success, message = auth.delete_user(username)

    if success:
        logger.info(f"Admin deleted user: {username}")
        await gateway_client.sync_user(
            {"username": username, "action": "delete"}
        ) if gateway_client else None
        return RedirectResponse(
            url=f"/admin/users?message={message.replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=f"/admin/users?error={message.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/users/{username}/projects", name="admin_set_user_projects")
async def admin_set_user_projects(
    username: str,
    allowed: list[str] = Form(default=[]),
    admin_session: Optional[str] = Cookie(None),
):
    """Replace the list of project codes this user may send to."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    success, message = auth.set_user_allowed_projects(username, allowed)

    if success:
        logger.info(f"Admin set allowed_projects for {username}: {allowed}")
        await _sync_user(username, "upsert")
        return RedirectResponse(
            url=f"/admin/users?message={message.replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=f"/admin/users?error={message.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/users/{username}/reset-password", name="admin_reset_user_password")
async def admin_reset_user_password(
    username: str,
    new_password: str = Form(...),
    admin_session: Optional[str] = Cookie(None),
):
    """Reset a user's password."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    success, message = auth.update_user_password(username, new_password)

    if success:
        logger.info(f"Admin reset password for user: {username}")
        await _sync_user(username, "upsert")
        return RedirectResponse(
            url=f"/admin/users?message={message.replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=f"/admin/users?error={message.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER,
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
    admin_session: Optional[str] = Cookie(None),
):
    """Create a new admin account."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    is_enabled = enabled.lower() in ("true", "on", "yes", "1")
    success, message = auth.create_admin_user(username.strip(), password, is_enabled)

    if success:
        logger.info(f"Admin created admin user: {username}")
        return RedirectResponse(
            url=f"/admin/users?message={message.replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    logger.warning(f"Admin failed to create admin user: {username}, error={message}")
    return RedirectResponse(
        url=f"/admin/users?error={message.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/admins/{username}/delete", name="admin_delete_admin")
async def admin_delete_admin(
    username: str, admin_session: Optional[str] = Cookie(None)
):
    """Delete an admin account (cannot delete the last admin)."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    success, message = auth.delete_admin_user(username)

    if success:
        logger.info(f"Admin deleted admin user: {username}")
        return RedirectResponse(
            url=f"/admin/users?message={message.replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=f"/admin/users?error={message.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# =============================================================================
# Key Management
# =============================================================================


@router.get("/keys", response_class=HTMLResponse, name="admin_keys")
async def admin_keys(
    request: Request,
    message: str = "",
    error: str = "",
    admin_session: Optional[str] = Cookie(None),
):
    """Key management page."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    keys = key_manager.list_keys()
    return templates.TemplateResponse(
        request,
        "admin/keys.html",
        {"title": "Key Management",
            "keys": keys,
            "message": message,
            "error": error,
            **get_branding(),
        },
    )


@router.post("/keys/generate", name="admin_generate_key")
async def admin_generate_key(
    request: Request,
    key_name: str = Form(...),
    key_size: int = Form(2048),
    admin_session: Optional[str] = Cookie(None),
):
    """Generate a new RSA key pair."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    name = key_name.strip()
    if not name:
        return RedirectResponse(
            url="/admin/keys?error=Key+name+is+required",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        metadata = key_manager.generate_key_pair(name, key_size)
        logger.info(f"Admin generated key: {metadata['key_id']}, name={name}")

        # Best-effort sync to low-side via the gateway
        if gateway_client:
            ca_pem = key_manager.get_ca_cert_pem()
            if ca_pem:
                await gateway_client.sync_ca(ca_pem)

            cert_path = key_manager.get_file(metadata["key_id"], "client.crt")
            if cert_path:
                await gateway_client.sync_client_cert(
                    {
                        "key_id": metadata["key_id"],
                        "name": name,
                        "cert_pem": cert_path.read_text(encoding="utf-8"),
                        "action": "upsert",
                    }
                )

        return RedirectResponse(
            url=f"/admin/keys?message=Key+pair+generated+and+synced+to+low-side:+{name}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except KeyManagerError as e:
        logger.error(f"Key generation failed: {e}")
        return RedirectResponse(
            url=f"/admin/keys?error={str(e)[:80].replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.get(
    "/keys/{key_id}/public", response_class=HTMLResponse, name="admin_view_public_key"
)
async def admin_view_public_key(
    request: Request, key_id: str, admin_session: Optional[str] = Cookie(None)
):
    """View a public key."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    metadata = key_manager.get_key(key_id)
    public_pem = key_manager.get_public_key_pem(key_id)

    if not metadata or not public_pem:
        return RedirectResponse(
            url="/admin/keys?error=Key+not+found", status_code=status.HTTP_303_SEE_OTHER
        )

    keys = key_manager.list_keys()
    return templates.TemplateResponse(
        request,
        "admin/keys.html",
        {"title": "Key Management",
            "keys": keys,
            "message": "",
            "error": "",
            "public_key_display": public_pem,
            "public_key_name": metadata.get("name", key_id),
            **get_branding(),
        },
    )


@router.post("/keys/sync-ca", name="admin_sync_ca")
async def admin_sync_ca(admin_session: Optional[str] = Cookie(None)):
    """Manually push the CA cert to low-side via the gateway."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    ca_pem = key_manager.get_ca_cert_pem()
    if not ca_pem:
        return RedirectResponse(
            url="/admin/keys?error=CA+not+yet+created",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if gateway_client:
        await gateway_client.sync_ca(ca_pem)
        logger.info("Admin manually synced CA to low-side")
        return RedirectResponse(
            url="/admin/keys?message=CA+sync+sent+to+gateway",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url="/admin/keys?error=Gateway+client+not+configured",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/keys/{key_id}/sync", name="admin_sync_client_cert")
async def admin_sync_client_cert(
    key_id: str, admin_session: Optional[str] = Cookie(None)
):
    """Manually push a client cert to low-side via the gateway."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    metadata = key_manager.get_key(key_id)
    cert_path = key_manager.get_file(key_id, "client.crt")
    if not metadata or not cert_path:
        return RedirectResponse(
            url="/admin/keys?error=Key+not+found",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if gateway_client:
        await gateway_client.sync_client_cert(
            {
                "key_id": key_id,
                "name": metadata.get("name", ""),
                "cert_pem": cert_path.read_text(encoding="utf-8"),
                "action": "upsert",
            }
        )
        logger.info(f"Admin manually synced client cert: {key_id}")
        return RedirectResponse(
            url=f"/admin/keys?message=Cert+sync+sent+for+{metadata.get('name', key_id)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url="/admin/keys?error=Gateway+client+not+configured",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/keys/ca.crt", name="admin_download_ca")
async def admin_download_ca(admin_session: Optional[str] = Cookie(None)):
    """Download the CA certificate (public, safe to distribute)."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    ca_pem = key_manager.get_ca_cert_pem()
    if not ca_pem:
        return PlainTextResponse("CA not yet created", status_code=404)

    return PlainTextResponse(
        ca_pem,
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="ca.crt"'},
    )


@router.get("/keys/{key_id}/download/{filename}", name="admin_download_key_file")
async def admin_download_key_file(
    key_id: str, filename: str, admin_session: Optional[str] = Cookie(None)
):
    """
    Download a file belonging to a key pair.

    Allowed filenames:
      - private.pem  (private key, keep secret)
      - public.pem   (public key)
      - client.crt   (signed client certificate)
      - client.pfx   (PKCS#12 bundle for Postman/browsers)
    """
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    file_path = key_manager.get_file(key_id, filename)
    if not file_path:
        return PlainTextResponse("File not found", status_code=404)

    metadata = key_manager.get_key(key_id) or {}
    safe_name = metadata.get("name", key_id).replace(" ", "_")
    download_name = f"{safe_name}-{filename}"

    media_types = {
        "private.pem": "application/x-pem-file",
        "public.pem": "application/x-pem-file",
        "client.crt": "application/x-pem-file",
        "client.pfx": "application/x-pkcs12",
    }

    logger.info(f"Admin downloaded {filename} for key {key_id}")
    return FileResponse(
        path=str(file_path),
        media_type=media_types.get(filename, "application/octet-stream"),
        filename=download_name,
    )


@router.post("/keys/{key_id}/revoke", name="admin_revoke_key")
async def admin_revoke_key(key_id: str, admin_session: Optional[str] = Cookie(None)):
    """Revoke a key pair."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    metadata = key_manager.get_key(key_id)
    if key_manager.revoke_key(key_id):
        logger.info(f"Admin revoked key: {key_id}")

        # Sync revocation to low-side (best-effort)
        if gateway_client and metadata:
            await gateway_client.sync_client_cert(
                {
                    "key_id": key_id,
                    "name": metadata.get("name", ""),
                    "cert_pem": "",
                    "action": "revoke",
                }
            )

        return RedirectResponse(
            url="/admin/keys?message=Key+revoked+and+synced+to+low-side",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url="/admin/keys?error=Key+not+found", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/keys/{key_id}/delete", name="admin_delete_key")
async def admin_delete_key(key_id: str, admin_session: Optional[str] = Cookie(None)):
    """Delete a key pair permanently."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    metadata = key_manager.get_key(key_id)
    if key_manager.delete_key(key_id):
        logger.info(f"Admin deleted key: {key_id}")

        # Sync deletion to low-side (best-effort)
        if gateway_client and metadata:
            await gateway_client.sync_client_cert(
                {
                    "key_id": key_id,
                    "name": metadata.get("name", ""),
                    "cert_pem": "",
                    "action": "delete",
                }
            )

        return RedirectResponse(
            url="/admin/keys?message=Key+deleted+and+low-side+notified",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url="/admin/keys?error=Key+not+found", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/admins/{username}/reset-password", name="admin_reset_admin_password")
async def admin_reset_admin_password(
    username: str,
    new_password: str = Form(...),
    admin_session: Optional[str] = Cookie(None),
):
    """Reset an admin's password."""
    if not require_admin_auth(admin_session):
        return RedirectResponse(
            url="/admin/login", status_code=status.HTTP_303_SEE_OTHER
        )

    success, message = auth.update_user_password(username, new_password)

    if success:
        logger.info(f"Admin reset password for admin: {username}")
        return RedirectResponse(
            url=f"/admin/users?message={message.replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=f"/admin/users?error={message.replace(' ', '+')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
