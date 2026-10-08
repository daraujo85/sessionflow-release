"""Authentication endpoints: single-user password login plus WebAuthn
(biometric) registration and login.

All routes live under the ``/auth`` prefix, which the global auth middleware
exempts from the JWT requirement. The two register endpoints still require a
valid JWT (enforced here, in-handler) so that a passkey can only be enrolled
after a successful password login.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.auth import create_token, decode_token, extract_token

router = APIRouter(prefix="/auth", tags=["auth"])

# In-memory challenge store (handle -> (challenge_bytes, expires_at)). Fine for a
# single instance; challenges are one-shot and short-lived.
_CHALLENGE_TTL_SECONDS = 300
_challenges: dict[str, tuple[bytes, float]] = {}


def _store_challenge(handle: str, challenge: bytes) -> None:
    _challenges[handle] = (challenge, time.monotonic() + _CHALLENGE_TTL_SECONDS)


def _take_challenge(handle: str) -> bytes | None:
    """Pop a challenge if present and not expired."""
    entry = _challenges.pop(handle, None)
    if entry is None:
        return None
    challenge, expires_at = entry
    if time.monotonic() > expires_at:
        return None
    return challenge


def _require_jwt(request: Request) -> dict:
    settings = request.app.state.settings
    token = extract_token(request)
    claims = decode_token(token or "", secret=settings.jwt_secret)
    if claims is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return claims


def _webauthn_collection(request: Request):
    settings = request.app.state.settings
    return request.app.state.mongo_db[settings.webauthn_collection]


# --------------------------------------------------------------------------- #
# Password login
# --------------------------------------------------------------------------- #
class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    token: str
    expires_in: int
    email: str


def _issue_token(settings) -> TokenOut:
    token = create_token(
        settings.auth_email, secret=settings.jwt_secret, ttl_seconds=settings.jwt_ttl_seconds
    )
    return TokenOut(
        token=token, expires_in=settings.jwt_ttl_seconds, email=settings.auth_email
    )


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request) -> TokenOut:
    settings = request.app.state.settings
    email_ok = body.email.strip().lower() == settings.auth_email.strip().lower()
    password_ok = body.password == settings.auth_password
    if not (email_ok and password_ok):
        raise HTTPException(status_code=401, detail="invalid credentials")
    return _issue_token(settings)


# --------------------------------------------------------------------------- #
# WebAuthn — registration (requires a valid JWT)
# --------------------------------------------------------------------------- #
@router.post("/webauthn/register/options")
async def webauthn_register_options(request: Request) -> dict:
    _require_jwt(request)
    settings = request.app.state.settings
    collection = _webauthn_collection(request)

    existing = await collection.find({}).to_list(length=None)
    exclude = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(doc["credential_id"]))
        for doc in existing
    ]

    options = generate_registration_options(
        rp_id=settings.rp_id,
        rp_name=settings.rp_name,
        user_id=settings.auth_email.encode("utf-8"),
        user_name=settings.auth_email,
        user_display_name=settings.auth_email,
        exclude_credentials=exclude,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    _store_challenge("register", options.challenge)
    # options_to_json returns a JSON *string*; return as a parsed object so
    # FastAPI serializes a clean JSON body for the browser.

    return json.loads(options_to_json(options))


@router.post("/webauthn/register/verify")
async def webauthn_register_verify(request: Request) -> dict:
    _require_jwt(request)
    settings = request.app.state.settings
    collection = _webauthn_collection(request)

    credential = await request.json()
    challenge = _take_challenge("register")
    if challenge is None:
        raise HTTPException(status_code=400, detail="no pending challenge")

    verification = verify_registration_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=settings.rp_id,
        expected_origin=settings.rp_origin,
    )

    await collection.insert_one(
        {
            "credential_id": bytes_to_base64url(verification.credential_id),
            "public_key": bytes_to_base64url(verification.credential_public_key),
            "sign_count": verification.sign_count,
            "created_at": datetime.now(UTC),
        }
    )
    return {"ok": True}


# --------------------------------------------------------------------------- #
# WebAuthn — login (public)
# --------------------------------------------------------------------------- #
@router.get("/webauthn/available")
async def webauthn_available(request: Request) -> dict:
    collection = _webauthn_collection(request)
    count = await collection.count_documents({})
    return {"available": count > 0}


@router.post("/webauthn/login/options")
async def webauthn_login_options(request: Request) -> dict:
    settings = request.app.state.settings
    collection = _webauthn_collection(request)

    creds = await collection.find({}).to_list(length=None)
    if not creds:
        raise HTTPException(status_code=404, detail={"available": False})

    allow = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(doc["credential_id"]))
        for doc in creds
    ]
    options = generate_authentication_options(
        rp_id=settings.rp_id,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    _store_challenge("login", options.challenge)

    return json.loads(options_to_json(options))


@router.post("/webauthn/login/verify", response_model=TokenOut)
async def webauthn_login_verify(request: Request) -> TokenOut:
    settings = request.app.state.settings
    collection = _webauthn_collection(request)

    assertion = await request.json()
    challenge = _take_challenge("login")
    if challenge is None:
        raise HTTPException(status_code=400, detail="no pending challenge")

    cred_id = assertion.get("id") or assertion.get("rawId")
    doc = await collection.find_one({"credential_id": cred_id})
    if doc is None:
        raise HTTPException(status_code=404, detail="unknown credential")

    verification = verify_authentication_response(
        credential=assertion,
        expected_challenge=challenge,
        expected_rp_id=settings.rp_id,
        expected_origin=settings.rp_origin,
        credential_public_key=base64url_to_bytes(doc["public_key"]),
        credential_current_sign_count=doc["sign_count"],
    )

    await collection.update_one(
        {"credential_id": doc["credential_id"]},
        {"$set": {"sign_count": verification.new_sign_count}},
    )
    return _issue_token(settings)
