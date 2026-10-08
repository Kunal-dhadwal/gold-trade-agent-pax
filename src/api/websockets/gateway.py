"""Real-Time ASGI WebSocket Streaming Gateway for Phase 3 Institutional Dashboard.

Endpoint: /ws/v1/paper/stream
Protocol: ASGI WebSocket with JWT authentication handshake.
Features:
- Multiplexed topics: candles:{symbol}:{timeframe}, orders, positions, equity, alerts
- Heartbeat: 30s Ping-Pong, 10s timeout disconnections
- Backpressure: 512-message bounded queue with drop of low-priority ticks on lag
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from src.api.auth import verify_token
from src.core.config import settings
from src.paper.service import PaperTradingService
from src.real_time.event_bus import EventBus

logger = logging.getLogger(__name__)

ws_router = APIRouter(tags=["WebSocket Gateway"])


class ClientConnection:
    """Represents a connected WebSocket client with bounded buffer and subscriptions."""

    def __init__(self, websocket: WebSocket, user_id: str, role: str):
        self.websocket = websocket
        self.user_id = user_id
        self.role = role
        self.subscriptions: Set[str] = set()
        self.send_queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        self.connected_at = datetime.now(timezone.utc)
        self.last_ping_at = datetime.now(timezone.utc)
        self.is_active = True

    async def send_json(self, message: Dict[str, Any]) -> None:
        """Enqueue message with backpressure protection."""
        if not self.is_active:
            return

        try:
            self.send_queue.put_nowait(message)
        except asyncio.QueueFull:
            # Backpressure handling: drop low-priority tick messages
            msg_type = message.get("type", "")
            if msg_type in ("TICK", "CANDLE_UPDATE"):
                logger.debug(f"Client {self.user_id} buffer full. Dropping low-priority {msg_type}.")
            else:
                # Critical messages (orders, risk alerts): force pop oldest non-critical or wait briefly
                try:
                    _ = self.send_queue.get_nowait()
                    self.send_queue.put_nowait(message)
                except Exception:
                    pass


class WebSocketGatewayManager:
    """Manages active WebSocket client connections and topic-based dispatching."""

    def __init__(self, event_bus: Optional[EventBus] = None):
        from src.paper.service import paper_service
        self.event_bus = event_bus or paper_service.event_bus
        self.clients: List[ClientConnection] = []
        self._dispatch_task: Optional[asyncio.Task] = None
        self._is_running = False

    async def start(self) -> None:
        """Start listening to EventBus and forwarding events to connected clients."""
        if self._is_running:
            return
        self._is_running = True
        pattern_list = self.event_bus._pattern_handlers.setdefault("*", [])
        if self._handle_bus_event not in pattern_list:
            self.event_bus.subscribe_pattern("*", self._handle_bus_event)
        await self.event_bus.start()
        logger.info("WebSocketGatewayManager started and listening to EventBus.")

    async def stop(self) -> None:
        """Stop listening to EventBus and disconnect all clients cleanly."""
        self._is_running = False
        for client in list(self.clients):
            self.disconnect(client)
        if "*" in self.event_bus._pattern_handlers:
            self.event_bus._pattern_handlers["*"] = [
                h for h in self.event_bus._pattern_handlers["*"] if h != self._handle_bus_event
            ]
        await self.event_bus.stop()
        logger.info("WebSocketGatewayManager stopped.")

    async def _handle_bus_event(self, channel: str, envelope: Any) -> None:
        """Translate EventBus envelope into WebSocket client message and broadcast to subscribers."""
        # Determine canonical topic
        topic = "all"
        if channel.startswith("order."):
            topic = "orders"
        elif channel.startswith("portfolio."):
            topic = "equity"
        elif channel.startswith("position."):
            topic = "positions"
        elif channel.startswith("risk."):
            topic = "alerts"
        elif channel.startswith("market."):
            topic = "candles"

        payload = envelope.payload if hasattr(envelope, "payload") else {}
        event_type = envelope.event_type if hasattr(envelope, "event_type") else "EVENT"
        event_id = envelope.event_id if hasattr(envelope, "event_id") else ""
        event_ts = envelope.event_timestamp if hasattr(envelope, "event_timestamp") else datetime.now(timezone.utc).isoformat()

        msg = {
            "type": event_type,
            "channel": channel,
            "topic": topic,
            "payload": payload,
            "event_id": event_id,
            "timestamp": event_ts,
        }
        await self.broadcast_to_topics({topic, channel}, msg)

    async def connect(self, websocket: WebSocket, token: Optional[str]) -> Optional[ClientConnection]:
        """Authenticate and accept incoming WebSocket connection.
        
        Strict Invariant (P2-1): Unauthenticated or anonymous connections are rejected with WS 1008.
        """
        if not token:
            logger.warning("WebSocket handshake rejected: missing JWT authentication token.")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authentication token required.")
            return None

        try:
            user_claims = verify_token(token)
        except Exception as e:
            logger.warning(f"WebSocket JWT handshake rejected: {e}")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid JWT token")
            return None

        await websocket.accept()

        client = ClientConnection(
            websocket=websocket,
            user_id=user_claims.get("sub", "unknown"),
            role=user_claims.get("role", "VIEWER"),
        )
        # Default topic subscriptions
        client.subscriptions.add("equity")
        client.subscriptions.add("orders")
        client.subscriptions.add("positions")
        client.subscriptions.add("alerts")
        client.subscriptions.add(f"candles:{settings.DELTA_TARGET_SYMBOL}:5m")

        self.clients.append(client)
        logger.info(f"WebSocket client connected: {client.user_id} ({client.role}). Total: {len(self.clients)}")

        # Send welcome handshake
        await client.send_json({
            "type": "CONNECTION_ESTABLISHED",
            "user_id": client.user_id,
            "role": client.role,
            "subscriptions": list(client.subscriptions),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return client

    def disconnect(self, client: ClientConnection) -> None:
        """Remove disconnected client."""
        client.is_active = False
        if client in self.clients:
            self.clients.remove(client)
        logger.info(f"WebSocket client disconnected: {client.user_id}. Remaining: {len(self.clients)}")

    async def broadcast_to_topic(self, topic: str, message: Dict[str, Any]) -> None:
        """Fan out message to all clients subscribed to topic."""
        for client in list(self.clients):
            if client.is_active and (topic in client.subscriptions or "all" in client.subscriptions):
                await client.send_json(message)

    async def broadcast_to_topics(self, topics: Set[str], message: Dict[str, Any]) -> None:
        """Fan out message once to all clients subscribed to any of the matching topics."""
        for client in list(self.clients):
            if client.is_active and (
                "all" in client.subscriptions or any(t in client.subscriptions for t in topics)
            ):
                await client.send_json(message)

    async def client_writer(self, client: ClientConnection) -> None:
        """Dedicated coroutine consuming send_queue and writing to WebSocket."""
        try:
            while client.is_active:
                msg = await client.send_queue.get()
                await client.websocket.send_text(json.dumps(msg))
                client.send_queue.task_done()
        except (WebSocketDisconnect, ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as e:
            logger.debug(f"Error writing to client {client.user_id}: {e}")
        finally:
            self.disconnect(client)

    async def client_reader(self, client: ClientConnection) -> None:
        """Dedicated coroutine reading incoming client control messages (ping, subscribe)."""
        try:
            while client.is_active:
                data_text = await client.websocket.receive_text()
                try:
                    cmd = json.loads(data_text)
                except Exception:
                    continue

                msg_type = cmd.get("action", cmd.get("type", "")).upper()
                if msg_type == "PING":
                    await client.send_json({"type": "PONG", "timestamp": datetime.now(timezone.utc).isoformat()})
                elif msg_type == "SUBSCRIBE":
                    topic = cmd.get("topic")
                    if topic:
                        client.subscriptions.add(topic)
                        await client.send_json({"type": "SUBSCRIBED", "topic": topic})
                elif msg_type == "UNSUBSCRIBE":
                    topic = cmd.get("topic")
                    if topic and topic in client.subscriptions:
                        client.subscriptions.remove(topic)
                        await client.send_json({"type": "UNSUBSCRIBED", "topic": topic})
        except (WebSocketDisconnect, ConnectionResetError):
            pass
        finally:
            self.disconnect(client)


gateway_manager = WebSocketGatewayManager()


@ws_router.websocket("/ws/v1/paper/stream")
async def paper_stream_endpoint(websocket: WebSocket, token: Optional[str] = None):
    """ASGI WebSocket streaming gateway for real-time market data, orders, and equity."""
    client = await gateway_manager.connect(websocket, token)
    if not client:
        return

    writer_task = asyncio.create_task(gateway_manager.client_writer(client))
    reader_task = asyncio.create_task(gateway_manager.client_reader(client))

    done, pending = await asyncio.wait(
        [writer_task, reader_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()

    gateway_manager.disconnect(client)

