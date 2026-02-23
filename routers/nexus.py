"""Nexus gateway router.

Exposes the Unified Message Bus over HTTP so the WebChat widget (and future
channel adapters) can send/receive messages through the Arcturus agent core.

Endpoints
---------
POST /api/nexus/webchat/inbound
    Receive an inbound WebChat message, route it through the bus (agent
    processing + outbound delivery to the session outbox).

GET  /api/nexus/webchat/messages/{session_id}
    Poll for queued outbound messages for a WebChat session. Each call drains
    and returns all pending messages (fire-and-forget delivery model).
"""

import asyncio
import json
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from gateway.envelope import MessageEnvelope

router = APIRouter(prefix="/nexus", tags=["Nexus"])

# Lazy reference to the shared MessageBus singleton.
# We defer import so that this module can be imported safely at startup
# before gateway components are fully initialized.
_bus = None


def _get_bus():
    global _bus
    if _bus is None:
        from shared.state import get_message_bus
        _bus = get_message_bus()
    return _bus


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class WebChatInboundRequest(BaseModel):
    """Inbound WebChat message from the widget."""

    session_id: str
    sender_id: str
    sender_name: str
    text: str
    message_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/webchat/inbound")
async def webchat_inbound(req: WebChatInboundRequest):
    """Receive a message from the WebChat widget.

    Builds a ``MessageEnvelope``, runs it through the bus (agent processing +
    formatted reply enqueued in the session outbox), and returns the bus result.

    The widget should follow up with GET ``/api/nexus/webchat/messages/{session_id}``
    to fetch the agent's reply.
    """
    envelope = MessageEnvelope.from_webchat(
        session_id=req.session_id,
        sender_id=req.sender_id,
        sender_name=req.sender_name,
        text=req.text,
        message_id=req.message_id or str(uuid.uuid4()),
    )
    result = await _get_bus().roundtrip(envelope)
    return result.to_dict()


@router.get("/webchat/messages/{session_id}")
async def webchat_poll(session_id: str):
    """Poll for pending outbound messages for a WebChat session.

    Drains the session outbox — each message is returned exactly once.
    Returns an empty list if no messages are queued.
    """
    bus = _get_bus()
    adapter = bus.adapters.get("webchat")
    messages = adapter.drain_outbox(session_id) if adapter else []
    return {
        "session_id": session_id,
        "messages": messages,
        "count": len(messages),
    }


@router.get("/webchat/stream/{session_id}")
async def webchat_stream(session_id: str, request: Request):
    """SSE push stream for a WebChat session.

    The client connects once; replies are pushed as ``event: message`` events
    the instant the agent delivers them — no polling required.  A ``ping``
    keepalive is sent every 15 seconds to prevent proxy timeouts.

    The polling endpoint (``/webchat/messages/{session_id}``) remains available
    as a fallback for clients that do not support SSE.
    """
    bus = _get_bus()
    adapter = bus.adapters.get("webchat")
    q = adapter.subscribe_sse(session_id) if adapter else asyncio.Queue()

    async def _generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield {"event": "message", "data": json.dumps(msg)}
                except asyncio.TimeoutError:
                    # Send a keepalive ping so the connection is not dropped by
                    # proxies / load balancers that time out idle streams.
                    yield {"event": "ping", "data": ""}
        finally:
            if adapter:
                adapter.unsubscribe_sse(session_id, q)

    return EventSourceResponse(_generator())


# ---------------------------------------------------------------------------
# Slack Events API
# ---------------------------------------------------------------------------


@router.post("/slack/events")
async def slack_events(request: Request) -> Dict[str, Any]:
    """Receive Slack Events API webhook.

    Handles two Slack event types:

    * ``url_verification`` — initial handshake when the Slack app is configured;
      returns the ``challenge`` token so Slack confirms ownership of the URL.
    * ``event_callback`` with ``message`` sub-type — routes the message through
      the Nexus bus (ingest → mock agent → deliver reply back to the channel).

    Signature verification is performed when ``SLACK_SIGNING_SECRET`` is set
    on the adapter (via env var or config).  Requests with an invalid signature
    are rejected with HTTP 403.  Signature checking is skipped in dev/test mode
    (when the secret is empty).
    """
    body = await request.body()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # 1. url_verification handshake (Slack app setup / re-verification).
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    # 2. Optional signature verification.
    bus = _get_bus()
    adapter = bus.adapters.get("slack")
    signing_secret: str = getattr(adapter, "signing_secret", "") if adapter else ""
    if signing_secret:
        ts = request.headers.get("X-Slack-Request-Timestamp", "")
        sig = request.headers.get("X-Slack-Signature", "")
        from channels.slack import SlackAdapter as _SlackAdapter
        if not _SlackAdapter.verify_signature(body, ts, sig, signing_secret):
            raise HTTPException(status_code=403, detail="Invalid Slack signature")

    # 3. Route message events through the bus.
    event = payload.get("event", {})
    if event.get("type") == "message" and not event.get("bot_id"):
        envelope = MessageEnvelope.from_slack(
            channel_id=event.get("channel", "unknown"),
            sender_id=event.get("user", "unknown"),
            sender_name=event.get("user", "unknown"),
            text=event.get("text", ""),
            message_id=event.get("ts", str(uuid.uuid4())),
            thread_ts=event.get("thread_ts"),
        )
        await bus.roundtrip(envelope)

    # Slack requires a 200 OK with any body to acknowledge receipt.
    return {"ok": True}


