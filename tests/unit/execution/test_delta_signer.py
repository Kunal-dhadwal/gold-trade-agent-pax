"""Unit tests for Delta Request Signer module."""

import hashlib
import hmac
import pytest
from src.execution.delta.signer import DeltaRequestSigner
from src.execution.delta.types import DeltaAuthenticationError


def test_signer_initialization_and_empty_credentials():
    """Verify that signer rejects empty API key or secret with DeltaAuthenticationError."""
    with pytest.raises(DeltaAuthenticationError):
        DeltaRequestSigner(api_key="", api_secret="secret")
    with pytest.raises(DeltaAuthenticationError):
        DeltaRequestSigner(api_key="key", api_secret="")
    with pytest.raises(DeltaAuthenticationError):
        DeltaRequestSigner(api_key="", api_secret="")

    signer = DeltaRequestSigner(api_key="valid_key", api_secret="valid_secret")
    assert signer.api_key == "valid_key"


def test_signer_secret_redaction_in_repr():
    """Verify that signer does not leak api_secret in __repr__ or str."""
    signer = DeltaRequestSigner(api_key="1234567890abcdef", api_secret="super_secret_private_key")
    repr_str = repr(signer)
    assert "super_secret_private_key" not in repr_str
    assert "[REDACTED]" in repr_str
    assert "1234...cdef" in repr_str


def test_rest_signature_rfc_hmac_calculation():
    """Verify REST signature computation against explicit manual HMAC-SHA256 calculation."""
    api_key = "test_key_123"
    api_secret = "test_secret_456"
    signer = DeltaRequestSigner(api_key=api_key, api_secret=api_secret)

    method = "GET"
    timestamp_ms = 1728475200000
    path = "/v2/orders"
    query_or_body = "?product_id=1234"

    # Expected payload: "GET1728475200000/v2/orders?product_id=1234"
    expected_payload = f"{method}{timestamp_ms}{path}{query_or_body}"
    expected_signature = hmac.new(
        api_secret.encode("utf-8"),
        expected_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    sig = signer.build_rest_signature(
        method=method,
        timestamp_ms=timestamp_ms,
        path=path,
        query_or_body=query_or_body,
    )
    assert sig == expected_signature
    assert signer.verify_rest_signature(
        signature=sig,
        method=method,
        timestamp_ms=timestamp_ms,
        path=path,
        query_or_body=query_or_body,
    )


def test_rest_post_body_signature():
    """Verify REST POST body signature with JSON string payload."""
    api_key = "test_key_123"
    api_secret = "test_secret_456"
    signer = DeltaRequestSigner(api_key=api_key, api_secret=api_secret)

    method = "POST"
    timestamp_ms = 1728475205000
    path = "/v2/orders"
    body = '{"product_id":1234,"size":50,"side":"buy"}'

    expected_payload = f"{method}{timestamp_ms}{path}{body}"
    expected_sig = hmac.new(
        api_secret.encode("utf-8"),
        expected_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    sig = signer.build_rest_signature(
        method=method,
        timestamp_ms=timestamp_ms,
        path=path,
        query_or_body=body,
    )
    assert sig == expected_sig


def test_rest_headers_construction():
    """Verify generated HTTP headers dictionary."""
    api_key = "my_api_key_delta"
    api_secret = "my_api_secret_delta"
    signer = DeltaRequestSigner(api_key=api_key, api_secret=api_secret)

    ts = 1728475210000
    headers = signer.build_rest_headers(
        method="GET",
        path="/v2/wallet/balances",
        timestamp_ms=ts,
    )
    assert headers["api-key"] == api_key
    assert headers["timestamp"] == str(ts)
    assert "signature" in headers
    assert len(headers["signature"]) == 64  # SHA256 hex string length


def test_websocket_auth_payload_construction():
    """Verify WebSocket authentication frame and payload signature format."""
    api_key = "ws_test_key"
    api_secret = "ws_test_secret"
    signer = DeltaRequestSigner(api_key=api_key, api_secret=api_secret)

    ts = 1728475215000
    expected_payload_str = f"GET{ts}/live"
    expected_sig = hmac.new(
        api_secret.encode("utf-8"),
        expected_payload_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    auth_frame = signer.build_websocket_auth_payload(timestamp_ms=ts)
    assert auth_frame["type"] == "auth"
    payload = auth_frame["payload"]
    assert payload["api-key"] == api_key
    assert payload["timestamp"] == ts
    assert payload["signature"] == expected_sig

    assert signer.verify_websocket_auth_payload(auth_frame) is True


def test_invalid_signature_verification_rejection():
    """Verify that tampered signatures are rejected by constant-time verification."""
    signer = DeltaRequestSigner(api_key="key", api_secret="secret")
    ts = 1728475220000
    valid_sig = signer.build_rest_signature("GET", ts, "/v2/positions")

    tampered_sig = valid_sig[:-2] + "ff"
    assert signer.verify_rest_signature(tampered_sig, "GET", ts, "/v2/positions") is False

    # Timestamp mismatch
    assert signer.verify_rest_signature(valid_sig, "GET", ts + 100, "/v2/positions") is False

    # Path mismatch
    assert signer.verify_rest_signature(valid_sig, "GET", ts, "/v2/orders") is False

