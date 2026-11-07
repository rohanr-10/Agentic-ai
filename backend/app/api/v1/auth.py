"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import DbDep, PrincipalDep, SettingsDep, rate_limit, request_meta
from app.schemas.auth import (
    LoginIn,
    LogoutIn,
    PasswordChangeIn,
    PasswordResetConfirmIn,
    PasswordResetRequestIn,
    RefreshIn,
    RegisterIn,
    TokenPair,
    UserOut,
    UserUpdateIn,
    validate_password_strength,
)
from app.services.auth import AuthService, validate_registration_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _tokens(user, issued) -> TokenPair:
    return TokenPair(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=issued.expires_in,
        user=UserOut.model_validate(user),
    )


@router.post(
    "/register",
    response_model=TokenPair,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("auth-register"))],
    summary="Register a new user account",
)
async def register(payload: RegisterIn, request: Request, db: DbDep, settings: SettingsDep):
    validate_registration_password(payload.password, settings)
    service = AuthService(db, settings)
    user, issued = await service.register(
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
        meta=request_meta(request),
    )
    return _tokens(user, issued)


@router.post(
    "/login",
    response_model=TokenPair,
    dependencies=[Depends(rate_limit("auth-login"))],
    summary="Login with email and password",
)
async def login(payload: LoginIn, request: Request, db: DbDep, settings: SettingsDep):
    service = AuthService(db, settings)
    user, issued = await service.login(
        email=payload.email,
        password=payload.password,
        meta=request_meta(request),
    )
    return _tokens(user, issued)


@router.post(
    "/refresh",
    response_model=TokenPair,
    dependencies=[Depends(rate_limit("auth-refresh"))],
    summary="Rotate refresh token and obtain new access token",
)
async def refresh(payload: RefreshIn, request: Request, db: DbDep, settings: SettingsDep):
    service = AuthService(db, settings)
    user, issued = await service.refresh(
        refresh_token=payload.refresh_token, meta=request_meta(request)
    )
    return _tokens(user, issued)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the current session (logout)",
)
async def logout(
    request: Request,
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
    payload: LogoutIn | None = None,
):
    service = AuthService(db, settings)
    await service.logout(
        session_id=principal.session.id,
        user_id=principal.user.id,
        meta=request_meta(request),
    )


@router.get("/me", response_model=UserOut, summary="Current user profile")
async def me(principal: PrincipalDep):
    return UserOut.model_validate(principal.user)


@router.patch("/me", response_model=UserOut, summary="Update current user profile")
async def update_me(
    payload: UserUpdateIn,
    request: Request,
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
):
    service = AuthService(db, settings)
    user = await service.update_profile(
        user=principal.user, display_name=payload.display_name, meta=request_meta(request)
    )
    return UserOut.model_validate(user)


@router.post(
    "/password-reset",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("auth-reset"))],
    summary="Request a password reset (never reveals account existence)",
)
async def request_password_reset(
    payload: PasswordResetRequestIn, request: Request, db: DbDep, settings: SettingsDep
):
    service = AuthService(db, settings)
    await service.request_password_reset(email=payload.email, meta=request_meta(request))
    return {"status": "accepted"}


@router.post(
    "/password-reset/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit("auth-reset"))],
    summary="Confirm a password reset with the emailed token",
)
async def confirm_password_reset(
    payload: PasswordResetConfirmIn, request: Request, db: DbDep, settings: SettingsDep
):
    validate_registration_password(payload.new_password, settings)
    service = AuthService(db, settings)
    await service.confirm_password_reset(
        token=payload.token, new_password=payload.new_password, meta=request_meta(request)
    )


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change password (revokes other sessions)",
)
async def change_password(
    payload: PasswordChangeIn,
    request: Request,
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
):
    try:
        validate_password_strength(
            payload.new_password, min_length=settings.password_min_length
        )
    except ValueError as exc:
        from app.core.errors import RequestValidationError

        raise RequestValidationError(str(exc)) from exc
    service = AuthService(db, settings)
    await service.change_password(
        user=principal.user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        keep_session_id=principal.session.id,
        meta=request_meta(request),
    )
