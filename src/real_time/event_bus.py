"""Event bus and standardized event envelope for Phase 3 real-time architecture."""

import asyncio
from datetime import datetime, timezone
import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class EventEnvelope(BaseModel):
    """Standardized event envelope enforcing strict identity and idempotency."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    aggregate_id: str
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    causation_id: Optional[str] = None
    idempotency_key: str
    producer: str = "paper_engine"
    event_timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    sequence_number: int = 1
    schema_version: str = "1.0"
    payload: Dict[str, Any] = Field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize envelope to JSON string."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str) -> "EventEnvelope":
        """Deserialize envelope from JSON string."""
        return cls.model_validate_json(data)


class EventBus:
    """Pub/Sub event bus supporting Redis 7 with in-memory dispatch and fallback."""

    def __init__(self, redis_client: Optional[Any] = None):
        self.redis = redis_client
        self._handlers: Dict[str, List[Callable[[EventEnvelope], Awaitable[None]]]] = {}
        self._pattern_handlers: Dict[str, List[Callable[[str, EventEnvelope], Awaitable[None]]]] = {}
        self._running = False
        self._listener_task: Optional[asyncio.Task] = None
        self._pubsub = None

    async def start(self) -> None:
        """Start background listener if Redis client is available."""
        self._running = True
        if self.redis:
            try:
                self._pubsub = self.redis.pubsub()
                # Subscribe to wildcard patterns
                await self._pubsub.psubscribe("market.*", "strategy.*", "risk.*", "order.*", "position.*", "portfolio.*", "system.*")
                self._listener_task = asyncio.create_task(self._listen_loop())
                logger.info("EventBus started with Redis Pub/Sub listener.")
            except Exception as e:
                logger.warning(f"Could not initialize Redis pubsub listener: {e}. Operating in in-memory mode.")

    async def stop(self) -> None:
        """Gracefully stop event bus listener."""
        self._running = False
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._pubsub:
            try:
                await self._pubsub.unsubscribe()
                await self._pubsub.close()
            except Exception as e:
                logger.debug(f"Error closing pubsub: {e}")
        logger.info("EventBus stopped.")

    def subscribe(self, channel: str, handler: Callable[[EventEnvelope], Awaitable[None]]) -> None:
        """Register an async handler for a specific channel."""
        if channel not in self._handlers:
            self._handlers[channel] = []
        self._handlers[channel].append(handler)

    def subscribe_pattern(self, pattern: str, handler: Callable[[str, EventEnvelope], Awaitable[None]]) -> None:
        """Register an async handler for wildcard channel patterns (e.g. 'order.*')."""
        if pattern not in self._pattern_handlers:
            self._pattern_handlers[pattern] = []
        self._pattern_handlers[pattern].append(handler)

    async def publish(self, channel: str, envelope: EventEnvelope) -> None:
        """Publish an event envelope to a channel."""
        message_json = envelope.to_json()

        # 1. Dispatch locally in-memory first for deterministic immediate processing
        await self._dispatch_local(channel, envelope)

        # 2. Publish to Redis if connected
        if self.redis:
            try:
                await self.redis.publish(channel, message_json)
            except Exception as e:
                logger.error(f"Failed to publish event {envelope.event_id} to Redis channel {channel}: {e}")

    async def _dispatch_local(self, channel: str, envelope: EventEnvelope) -> None:
        """Dispatch event envelope to registered local handlers."""
        # Exact channel handlers
        if channel in self._handlers:
            for handler in self._handlers[channel]:
                try:
                    await handler(envelope)
                except Exception as e:
                    logger.error(f"Error in EventBus handler for channel {channel}: {e}", exc_info=True)

        # Pattern handlers
        for pattern, handlers in self._pattern_handlers.items():
            if self._channel_matches_pattern(channel, pattern):
                for handler in handlers:
                    try:
                        await handler(channel, envelope)
                    except Exception as e:
                        logger.error(f"Error in EventBus pattern handler for {pattern}: {e}", exc_info=True)

    @staticmethod
    def _channel_matches_pattern(channel: str, pattern: str) -> bool:
        """Simple glob pattern matching for channels."""
        if pattern == "*" or pattern == channel:
            return True
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            return channel == prefix or channel.startswith(prefix + ".")
        return False

    async def _listen_loop(self) -> None:
        """Background loop reading from Redis pubsub."""
        while self._running:
            try:
                message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] in ("message", "pmessage"):
                    channel = message["channel"]
                    if isinstance(channel, bytes):
                        channel = channel.decode("utf-8")
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    try:
                        envelope = EventEnvelope.from_json(data)
                        # Avoid double-dispatch if already handled locally by checking metadata if needed
                    except Exception as e:
                        logger.error(f"Failed to decode Redis pubsub message on {channel}: {e}")
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error in EventBus Redis listener loop: {e}")
                await asyncio.sleep(1.0)

