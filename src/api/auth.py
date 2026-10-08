"""Authentication and Role-Based Access Control (RBAC) for Phase 3 API Gateway.

RFC 7519 HMAC-SHA256 JWT tokens.
Strictly enforces:
- VIEWER, OPERATOR, ADMIN roles.
- AI models possess ZERO user accounts, zero credentials, and zero execution authority.
"""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.core.config import settings

security = HTTPBearer(auto_error=False)

ROLE_HIERARCHY = {
    "VIEWER": 1,
    "OPERATOR": 2,
    "ADMIN": 3,
}


def _b64url_encode(data: bytes) -> str:
    """Encode bytes to base64url string without trailing padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """Decode base64url string with optional padding."""
    pad = len(s) % 4
    if pad:
        s += "=" * (4 - pad)
    return base64.urlsafe_b64decode(s)


def create_access_token(
    subject: str,
    role: str = "VIEWER",
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate RFC 7519 compliant HMAC-SHA256 JWT token."""
    role_upper = role.upper()
    if role_upper not in ROLE_HIERARCHY:
        raise ValueError(f"Invalid role: {role}")

    # Explicit ban on AI identities
    if subject.lower().startswith(("ai_", "bot_", "agent_", "llm_")):
        raise PermissionError("AI agents cannot be issued authentication tokens.")

    header = {"alg": settings.JWT_ALGORITHM, "typ": "JWT"}
    now = int(time.time())
    if expires_delta:
        exp = now + int(expires_delta.total_seconds())
    else:
        exp = now + (settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60)

    payload: Dict[str, Any] = {
        "sub": subject,
        "role": role_upper,
        "iat": now,
        "exp": exp,
    }
    if extra_claims:
        payload.update(extra_claims)

    secret = settings.JWT_SECRET_KEY.encode("utf-8")
    h_enc = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p_enc = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    msg = f"{h_enc}.{p_enc}".encode("utf-8")
    sig = _b64url_encode(hmac.new(secret, msg, hashlib.sha256).digest())

    return f"{h_enc}.{p_enc}.{sig}"


def verify_token(token: str) -> Dict[str, Any]:
    """Verify signature and expiration of JWT token."""
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed JWT token format.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    h_enc, p_enc, sig_received = parts
    secret = settings.JWT_SECRET_KEY.encode("utf-8")
    expected_sig = _b64url_encode(hmac.new(secret, f"{h_enc}.{p_enc}".encode("utf-8"), hashlib.sha256).digest())

    if not hmac.compare_digest(sig_received, expected_sig):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid JWT signature.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload_bytes = _b64url_decode(p_enc)
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Failed to parse JWT payload.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check expiration
    exp = payload.get("exp")
    if exp and int(time.time()) > exp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    sub = str(payload.get("sub", ""))
    if sub.lower().startswith(("ai_", "bot_", "agent_", "llm_")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SECURITY VETO: AI models possess zero platform authority.",
        )

    return payload


async def get_current_user(
    auth: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> Dict[str, Any]:
    """Extract and validate user claims from Authorization header."""
    if not auth or not auth.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization credentials required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return verify_token(auth.credentials)


def require_role(min_role: str):
    """Dependency enforcing minimum RBAC level (VIEWER <= OPERATOR <= ADMIN)."""
    min_role_upper = min_role.upper()
    min_level = ROLE_HIERARCHY.get(min_role_upper, 999)

    async def role_checker(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
        user_role = user.get("role", "VIEWER").upper()
        user_level = ROLE_HIERARCHY.get(user_role, 0)

        if user_level < min_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions: requires {min_role_upper} role (current: {user_role}).",
            )
        return user

    return role_checker

