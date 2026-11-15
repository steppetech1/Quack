"""Routes of login and consents (ТЗ Ф4 WP18, §6.4 «Вход»; Т-§19.1, Т-§19.4, Т-§14.6).

Public: `POST /auth/link`, `GET` and `POST /auth/link/verify`, `GET /auth/google/start`,
`GET /auth/google/callback`. With a session: `POST /auth/logout`, `GET /v1/me`,
`PATCH /v1/learner/settings`, `POST /v1/consents`, `DELETE /v1/consents/research`.
"""

from __future__ import annotations

import html
import secrets
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from quack.api.auth import SESSION_COOKIE, CurrentSession, get_current_session
from quack.api.errors import ApiError, error_response
from quack.api.resources import AppResources, get_resources
from quack.auth.consents import give_consent, has_current_terms, revoke_research
from quack.auth.google import (
    EmailNotVerified,
    GoogleOIDC,
    OidcFailed,
    code_challenge,
    learner_for_google,
    new_code_verifier,
    new_nonce,
)
from quack.auth.links import (
    AuthRateLimited,
    LinkInvalid,
    LinkLimiter,
    new_token,
    redeem_link,
    request_link,
)
from quack.auth.pairing import CanvasNotPairable, issue_pairing_token, redeem_pairing_token
from quack.auth.sessions import cookie_params, open_session, revoke_session

# D-C-115: the state of the Google flow lives in a short cookie of its own. The cookie holds
# `state.nonce.code_verifier` (the alphabet of `secrets.token_urlsafe` has no dot).
OIDC_STATE_COOKIE = "quack_oidc_state"
_OIDC_COOKIE_PARTS = 3

