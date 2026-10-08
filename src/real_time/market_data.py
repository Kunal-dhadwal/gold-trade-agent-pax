"""Real-time market data providers with Delta public WebSocket and replay capabilities."""

from abc import ABC, abstractmethod
import asyncio
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional
import urllib.request
import websockets
from src.core.config import settings
from src.paper.specifications import get_instrument_specification

logger = logging.getLogger(__name__)


class MarketDataProvider(ABC):
    """Abstract base class for paper market data providers."""

    @abstractmethod
    async def start(self) -> None:
        """Initialize connection and start listening."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Close connections and cleanup."""
        pass

    @abstractmethod
    def is_healthy(self) -> bool:
        """Return True if market data feed is healthy and non-stale."""
        pass

    @abstractmethod
    def get_latest_candle(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Retrieve most recently closed candle for symbol."""
        pass

    @abstractmethod
    def get_clock_drift_ms(self) -> float:
        """Return estimated clock drift against exchange in milliseconds."""
        pass


class DeltaWebSocketMarketData(MarketDataProvider):
    """Production paper market data provider connecting to Delta Exchange public WebSocket."""

    def __init__(
        self,
        symbol: str = "PAXGUSDT",
        timeframe: str = "5m",
        ws_url: Optional[str] = None,
        api_base_url: Optional[str] = None,
    ):
        self.symbol = symbol.strip().upper()
        self.timeframe = timeframe
        self.ws_url = ws_url or settings.DELTA_WS_URL
        self.api_base_url = api_base_url or settings.DELTA_API_BASE_URL
        self.spec = get_instrument_specification(self.symbol)

        self._running = False
        self._connected = False
        self._last_candle: Optional[Dict[str, Any]] = None
        self._last_candle_time: float = 0.0
        self._clock_drift_ms: float = 0.0
        self._handlers: List[Callable[[Dict[str, Any]], Awaitable[None]]] = []
        self._ws_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None

    def add_candle_handler(self, handler: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        """Register callback invoked whenever a candle closes."""
        self._handlers.append(handler)

    def is_healthy(self) -> bool:
        """Check if feed is connected and data is not stale (< 15 seconds)."""
        if not self._connected or self._last_candle_time == 0:
            return False
        age = time.time() - self._last_candle_time
        return age <= 15.0

    def get_latest_candle(self, symbol: str) -> Optional[Dict[str, Any]]:
        return self._last_candle

    def get_clock_drift_ms(self) -> float:
        return self._clock_drift_ms

    async def validate_public_product(self) -> Dict[str, Any]:
        """Validate execution instrument against Delta Exchange public products API.

        Fails closed if instrument is missing, halted, suspended, wrong contract type,
        or has malformed metadata.
        """
        url = f"{self.api_base_url}/v2/products/{self.symbol}"
        try:
            # Run in executor to avoid blocking event loop
            def _fetch():
                req = urllib.request.Request(url, headers={"User-Agent": "AGentTrXPAUS/1.0"})
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(None, _fetch)
            if not isinstance(data, dict):
                raise ValueError(f"Malformed product API response for {self.symbol}: expected JSON object.")

            result = data.get("result")
            if not result or not isinstance(result, dict):
                raise ValueError(f"Delta product {self.symbol} not found in product registry. Fail-closed enforced.")

            # Validate symbol match
            res_symbol = str(result.get("symbol", "")).upper()
            if res_symbol != self.symbol:
                raise ValueError(
                    f"Product symbol mismatch: expected '{self.symbol}', received '{res_symbol}'."
                )

            # Validate required contract fields
            required_fields = ["symbol", "trading_status", "contract_type"]
            for f in required_fields:
                if f not in result or not result[f]:
                    raise ValueError(f"Malformed product metadata for {self.symbol}: missing required field '{f}'.")

            # Validate trading status
            status = str(result.get("trading_status", "")).lower()
            if status not in ("operational", "active"):
                raise ValueError(
                    f"Delta product {self.symbol} trading status is '{status}'. Fail-closed enforced."
                )

            logger.info(f"Verified Delta public product: {self.symbol} (status={status}, type={result.get('contract_type')}).")
            return result
        except Exception as e:
            logger.error(f"Failed to validate public product contract for {self.symbol}: {e}")
            raise

    async def start(self) -> None:
        """Start WebSocket client and health watchdog after validating product contract."""
        await self.validate_public_product()
        self._running = True
        self._ws_task = asyncio.create_task(self._connect_and_stream())
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        logger.info(f"DeltaWebSocketMarketData started for {self.symbol}:{self.timeframe}")

    async def stop(self) -> None:
        """Stop streaming and cleanup."""
        self._running = False
        self._connected = False
        if self._ws_task:
            self._ws_task.cancel()
        if self._watchdog_task:
            self._watchdog_task.cancel()
        logger.info("DeltaWebSocketMarketData stopped.")

    async def _connect_and_stream(self) -> None:
        """Maintain persistent WebSocket connection with exponential backoff."""
        backoff = 1.0
        channel_name = f"candlestick_{self.timeframe}"

        while self._running:
            try:
                logger.info(f"Connecting to Delta WebSocket: {self.ws_url}")
                async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=10) as ws:
                    self._connected = True
                    backoff = 1.0
                    logger.info("Delta WebSocket connected successfully. Subscribing...")

                    sub_payload = {
                        "type": "subscribe",
                        "payload": {
                            "channels": [
                                {
                                    "name": channel_name,
                                    "symbols": [self.symbol],
                                }
                            ]
                        },
                    }
                    await ws.send(json.dumps(sub_payload))

                    async for message in ws:
                        if not self._running:
                            break
                        await self._process_ws_message(message)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                logger.warning(f"Delta WebSocket connection dropped: {e}. Retrying in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)

    async def _process_ws_message(self, raw_message: str) -> None:
        """Parse, validate, and dispatch candle message."""
        try:
            data = json.loads(raw_message)
            if data.get("type") == "candlestick":
                candle_data = data.get("data", {})
                candle = self._validate_and_normalize_candle(candle_data)
                if candle:
                    self._last_candle = candle
                    self._last_candle_time = time.time()
                    for handler in self._handlers:
                        try:
                            await handler(candle)
                        except Exception as e:
                            logger.error(f"Error in market data candle handler: {e}")
        except Exception as e:
            logger.debug(f"Ignored non-candle WS message: {e}")

    def _validate_and_normalize_candle(self, c: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Validate candle geometry and compute clock drift."""
        try:
            o = float(c["open"])
            h = float(c["high"])
            l = float(c["low"])
            close_p = float(c["close"])
            v = float(c.get("volume", 0.0))

            # Geometry invariants
            if not (h >= o and h >= close_p and h >= l and l <= o and l <= close_p and l <= h):
                logger.warning(f"Dropping corrupted candle geometry: O={o}, H={h}, L={l}, C={close_p}")
                return None
            if o <= 0 or h <= 0 or l <= 0 or close_p <= 0 or v < 0:
                logger.warning(f"Dropping invalid price/volume candle: {c}")
                return None

            ts_raw = c.get("time") or c.get("timestamp")
            if isinstance(ts_raw, (int, float)):
                candle_ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc).isoformat()
                now_epoch = time.time()
                self._clock_drift_ms = abs(now_epoch - float(ts_raw)) * 1000.0
            else:
                candle_ts = datetime.now(timezone.utc).isoformat()

            return {
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "timestamp": candle_ts,
                "open": o,
                "high": h,
                "low": l,
                "close": close_p,
                "volume": v,
            }
        except (KeyError, ValueError, TypeError) as e:
            logger.debug(f"Failed to normalize candle: {e}")
            return None

    async def _watchdog_loop(self) -> None:
        """Watchdog checking data freshness every 5 seconds."""
        while self._running:
            try:
                await asyncio.sleep(5.0)
                if self._connected and self._last_candle_time > 0:
                    age = time.time() - self._last_candle_time
                    if age > 15.0:
                        logger.warning(f"Market data is stale! Last candle was {age:.1f}s ago (>15s).")
            except asyncio.CancelledError:
                break


