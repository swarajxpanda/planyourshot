from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from planyourshot.web import serialize, usage
from planyourshot.web.db import get_db
from planyourshot.web.deps import current_user
from planyourshot.web.limits import limits_for
from planyourshot.web.ratelimit import auth_rate_limit
from planyourshot.web.models import User, utcnow
from planyourshot.web.settings import get_settings

router = APIRouter(prefix="/api", tags=["auth"])
_limited = [Depends(auth_rate_limit)]  # every /api/auth/* route: per-IP limit

_oauth: OAuth | None = None


def _google():
    global _oauth
    s = get_settings()
    if not s.google_configured:
        raise HTTPException(503, "Google sign-in isn't configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.")
    if _oauth is None:
        _oauth = OAuth()
        _oauth.register(
            name="google",
            client_id=s.google_client_id,
            client_secret=s.google_client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    return _oauth.google


def _sign_in(request: Request, db: Session, *, sub: str, email: str, name: str, picture: str) -> User:
    # Column lengths are enforced by Postgres; Google values are normally far shorter.
    sub, email, name, picture = sub[:255], email[:320], name[:255], picture[:1024]
    user = db.scalar(select(User).where(User.google_sub == sub))
    if user is None:
        user = User(google_sub=sub, email=email, name=name, picture=picture)
        db.add(user)
    else:
        user.email, user.name, user.picture, user.last_login_at = email, name, picture, utcnow()
    if user.terms_accepted_at is None:
        # Signing in from the login page means agreeing to the Terms and Privacy Policy
        # shown under the sign-in button.
        user.terms_accepted_at = utcnow()
    db.commit()
    request.session.clear()  # new session on login
    request.session["uid"] = user.id
    return user


@router.get("/auth/config", dependencies=_limited)
def auth_config():
    s = get_settings()
    return {"google": s.google_configured, "dev_login": s.auth_dev_login}


@router.get("/auth/login", dependencies=_limited)
async def login(request: Request):
    google = _google()
    return await google.authorize_redirect(request, f"{get_settings().app_base_url}/api/auth/callback")


@router.get("/auth/callback", dependencies=_limited)
async def callback(request: Request, db: Session = Depends(get_db)):
    front = get_settings().frontend_url
    try:
        token = await _google().authorize_access_token(request)
    except OAuthError:
        return RedirectResponse(f"{front}/login?error=oauth")
    info = token.get("userinfo") or {}
    if not info.get("sub") or not info.get("email") or info.get("email_verified") is False:
        return RedirectResponse(f"{front}/login?error=unverified")
    _sign_in(
        request, db,
        sub=info["sub"], email=info["email"],
        name=info.get("name") or info["email"].split("@")[0], picture=info.get("picture") or "",
    )
    return RedirectResponse(f"{front}/")


class DevLogin(BaseModel):
    email: str = "dev@localhost"
    name: str = "Dev User"


@router.post("/auth/dev-login", dependencies=_limited)
def dev_login(body: DevLogin, request: Request, db: Session = Depends(get_db)):
    """Local development only (AUTH_DEV_LOGIN=1): sign in without Google."""
    if not get_settings().auth_dev_login:
        raise HTTPException(404, "Not found")
    email = body.email.strip().lower()[:320] or "dev@localhost"
    user = _sign_in(request, db, sub=f"dev:{email}", email=email, name=body.name.strip()[:100] or "Dev User", picture="")
    return serialize.user(user)


@router.post("/auth/logout", dependencies=_limited)
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    s = get_settings()
    return {**serialize.user(user), "max_scripts": limits_for(user).max_scripts, "max_upload_mb": s.max_upload_mb,
            "is_admin": user.email.lower() in s.admin_emails}


@router.get("/me/usage")
def my_usage(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Plan name, every limit, and current counts (rolling windows, as enforced)."""
    return usage.usage_summary(db, user)
