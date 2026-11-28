"""Authentication routes: status, initial admin setup, login, logout."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import get_container
from app.container import AppContainer
from app.security.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    extract_request_session_token,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class CredentialsPayload(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=8, max_length=256)
    totp_code: str = ""


class EnableTotpPayload(BaseModel):
    code: str = Field(..., min_length=6, max_length=8)
    secret: str = ""


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=is_https,
    )


@router.get("/status")
def auth_status(request: Request, container: AppContainer = Depends(get_container)) -> dict:
    token = extract_request_session_token(request.headers, request.cookies)
    st = container.auth.status(token)
    return {
        "login_required": st.login_required,
        "has_admin": st.has_admin,
        "authenticated": st.authenticated,
        "username": st.username,
        "totp_enabled": st.totp_enabled,
    }


@router.post("/setup")
def auth_setup(
    payload: CredentialsPayload,
    request: Request,
    response: Response,
    container: AppContainer = Depends(get_container),
) -> dict:
    try:
        token = container.auth.setup_admin(payload.username, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _set_session_cookie(response, request, token)
    return {"ok": True, "username": payload.username.strip(), "token": token}


@router.post("/login")
def auth_login(
    payload: CredentialsPayload,
    request: Request,
    response: Response,
    container: AppContainer = Depends(get_container),
) -> dict:
    client_ip = request.client.host if request.client else "unknown"
    try:
        token = container.auth.login(
            payload.username,
            payload.password,
            client_key=client_ip,
            totp_code_val=payload.totp_code,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _set_session_cookie(response, request, token)
    return {"ok": True, "username": payload.username.strip(), "token": token}


@router.post("/logout")
def auth_logout(
    request: Request,
    response: Response,
    container: AppContainer = Depends(get_container),
) -> dict:
    token = extract_request_session_token(request.headers, request.cookies)
    container.auth.logout(token)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


@router.post("/totp/setup")
def totp_setup(request: Request, container: AppContainer = Depends(get_container)) -> dict:
    st = container.auth.status(extract_request_session_token(request.headers, request.cookies))
    if not st.authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")
    return container.auth.begin_totp_setup()


@router.post("/totp/enable")
def totp_enable(
    payload: EnableTotpPayload,
    request: Request,
    container: AppContainer = Depends(get_container),
) -> dict:
    st = container.auth.status(extract_request_session_token(request.headers, request.cookies))
    if not st.authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        container.auth.enable_totp(payload.code, secret_override=payload.secret)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"ok": True, "totp_enabled": True}


@router.post("/totp/disable")
def totp_disable(request: Request, container: AppContainer = Depends(get_container)) -> dict:
    st = container.auth.status(extract_request_session_token(request.headers, request.cookies))
    if not st.authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")
    container.auth.disable_totp()
    return {"ok": True, "totp_enabled": False}
