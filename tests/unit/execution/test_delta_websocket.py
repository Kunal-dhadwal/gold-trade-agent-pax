"""Unit tests for Delta WebSocket Client and protocol handling."""

import asyncio
import json
import pytest
import websockets
from websockets.asyncio.server import ServerConnection, serve
from src.execution.delta.signer import DeltaRequestSigner
from src.execution.delta.types import (
    DeltaAuthenticationError,
    DeltaChannel,
    DeltaConfig,
    DeltaEnvironment,
)
from src.execution.delta.websocket import DeltaWebSocketClient


@pytest.mark.asyncio
async def test_websocket_client_init_and_handlers():
    """Verify subscription handler registration and event routing."""
    config = DeltaConfig(ws_url="wss://testnet-socket.delta.exchange")
    client = DeltaWebSocketClient(config=config)

    received_events = []

    async def sample_handler(channel: str, data: dict):
        received_events.append((channel, data))

    client.subscribe("orders", sample_handler)
    assert "orders" in client._handlers
    assert len(client._handlers["orders"]) == 1

    # Simulate message handling directly
    order_event = json.dumps({"channel": "orders", "action": "update", "order_id": 1001})
    await client._handle_message(order_event)

    assert len(received_events) == 1
    assert received_events[0][0] == "orders"
    assert received_events[0][1]["order_id"] == 1001


@pytest.mark.asyncio
async def test_websocket_mock_handshake_and_subscriptions():
    """Verify end-to-end WebSocket connection, auth handshake, and channel subscription."""
    api_key = "test_ws_api_key"
    api_secret = "test_ws_api_secret_32_chars_long"
    signer = DeltaRequestSigner(api_key, api_secret)

    received_frames = []

    async def mock_ws_server(ws: ServerConnection):
        try:
            # 1. Receive auth frame
            auth_msg = await ws.recv()
            auth_data = json.loads(auth_msg)
            received_frames.append(auth_data)

            # Check auth
            assert auth_data.get("type") == "auth"
            payload = auth_data.get("payload", {})
            assert payload.get("api-key") == api_key

            # Reply with auth success
            await ws.send(json.dumps({"type": "auth", "status": "authenticated", "message": "OK"}))

            # 2. Receive subscribe frame
            sub_msg = await ws.recv()
            sub_data = json.loads(sub_msg)
            received_frames.append(sub_data)
            assert sub_data.get("type") == "subscribe"

            # 3. Push a position update
            await ws.send(json.dumps({
                "channel": "positions",
                "symbol": "PAXGUSDT",
                "size": 50,
                "entry_price": 2650.0,
            }))

            # 4. Handle a ping frame
            ping_msg = await ws.recv()
            ping_data = json.loads(ping_msg)
            if ping_data.get("type") == "ping":
                await ws.send(json.dumps({"type": "pong"}))

            await asyncio.sleep(0.1)
        except Exception as e:
            pass

    server = await serve(mock_ws_server, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    ws_url = f"ws://127.0.0.1:{port}"

    config = DeltaConfig(
        ws_url=ws_url,
        api_key=api_key,
        api_secret=api_secret,
        heartbeat_interval_seconds=0.1,  # Fast ping for test
    )
    client = DeltaWebSocketClient(config=config, signer=signer)

    pos_events = []
    async def pos_handler(channel: str, data: dict):
        pos_events.append(data)

    client.subscribe("positions", pos_handler)

    try:
        await client.connect()
        assert client.is_connected
        assert client.is_authenticated

        # Wait briefly for position event push
        await asyncio.sleep(0.3)
        assert len(pos_events) == 1
        assert pos_events[0]["symbol"] == "PAXGUSDT"
        assert pos_events[0]["size"] == 50

        # Verify frames received by server
        assert len(received_frames) >= 2
        assert received_frames[0]["type"] == "auth"
        assert received_frames[1]["type"] == "subscribe"
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_websocket_auth_rejection():
    """Verify that rejected auth frame raises DeltaAuthenticationError and disconnects."""
    api_key = "bad_key"
    api_secret = "bad_secret_32_chars_long_key"
    signer = DeltaRequestSigner(api_key, api_secret)

    async def mock_reject_server(ws: ServerConnection):
        try:
            await ws.recv()  # Auth frame
            await ws.send(json.dumps({
                "type": "auth",
                "status": "unauthorized",
                "message": "Invalid API key or signature",
            }))
        except Exception:
            pass

    server = await serve(mock_reject_server, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    ws_url = f"ws://127.0.0.1:{port}"

    config = DeltaConfig(ws_url=ws_url, api_key=api_key, api_secret=api_secret)
    client = DeltaWebSocketClient(config=config, signer=signer)

    try:
        with pytest.raises(DeltaAuthenticationError) as exc_info:
            await client.connect()
        assert "WebSocket authentication rejected" in str(exc_info.value)
        assert not client.is_connected
        assert not client.is_authenticated
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_websocket_duplicate_and_out_of_order_events():
    """Verify that handler handles duplicate events and out-of-order sequences gracefully."""
    config = DeltaConfig()
    client = DeltaWebSocketClient(config=config)

    processed_events = []
    seen_ids = set()

    async def deduplicating_order_handler(channel: str, data: dict):
        event_id = data.get("event_id")
        seq = data.get("sequence", 0)
        # Deduplication check
        if event_id in seen_ids:
            return
        seen_ids.add(event_id)
        processed_events.append((seq, data))

    client.subscribe("orders", deduplicating_order_handler)

    # Dispatch events: duplicate event_id and out-of-order sequences
    ev1 = json.dumps({"channel": "orders", "event_id": "EVT_1", "sequence": 1, "order_id": 101})
    ev2_dup = json.dumps({"channel": "orders", "event_id": "EVT_1", "sequence": 1, "order_id": 101})
    ev3_ooo = json.dumps({"channel": "orders", "event_id": "EVT_3", "sequence": 3, "order_id": 103})
    ev2_late = json.dumps({"channel": "orders", "event_id": "EVT_2", "sequence": 2, "order_id": 102})

    await client._handle_message(ev1)
    await client._handle_message(ev2_dup)
    await client._handle_message(ev3_ooo)
    await client._handle_message(ev2_late)

    # Expect 3 processed events, deduplicating the second EVT_1
    assert len(processed_events) == 3
    assert [p[0] for p in processed_events] == [1, 3, 2]
    # Verify ordering can be sorted deterministically
    sorted_events = sorted(processed_events, key=lambda x: x[0])
    assert [p[0] for p in sorted_events] == [1, 2, 3]

