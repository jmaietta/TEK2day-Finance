"""Firebase server-side auth for TEK2day Finance.

Standard Firebase pattern, written fresh (no Kilby code/imports): the web client
signs in with the Firebase JS SDK and posts its ID token here; we verify the token
with Google's library, then issue an HTTPOnly session cookie backed by a Firestore
`sessions/{sid}` doc on the yfinance-cli project (same Firestore as market data).

Auth is OPTIONAL — get_uid() returns None for anonymous visitors and never raises.
Only watchlist/export (Step 2) will call require_uid() to gate those actions.
"""
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import config
import storage

router = APIRouter()

SESSIONS = "sessions"


def _verify_firebase_token(token: str) -> dict:
    """Verify a Firebase ID token (Google's documented server-side call)."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    return id_token.verify_firebase_token(
        token, google_requests.Request(), audience=config.FIREBASE_PROJECT_ID
    )


def _session_doc(sid: str):
    return storage.get_db().collection(SESSIONS).document(sid)


def _read_session(sid: str | None) -> dict | None:
    if not sid:
        return None
    snap = _session_doc(sid).get()
    return snap.to_dict() if snap.exists else None


def get_uid(request: Request) -> str | None:
    """The signed-in uid, or None. Never raises — auth is optional."""
    sess = _read_session(request.cookies.get(config.COOKIE_NAME))
    return sess.get("uid") if sess else None


def require_uid(request: Request) -> str:
    """uid for a signed-in user, else HTTP 401. Use to gate watchlist/export."""
    uid = get_uid(request)
    if not uid:
        raise HTTPException(status_code=401, detail="Sign in required")
    return uid


def require_admin(request: Request) -> str:
    """Email of a signed-in admin, else 401/403. Gates /admin/*.

    Three things must hold, not one:

    1. A valid session exists (401 otherwise).
    2. The address is on the allowlist (403 otherwise).
    3. Google verified that address (403 otherwise).

    Point 3 matters. Without it, anyone who could create a Firebase account
    claiming an allowlisted address — possible if email/password signup is ever
    enabled — would land on the allowlist without ever proving they own it.
    Google Sign-In satisfies it for free, which is how the real admins sign in.
    """
    sess = _read_session(request.cookies.get(config.COOKIE_NAME))
    if not sess:
        raise HTTPException(status_code=401, detail="Sign in required")

    email = str(sess.get("email") or "").strip().lower()
    if email not in config.ADMIN_EMAILS or not sess.get("email_verified"):
        # Deliberately identical for "not an admin" and "unverified address":
        # a probing caller learns nothing about who is on the list.
        raise HTTPException(status_code=403, detail="Not authorised")
    return email


def _configured() -> bool:
    return bool(
        config.FIREBASE_WEB_API_KEY
        and config.FIREBASE_PROJECT_ID
        and config.FIREBASE_AUTH_DOMAIN
        and config.FIREBASE_APP_ID
    )


def _cookie_kwargs(request: Request) -> dict:
    # secure only over HTTPS so the cookie still works on http://localhost.
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": request.url.scheme == "https",
        "path": "/",
    }


@router.get("/firebase/config")
def firebase_config():
    """Web Firebase config for the client SDK (safe to expose; these are public)."""
    ok = _configured()
    return {
        "configured": ok,
        "firebaseConfig": {
            "apiKey": config.FIREBASE_WEB_API_KEY,
            "authDomain": config.FIREBASE_AUTH_DOMAIN,
            "projectId": config.FIREBASE_PROJECT_ID,
            "storageBucket": config.FIREBASE_STORAGE_BUCKET,
            "messagingSenderId": config.FIREBASE_MESSAGING_SENDER_ID,
            "appId": config.FIREBASE_APP_ID,
        }
        if ok
        else None,
    }


class LoginBody(BaseModel):
    token: str


@router.post("/auth/login")
def auth_login(body: LoginBody, request: Request):
    try:
        decoded = _verify_firebase_token(body.token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid auth token") from exc
    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid auth token")

    uid = decoded.get("uid") or decoded.get("sub")
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid auth token")
    email = str(decoded.get("email") or "").strip()

    sid = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    _session_doc(sid).set(
        {
            "uid": uid,
            "email": email,
            # Recorded because require_admin() needs to know Google actually
            # verified this address, not merely that the token carries it.
            # Sessions created before this field existed have no value, so an
            # admin signed in from an older session must sign in again — the
            # safe direction to fail.
            "email_verified": bool(decoded.get("email_verified")),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=config.SESSION_TTL_DAYS)).isoformat(),
        }
    )
    resp = JSONResponse({"ok": True, "uid": uid, "email": email})
    resp.set_cookie(
        config.COOKIE_NAME,
        sid,
        max_age=config.SESSION_TTL_DAYS * 86400,
        **_cookie_kwargs(request),
    )
    return resp


@router.post("/auth/logout")
def auth_logout(request: Request):
    sid = request.cookies.get(config.COOKIE_NAME)
    if sid:
        try:
            _session_doc(sid).delete()
        except Exception:
            pass
    resp = JSONResponse({"ok": True})
    ck = _cookie_kwargs(request)
    resp.delete_cookie(
        config.COOKIE_NAME, path="/", httponly=True,
        samesite=ck["samesite"], secure=ck["secure"],
    )
    return resp


@router.get("/auth/me")
def auth_me(request: Request):
    sess = _read_session(request.cookies.get(config.COOKIE_NAME))
    if not sess:
        return {"uid": None, "email": ""}
    return {"uid": sess.get("uid"), "email": sess.get("email", "")}