# The page of `GET /auth/link/verify`: no external resources, one button, the token in a hidden
# field. The same rules go in `<meta>` too — a proxy of the frontend passes the body, not headers.
_CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
_VERIFY_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<meta name="robots" content="noindex">
<title>Sign in</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;font:16px system-ui}}
main{{text-align:center;padding:24px}}
button{{font:inherit;padding:12px 32px;border-radius:8px;border:0;background:#111;color:#fff}}
</style>
</head>
<body>
<main>
<h1>Sign in to Quack</h1>
<form method="post" action="verify">
<input type="hidden" name="token" value="{token}">
<button type="submit">Sign in</button>
</form>
</main>
</body>
</html>
"""

router = APIRouter(tags=["auth"])

Resources = Annotated[AppResources, Depends(get_resources)]
Session = Annotated[CurrentSession, Depends(get_current_session)]


class LinkRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")


class LearnerSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")  # a `learner_id` in the body changes nothing

    timezone: str

    @field_validator("timezone")
    @classmethod
    def _iana(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("not an IANA time zone") from exc
        return value


class ConsentRequest(BaseModel):
    kind: Literal["terms", "research"]
    version: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)


class ConsentRevocation(BaseModel):
    version: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)


class LinkSent(BaseModel):
    """The same answer for a known and a new address (WP18.1)."""

    status: Literal["sent"] = "sent"


class SignedIn(BaseModel):
    """Without `auth_success_redirect_url` (D-C-105) sign-in answers 200 with the cookie."""

    learner_id: UUID


class PairingIssued(BaseModel):
    """WP22.1: the frontend builds the QR with a link carrying `token` (Т-§16.1)."""

    token: str
    expires_at: datetime


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1)


class Paired(BaseModel):
    canvas_id: UUID


class SignedOut(BaseModel):
    status: Literal["signed_out"] = "signed_out"


class LearnerSettings(BaseModel):
    settings: dict[str, Any]


class ConsentRecorded(BaseModel):
    kind: Literal["terms", "research"]
    version: str
    given: bool


class ClientParams(BaseModel):
    """Numbers of the client from the server config (ТЗ Ф4 §6.7)."""

    stroke_batch_ms: int
    line_pause_ms: int
    capture_still_ms: int


class Me(BaseModel):
    learner_id: UUID
    email: str
    settings: dict[str, Any]
    terms_version: str | None
    terms_accepted: bool
    client: ClientParams


def _now() -> datetime:
    return datetime.now(UTC)


def _limiter(request: Request, resources: AppResources) -> LinkLimiter:
    """One limiter per app: its local counters live as long as the process (Д-§10)."""
    limiter: LinkLimiter | None = getattr(request.app.state, "link_limiter", None)
    if limiter is None:
        limiter = request.app.state.link_limiter = LinkLimiter(resources.redis, resources.settings)
    return limiter


def _oidc(request: Request) -> GoogleOIDC:
    return request.app.state.google_oidc


def _signed_in(resources: AppResources, token: str, learner_id: UUID) -> Response:
    """The session cookie and the way back to the app (`auth_success_redirect_url`, D-C-105:
    without it — 200 `SignedIn` with the cookie)."""
    target = resources.settings.auth_success_redirect_url
    response: Response = (
        RedirectResponse(target, status_code=303)
        if target
        else JSONResponse(SignedIn(learner_id=learner_id).model_dump(mode="json"))
    )
    params = cookie_params(resources.settings)
    response.set_cookie(value=token, **params)
    return response


def _unauthenticated(request: Request, message: str) -> JSONResponse:
    return error_response(request, "UNAUTHENTICATED", message, 401)


@router.post("/auth/link", response_model=LinkSent)
async def post_link(body: LinkRequest, request: Request, resources: Resources) -> Response:
    """A one-time link by mail; the same answer for a known and a new address."""
    limiter = _limiter(request, resources)
    ip = request.client.host if request.client else "unknown"
    try:
        await request_link(
            resources.db,
            resources.email,
            limiter,
            resources.settings,
            email=body.email,
            ip=ip,
            verify_url=str(request.url_for("verify_link")),
            now=_now(),
        )
    except AuthRateLimited as exc:
        response = error_response(request, "RATE_LIMITED", str(exc), 429)
        response.headers["Retry-After"] = str(exc.retry_after_s)
        return response
    return JSONResponse(LinkSent().model_dump())


@router.get(
    "/auth/link/verify",
    name="verify_link",
    response_class=HTMLResponse,
    response_model=None,
)
async def verify_link_page(token: str) -> Response:
    """The page of the link: a form with one button. It redeems nothing and writes nothing —
    mail scanners open links before the learner does, and a GET that signs in would spend the
    token on them. The token is spent by the POST of the form."""
    return HTMLResponse(
        _VERIFY_PAGE.format(csp=_CSP, token=html.escape(token, quote=True)),
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": _CSP,
        },
    )


@router.post(
    "/auth/link/verify",
    name="verify_link_submit",
    response_model=SignedIn,
    responses={303: {"description": "Signed in: back to the app"}},
)
async def verify_link(
    request: Request, resources: Resources, token: Annotated[str, Form()]
) -> Response:
    """Redeems the token once (`application/x-www-form-urlencoded`, field `token`) and signs in."""
    try:
        learner_id = await redeem_link(resources.db, token=token, now=_now())
    except LinkInvalid:
        return _unauthenticated(request, "login link is invalid or expired")
    session = await open_session(
        resources.db, resources.settings, learner_id=learner_id, now=_now()
    )
    return _signed_in(resources, session, learner_id)


@router.get(
    "/auth/google/start",
    response_model=None,
    status_code=302,
    responses={302: {"description": "To Google"}},
)
async def google_start(request: Request, resources: Resources) -> Response:
    state, nonce, verifier = new_token(), new_nonce(), new_code_verifier()
    url = _oidc(request).authorize_url(
        state=state, nonce=nonce, code_challenge=code_challenge(verifier)
    )
    response = RedirectResponse(url, status_code=302)
    params = cookie_params(resources.settings)
    params.update(key=OIDC_STATE_COOKIE, max_age=resources.settings.auth_link_ttl_min * 60)
    response.set_cookie(value=f"{state}.{nonce}.{verifier}", **params)
    return response


@router.get(
    "/auth/google/callback",
    response_model=SignedIn,
    responses={303: {"description": "Signed in: back to the app"}},
)
async def google_callback(
    request: Request,
    resources: Resources,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """Google answers `?code&state`, or `?error=access_denied&state` when the learner refuses:
    without `code` that is the same 401 as any other failed sign-in, not a 422 of the framework."""
    parts = request.cookies.get(OIDC_STATE_COOKIE, "").split(".")
    if (
        len(parts) != _OIDC_COOKIE_PARTS
        or not all(parts)
        or not state
        or not secrets.compare_digest(parts[0].encode(), state.encode())
    ):
        return _unauthenticated(request, "sign-in with Google was not started here")
    _, nonce, verifier = parts
    if error is not None or not code:
        response = _unauthenticated(request, "sign-in with Google was not completed")
        response.delete_cookie(OIDC_STATE_COOKIE, domain=resources.settings.session_cookie_domain)
        return response
    try:
        identity = await _oidc(request).identity(code=code, nonce=nonce, code_verifier=verifier)
        learner_id = await learner_for_google(resources.db, identity)
    except EmailNotVerified:
        return _unauthenticated(request, "Google has not verified this address")
    except OidcFailed:
        return _unauthenticated(request, "sign-in with Google failed")
    session = await open_session(
        resources.db, resources.settings, learner_id=learner_id, now=_now()
    )
    response = _signed_in(resources, session, learner_id)
    response.delete_cookie(OIDC_STATE_COOKIE, domain=resources.settings.session_cookie_domain)
    return response


@router.post("/auth/logout", response_model=SignedOut)
async def logout(request: Request, session: Session, resources: Resources) -> Response:
    token = request.cookies.get(SESSION_COOKIE, "")
    await revoke_session(resources.db, learner_id=session.learner_id, token=token, now=_now())
    response = JSONResponse(SignedOut().model_dump())
    response.delete_cookie(SESSION_COOKIE, domain=resources.settings.session_cookie_domain)
    return response


_LEARNER = "SELECT email, settings FROM core.learners WHERE learner_id = %(learner_id)s"
_SET_TIMEZONE = (
    "UPDATE core.learners SET settings = settings || jsonb_build_object('timezone', "
    "%(timezone)s::text) WHERE learner_id = %(learner_id)s"
)


@router.get("/v1/me", response_model=Me)
async def me(session: Session, resources: Resources) -> Me:
    row = await resources.db.fetch_one(_LEARNER, {"learner_id": session.learner_id})
    if row is None:
        raise ApiError("UNAUTHENTICATED", "learner not found", status_code=401)
    settings = resources.settings
    return Me(
        learner_id=session.learner_id,
        email=row["email"],
        settings=row["settings"],
        terms_version=settings.terms_version,
        terms_accepted=await has_current_terms(resources.db, session.learner_id, settings),
        client=ClientParams(
            stroke_batch_ms=settings.stroke_batch_ms,
            line_pause_ms=settings.line_pause_ms,
            capture_still_ms=settings.capture_still_ms,
        ),
    )


@router.patch("/v1/learner/settings", response_model=LearnerSettings)
async def patch_settings(
    body: LearnerSettingsPatch, session: Session, resources: Resources
) -> LearnerSettings:
    await resources.db.execute(
        _SET_TIMEZONE, {"timezone": body.timezone, "learner_id": session.learner_id}
    )
    row = await resources.db.fetch_one(_LEARNER, {"learner_id": session.learner_id})
    return LearnerSettings(settings={} if row is None else row["settings"])


@router.post("/v1/consents", response_model=ConsentRecorded)
async def post_consent(
    body: ConsentRequest, session: Session, resources: Resources
) -> ConsentRecorded:
    await give_consent(
        resources.db,
        session.learner_id,
        kind=body.kind,
        version=body.version,
        idempotency_key=body.idempotency_key,
        redis=resources.redis,
    )
    return ConsentRecorded(kind=body.kind, version=body.version, given=True)


@router.delete("/v1/consents/research", response_model=ConsentRecorded)
async def delete_research(
    body: ConsentRevocation, session: Session, resources: Resources
) -> ConsentRecorded:
    await revoke_research(
        resources.db,
        session.learner_id,
        version=body.version,
        idempotency_key=body.idempotency_key,
        redis=resources.redis,
    )
    return ConsentRecorded(kind="research", version=body.version, given=False)


@router.post("/v1/canvases/{canvas_id}/pairing", status_code=201, response_model=PairingIssued)
async def pairing(canvas_id: UUID, session: Session, resources: Resources) -> PairingIssued:
    """WP22.1: a one-time pairing token of the learner's canvas (Т-§16.1)."""
    try:
        issued = await issue_pairing_token(
            resources.db,
            resources.settings,
            learner_id=session.learner_id,
            canvas_id=canvas_id,
            now=_now(),
        )
    except CanvasNotPairable as exc:
        raise ApiError("NOT_FOUND", str(exc), status_code=404) from exc
    return PairingIssued(token=issued.token, expires_at=issued.expires_at)


@router.post("/companion/pair", response_model=Paired)
async def companion_pair(body: PairRequest, request: Request, resources: Resources) -> Response:
    """WP22.1: the phone exchanges the token for a `companion` session of the canvas (cookie);
    an unknown, used or expired token — 401."""
    paired = await redeem_pairing_token(
        resources.db, resources.settings, token=body.token, now=_now()
    )
    if paired is None:
        return _unauthenticated(request, "pairing token is invalid, used or expired")
    response = JSONResponse(Paired(canvas_id=paired.canvas_id).model_dump(mode="json"))
    response.set_cookie(value=paired.token, **cookie_params(resources.settings))
    return response
