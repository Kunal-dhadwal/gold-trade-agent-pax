"""Authenticated WebSocket client for Delta Exchange API v2."""

import asyncio
from datetime import datetime, timezone
import json
import random
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set
import websockets
from websockets.asyncio.client import ClientConnection
from src.core.logger import logger
from src.execution.delta.signer import DeltaRequestSigner
from src.execution.delta.types import (
    DeltaAuthenticationError,
    DeltaChannel,
    DeltaConfig,
    DeltaNetworkError,
)

EventHandler = Callable[[str, Dict[str, Any]], Coroutine[Any, Any, None]]


class DeltaWebSocketClient:
    """Authenticated Delta Exchange User WebSocket Feed Client.
    
    Guarantees:
    - Cryptographic authentication handshake within 5.0 seconds.
    - Automatic subscription to private channels: orders, positions, user_trades, margins.
    - Continuous 30s ping/pong heartbeat and 45s stale connection watchdog.
    - Jittered exponential backoff auto-reconnect circuit.
    - Zero plaintext credential leakage.
    """

    def __init__(
        self,
        config: Optional[DeltaConfig] = None,
        signer: Optional[DeltaRequestSigner] = None,
        symbols: Optional[List[str]] = None,
    ):
        self.config = config or DeltaConfig()
        if signer is not None:
            self.signer = signer
        elif self.config.api_key and self.config.api_secret:
            self.signer = DeltaRequestSigner(self.config.api_key, self.config.api_secret)
        else:
            self.signer = None

        self.symbols = symbols or ["PAXGUSDT"]
        self._handlers: Dict[str, List[EventHandler]] = {}
        self._ws: Optional[ClientConnection] = None
        self._running = False
        self._authenticated = False
        self._last_message_time = time.monotonic()
        self._last_pong_time = time.monotonic()
        self._listen_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 30.0

    @property
    def is_connected(self) -> bool:
        """Check if socket is currently open."""
        return self._ws is not None and self._ws.state.name == "OPEN"

    @property
    def is_authenticated(self) -> bool:
        """Check if connection has successfully authenticated."""
        return self.is_connected and self._authenticated

    def subscribe(self, channel: str, handler: EventHandler) -> None:
        """Register an asynchronous callback handler for a channel."""
        if channel not in self._handlers:
            self._handlers[channel] = []
        self._handlers[channel].append(handler)

    async def connect(self) -> None:
        """Establish connection and initiate background worker loops."""
        self._running = True
        await self._connect_socket()
        self._listen_task = asyncio.create_task(self._listen_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _connect_socket(self) -> None:
        """Connect to WebSocket URL and perform authentication handshake."""
        logger.info(f"Connecting to Delta WebSocket: {self.config.ws_url}")
        try:
            self._ws = await websockets.connect(
                self.config.ws_url,
                ping_interval=None,  # We manage application-level ping frames manually
                ping_timeout=None,
                close_timeout=5.0,
            )
            self._last_message_time = time.monotonic()
            self._last_pong_time = time.monotonic()
            self._authenticated = False

            # Transmit auth frame if signer is configured
            if self.signer is not None:
                await self._authenticate()
            else:
                logger.warning("No signer configured for DeltaWebSocketClient; running unauthenticated")
        except DeltaAuthenticationError:
            self._ws = None
            raise
        except Exception as exc:
            self._ws = None
            raise DeltaNetworkError(f"Failed to connect to {self.config.ws_url}: {exc}") from exc

    async def _authenticate(self) -> None:
        """Send authentication handshake frame and await acknowledgment."""
        if self._ws is None or self.signer is None:
            return

        auth_frame = self.signer.build_websocket_auth_payload()
        logger.info("Transmitting private WebSocket authentication frame")
        await self._ws.send(json.dumps(auth_frame))

        # Await auth confirmation frame within 5.0 seconds
        try:
            raw_response = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
            data = json.loads(raw_response)
            
            # Expected response: {"type": "auth", "status": "authenticated", ...}
            # or {"type": "auth", "success": true}
            is_success = (
                data.get("type") == "auth"
                and (data.get("status") == "authenticated" or data.get("success") is True)
            )

            if not is_success:
                error_msg = data.get("message") or data.get("error") or "Unknown auth failure"
                self._authenticated = False
                await self._ws.close()
                self._ws = None
                raise DeltaAuthenticationError(f"WebSocket authentication rejected: {error_msg}")

            self._authenticated = True
            logger.info("Delta WebSocket private channel authentication SUCCESSFUL")
            await self._subscribe_private_channels()
        except asyncio.TimeoutError as exc:
            self._authenticated = False
            await self._ws.close()
            self._ws = None
            raise DeltaAuthenticationError("Timed out waiting for WebSocket auth response (5.0s)") from exc

    async def _subscribe_private_channels(self) -> None:
        """Subscribe to authenticated user channels."""
        if self._ws is None or not self._authenticated:
            return

        channels: List[Dict[str, Any]] = [
            {"name": DeltaChannel.ORDERS.value, "symbols": self.symbols},
            {"name": DeltaChannel.POSITIONS.value, "symbols": self.symbols},
            {"name": DeltaChannel.USER_TRADES.value, "symbols": self.symbols},
            {"name": DeltaChannel.MARGINS.value},
        ]
        sub_frame = {"type": "subscribe", "payload": {"channels": channels}}
        logger.info(f"Subscribing to Delta private channels: {[c['name'] for c in channels]}")
        await self._ws.send(json.dumps(sub_frame))

    async def _listen_loop(self) -> None:
        """Message ingestion loop processing incoming frames."""
        while self._running:
            if self._ws is None or self._ws.state.name != "OPEN":
                await self._reconnect()
                continue

            try:
                raw_msg = await asyncio.wait_for(self._ws.recv(), timeout=1.0)
                self._last_message_time = time.monotonic()
                await self._handle_message(raw_msg)
            except asyncio.TimeoutError:
                # Normal read timeout, check stale connection watchdog
                now = time.monotonic()
                if (now - self._last_message_time) > self.config.connection_timeout_seconds:
                    logger.warning(
                        f"WebSocket connection stale (> {self.config.connection_timeout_seconds}s inactive). Forcing reconnect."
                    )
                    await self._reconnect()
            except websockets.ConnectionClosed as exc:
                logger.warning(f"WebSocket connection closed by server: {exc}. Reconnecting.")
                await self._reconnect()
            except Exception as exc:
                logger.error(f"Error in WebSocket listen loop: {exc}")
                await self._reconnect()

    async def _handle_message(self, raw_msg: str) -> None:
        """Parse frame and dispatch to registered callbacks."""
        try:
            data = json.loads(raw_msg)
        except Exception:
            return

        msg_type = data.get("type")
        # Handle heartbeat response
        if msg_type == "pong":
            self._last_pong_time = time.monotonic()
            return

        # Handle channel event push
        channel = data.get("channel") or data.get("name") or msg_type
        if channel and channel in self._handlers:
            for handler in self._handlers[channel]:
                try:
                    await handler(channel, data)
                except Exception as exc:
                    logger.error(f"Error executing callback for channel {channel}: {exc}")

        # Wildcard handlers
        if "*" in self._handlers:
            for handler in self._handlers["*"]:
                try:
                    await handler(channel or "unknown", data)
                except Exception as exc:
                    logger.error(f"Error executing wildcard callback: {exc}")

    async def _heartbeat_loop(self) -> None:
        """Send ping frame every heartbeat_interval_seconds."""
        while self._running:
            await asyncio.sleep(self.config.heartbeat_interval_seconds)
            if self._ws is not None and self._ws.state.name == "OPEN":
                try:
                    ping_frame = {"type": "ping"}
                    await self._ws.send(json.dumps(ping_frame))
                except Exception as exc:
                    logger.warning(f"Failed to transmit ping frame: {exc}")

    async def _reconnect(self) -> None:
        """Execute jittered exponential backoff reconnection."""
        if not self._running:
            return

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._authenticated = False

        jitter = random.uniform(0.1, 0.5)
        sleep_duration = self._reconnect_delay + jitter
        logger.info(f"Reconnecting WebSocket in {sleep_duration:.2f}s...")
        await asyncio.sleep(sleep_duration)

        # Exponential backoff update
        self._reconnect_delay = min(self._max_reconnect_delay, self._reconnect_delay * 2)

        try:
            await self._connect_socket()
            self._reconnect_delay = 1.0  # Reset backoff on success
            logger.info("WebSocket reconnected and re-authenticated successfully")
        except Exception as exc:
            logger.warning(f"Reconnection attempt failed: {exc}")

    async def close(self) -> None:
        """Gracefully disconnect WebSocket client."""
        self._running = False
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
        if self._listen_task is not None:
            self._listen_task.cancel()
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        self._authenticated = False
        logger.info("Delta WebSocket client disconnected cleanly")