# ---------------------------------------------------------------------------
# Discord Interactions / Gateway webhook
# ---------------------------------------------------------------------------


@router.post("/discord/events")
async def discord_events(request: Request) -> Dict[str, Any]:
    """Receive Discord webhook events (Interactions endpoint or Gateway relay).

    Handles two Discord payload types:

    * ``PING`` (type 1) — initial handshake when the Interactions endpoint is
      first configured in the Discord Developer Portal; returns ``{"type": 1}``
      so Discord confirms ownership.
    * ``APPLICATION_COMMAND`` / message event (type 2 / custom relay) — routes
      the message through the Nexus bus (ingest → mock agent → deliver reply).

    Signature verification is performed using Ed25519 when ``DISCORD_PUBLIC_KEY``
    is set on the adapter.  Requests with an invalid signature are rejected with
    HTTP 401 (Discord's required status for failed signature checks).
    Signature checking is skipped in dev/test mode (when the key is empty).
    """
    body = await request.body()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # 1. Optional Ed25519 signature verification.
    bus = _get_bus()
    adapter = bus.adapters.get("discord")
    public_key: str = getattr(adapter, "public_key", "") if adapter else ""
    if public_key:
        timestamp = request.headers.get("X-Signature-Timestamp", "")
        signature = request.headers.get("X-Signature-Ed25519", "")
        from channels.discord import DiscordAdapter as _DiscordAdapter
        if not _DiscordAdapter.verify_signature(body, timestamp, signature, public_key):
            raise HTTPException(status_code=401, detail="Invalid Discord signature")

    # 2. PING handshake (Discord requires type=1 response).
    if payload.get("type") == 1:
        return {"type": 1}

    # 3. Route message events through the bus.
    #    Supports both Interactions (type 2 APPLICATION_COMMAND) and
    #    a simple relay format: {"type": "message", "channel_id": ..., ...}
    event_type = payload.get("type")
    if event_type == 2:
        # Slash command / application command interaction
        data = payload.get("data", {})
        interaction_id = str(payload.get("id", uuid.uuid4()))
        guild_id = str(payload.get("guild_id", "unknown"))
        channel_id = str(payload.get("channel_id", "unknown"))
        member = payload.get("member", {})
        user = member.get("user", payload.get("user", {}))
        sender_id = str(user.get("id", "unknown"))
        sender_name = user.get("username", "unknown")
        # For slash commands the text is the command name + options joined
        options = data.get("options", [])
        text_parts = [data.get("name", "")]
        for opt in options:
            text_parts.append(str(opt.get("value", "")))
        text = " ".join(filter(None, text_parts)) or "command"

        envelope = MessageEnvelope.from_discord(
            guild_id=guild_id,
            channel_id=channel_id,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            message_id=interaction_id,
        )
        await bus.roundtrip(envelope)
        # Discord Interactions requires an immediate acknowledgement
        return {"type": 5}  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE

    elif event_type == "message" or payload.get("channel_id"):
        # Simple relay format used by tests and gateway relay bots
        guild_id = str(payload.get("guild_id", "unknown"))
        channel_id = str(payload.get("channel_id", "unknown"))
        sender_id = str(payload.get("author", {}).get("id", payload.get("sender_id", "unknown")))
        sender_name = payload.get("author", {}).get("username", payload.get("sender_name", "unknown"))
        text = payload.get("content", payload.get("text", ""))
        message_id = str(payload.get("id", payload.get("message_id", str(uuid.uuid4()))))
        is_bot = payload.get("author", {}).get("bot", False)

        if not is_bot and text:
            envelope = MessageEnvelope.from_discord(
                guild_id=guild_id,
                channel_id=channel_id,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                message_id=message_id,
            )
            await bus.roundtrip(envelope)

    return {"ok": True}
