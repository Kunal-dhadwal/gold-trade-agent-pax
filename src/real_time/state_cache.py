"""Ephemeral fast state cache implemented on Redis 7 with in-memory fallback."""

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class StateCache:
    """Fast ephemeral key-value/hash state cache for paper accounts, positions, and orders."""

    def __init__(self, redis_client: Optional[Any] = None):
        self.redis = redis_client
        self._memory: Dict[str, str] = {}

    async def set_account(self, account_id: str, data: Dict[str, Any]) -> None:
        """Cache current account balance, equity, and margin state."""
        key = f"paper:account:{account_id}"
        val = json.dumps(data)
        self._memory[key] = val
        if self.redis:
            try:
                await self.redis.set(key, val)
            except Exception as e:
                logger.warning(f"Failed to set account cache in Redis for {account_id}: {e}")

    async def get_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached account state."""
        key = f"paper:account:{account_id}"
        if self.redis:
            try:
                val = await self.redis.get(key)
                if val:
                    if isinstance(val, bytes):
                        val = val.decode("utf-8")
                    return json.loads(val)
            except Exception as e:
                logger.warning(f"Failed to get account cache from Redis for {account_id}: {e}")
        val = self._memory.get(key)
        return json.loads(val) if val else None

    async def set_position(self, account_id: str, symbol: str, data: Dict[str, Any]) -> None:
        """Cache active position details."""
        key = f"paper:position:{account_id}:{symbol}"
        val = json.dumps(data)
        self._memory[key] = val
        if self.redis:
            try:
                await self.redis.set(key, val)
            except Exception as e:
                logger.warning(f"Failed to set position cache in Redis: {e}")

    async def get_position(self, account_id: str, symbol: str) -> Optional[Dict[str, Any]]:
        """Retrieve active position details."""
        key = f"paper:position:{account_id}:{symbol}"
        if self.redis:
            try:
                val = await self.redis.get(key)
                if val:
                    if isinstance(val, bytes):
                        val = val.decode("utf-8")
                    return json.loads(val)
            except Exception as e:
                logger.warning(f"Failed to get position cache from Redis: {e}")
        val = self._memory.get(key)
        return json.loads(val) if val else None

    async def delete_position(self, account_id: str, symbol: str) -> None:
        """Clear closed position from cache."""
        key = f"paper:position:{account_id}:{symbol}"
        self._memory.pop(key, None)
        if self.redis:
            try:
                await self.redis.delete(key)
            except Exception as e:
                logger.warning(f"Failed to delete position cache in Redis: {e}")

    async def set_order(self, order_id: str, data: Dict[str, Any], ttl: int = 86400) -> None:
        """Cache order details with 24-hour TTL."""
        key = f"paper:order:{order_id}"
        val = json.dumps(data)
        self._memory[key] = val
        if self.redis:
            try:
                await self.redis.set(key, val, ex=ttl)
            except Exception as e:
                logger.warning(f"Failed to set order cache in Redis for {order_id}: {e}")

    async def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached order details."""
        key = f"paper:order:{order_id}"
        if self.redis:
            try:
                val = await self.redis.get(key)
                if val:
                    if isinstance(val, bytes):
                        val = val.decode("utf-8")
                    return json.loads(val)
            except Exception as e:
                logger.warning(f"Failed to get order cache from Redis: {e}")
        val = self._memory.get(key)
        return json.loads(val) if val else None

    async def set_latest_candle(self, symbol: str, timeframe: str, data: Dict[str, Any]) -> None:
        """Cache latest closed market candle."""
        key = f"market:candle:{symbol}:{timeframe}:latest"
        val = json.dumps(data)
        self._memory[key] = val
        if self.redis:
            try:
                await self.redis.set(key, val)
            except Exception as e:
                logger.warning(f"Failed to set candle cache in Redis: {e}")

    async def get_latest_candle(self, symbol: str, timeframe: str) -> Optional[Dict[str, Any]]:
        """Retrieve latest closed candle."""
        key = f"market:candle:{symbol}:{timeframe}:latest"
        if self.redis:
            try:
                val = await self.redis.get(key)
                if val:
                    if isinstance(val, bytes):
                        val = val.decode("utf-8")
                    return json.loads(val)
            except Exception as e:
                logger.warning(f"Failed to get candle cache from Redis: {e}")
        val = self._memory.get(key)
        return json.loads(val) if val else None

    async def set_risk_state(self, account_id: str, data: Dict[str, Any]) -> None:
        """Cache active circuit breaker and kill switch latches."""
        key = f"risk:state:{account_id}"
        val = json.dumps(data)
        self._memory[key] = val
        if self.redis:
            try:
                await self.redis.set(key, val)
            except Exception as e:
                logger.warning(f"Failed to set risk state cache in Redis: {e}")

    async def get_risk_state(self, account_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve active risk state."""
        key = f"risk:state:{account_id}"
        if self.redis:
            try:
                val = await self.redis.get(key)
                if val:
                    if isinstance(val, bytes):
                        val = val.decode("utf-8")
                    return json.loads(val)
            except Exception as e:
                logger.warning(f"Failed to get risk state cache from Redis: {e}")
        val = self._memory.get(key)
        return json.loads(val) if val else None

    async def flush_all_ephemeral(self) -> None:
        """Flush in-memory and ephemeral paper trading keys."""
        self._memory.clear()
        if self.redis:
            try:
                keys = await self.redis.keys("paper:*")
                if keys:
                    await self.redis.delete(*keys)
            except Exception as e:
                logger.warning(f"Failed to flush Redis keys: {e}")