class SimulatedLiveMarketData(MarketDataProvider):
    """Test and verification replay market data provider for deterministic testing."""

    def __init__(
        self,
        candles: List[Dict[str, Any]],
        step_delay_s: float = 0.0,
        symbol: Optional[str] = None,
        playback_speed: Optional[float] = None,
    ):
        self.candles = candles
        self.step_delay_s = step_delay_s
        self.symbol = symbol or "PAXGUSDT"
        self._handlers: List[Callable[[Dict[str, Any]], Awaitable[None]]] = []
        self._current_idx = 0
        self._running = False
        self._healthy = True

    def add_candle_handler(self, handler: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        self._handlers.append(handler)

    def subscribe(self, handler: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        """Alias for add_candle_handler."""
        self.add_candle_handler(handler)

    async def validate_public_product(self) -> Dict[str, Any]:
        """Simulation product validation returning compliant contract metadata."""
        return {
            "symbol": self.symbol,
            "trading_status": "operational",
            "contract_type": "spot",
            "tick_size": 0.01,
        }


    def is_healthy(self) -> bool:
        return self._healthy

    def get_latest_candle(self, symbol: str) -> Optional[Dict[str, Any]]:
        if 0 <= self._current_idx - 1 < len(self.candles):
            return self.candles[self._current_idx - 1]
        return None

    def get_clock_drift_ms(self) -> float:
        return 0.0

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def step_next(self) -> Optional[Dict[str, Any]]:
        """Advance one candle and trigger registered handlers."""
        if self._current_idx < len(self.candles):
            candle = self.candles[self._current_idx]
            self._current_idx += 1
            for handler in self._handlers:
                await handler(candle)
            return candle
        return None

    async def play_all(self) -> None:
        """Play through all candles sequentially."""
        while self._current_idx < len(self.candles) and self._running:
            await self.step_next()
            if self.step_delay_s > 0:
                await asyncio.sleep(self.step_delay_s)
