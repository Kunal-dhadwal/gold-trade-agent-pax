"""Isolated HMAC-SHA256 request signer for Delta Exchange API v2."""

import hashlib
import hmac
import time
from typing import Any, Dict, Optional
from src.execution.delta.types import DeltaAuthenticationError


class DeltaRequestSigner:
    """Computes and validates RFC 2104 HMAC-SHA256 signatures for Delta Exchange.
    
    Guarantees strict secret isolation:
    - Zero plaintext logging of API secrets.
    - Constant-time signature comparisons to prevent timing attacks.
    - Millisecond precision timestamp nonce generation.
    """

    def __init__(self, api_key: str, api_secret: str):
        if not api_key or not api_secret:
            raise DeltaAuthenticationError("Both api_key and api_secret must be non-empty strings")
        self._api_key = api_key.strip()
        self._api_secret = api_secret.strip()

    @property
    def api_key(self) -> str:
        """Public API key accessor."""
        return self._api_key

    def __repr__(self) -> str:
        """Prevent sensitive secret leakage in logs or stack traces."""
        masked_key = f"{self._api_key[:4]}...{self._api_key[-4:]}" if len(self._api_key) > 8 else "***"
        return f"DeltaRequestSigner(api_key='{masked_key}', api_secret='[REDACTED]')"

    @staticmethod
    def get_current_timestamp_ms() -> int:
        """Generate millisecond timestamp for request nonce."""
        return int(time.time() * 1000)

    def sign_payload(self, payload_string: str) -> str:
        """Compute HMAC-SHA256 hex digest for arbitrary message payload."""
        return hmac.new(
            self._api_secret.encode("utf-8"),
            payload_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def build_rest_signature(
        self,
        method: str,
        timestamp_ms: int,
        path: str,
        query_or_body: str = "",
    ) -> str:
        """Compute Delta REST API v2 request signature.
        
        Message Construction:
        Payload = HTTP_METHOD + TIMESTAMP_MS + REQUEST_PATH + QUERY_OR_BODY
        """
        method_upper = method.upper()
        payload = f"{method_upper}{timestamp_ms}{path}{query_or_body}"
        return self.sign_payload(payload)

    def build_rest_headers(
        self,
        method: str,
        path: str,
        query_or_body: str = "",
        timestamp_ms: Optional[int] = None,
    ) -> Dict[str, str]:
        """Generate authenticated HTTP headers for Delta Exchange REST dispatch."""
        ts = timestamp_ms if timestamp_ms is not None else self.get_current_timestamp_ms()
        signature = self.build_rest_signature(
            method=method,
            timestamp_ms=ts,
            path=path,
            query_or_body=query_or_body,
        )
        return {
            "api-key": self._api_key,
            "timestamp": str(ts),
            "signature": signature,
            "User-Agent": "AGentTrXPAUS-Quant/1.0",
        }

    def verify_rest_signature(
        self,
        signature: str,
        method: str,
        timestamp_ms: int,
        path: str,
        query_or_body: str = "",
    ) -> bool:
        """Verify REST signature using constant-time comparison."""
        expected = self.build_rest_signature(
            method=method,
            timestamp_ms=timestamp_ms,
            path=path,
            query_or_body=query_or_body,
        )
        return hmac.compare_digest(expected, signature)

    def build_websocket_auth_payload(
        self,
        timestamp_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Construct authenticated WebSocket JSON auth frame.
        
        Auth Signature Payload:
        Payload = "GET" + TIMESTAMP_MS + "/live"
        """
        ts = timestamp_ms if timestamp_ms is not None else self.get_current_timestamp_ms()
        payload_string = f"GET{ts}/live"
        signature = self.sign_payload(payload_string)
        return {
            "type": "auth",
            "payload": {
                "api-key": self._api_key,
                "timestamp": ts,
                "signature": signature,
            },
        }

    def verify_websocket_auth_payload(
        self,
        auth_frame: Dict[str, Any],
    ) -> bool:
        """Verify an incoming WebSocket auth payload for mock or testing."""
        if auth_frame.get("type") != "auth":
            return False
        payload = auth_frame.get("payload", {})
        key = payload.get("api-key")
        ts = payload.get("timestamp")
        sig = payload.get("signature")
        if not (key and ts and sig):
            return False
        if not hmac.compare_digest(self._api_key, key):
            return False
        expected_sig = self.sign_payload(f"GET{ts}/live")
        return hmac.compare_digest(expected_sig, sig)

