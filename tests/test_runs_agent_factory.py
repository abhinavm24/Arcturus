"""Tests for create_runs_agent factory in gateway/router.py.

Tests cover:
- Factory returns an object with a process_message method
- process_message happy path: POST run → poll → completed with output
- process_message when run fails: status=failed → error reply
- process_message timeout: run stays "running" past deadline → timeout reply
- process_message when POST /api/runs itself errors → error reply

All network calls are mocked via unittest.mock. No real server is needed.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from gateway.envelope import MessageEnvelope
from gateway.router import create_runs_agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_envelope(text: str = "What is the capital of France?") -> MessageEnvelope:
    return MessageEnvelope.from_webchat(
        session_id="test-session-001",
        sender_id="user-001",
        sender_name="Test User",
        text=text,
        message_id="MSG-001",
    )


def _mock_post_response(run_id: str = "1700000042") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"id": run_id, "status": "starting"}
    resp.raise_for_status = MagicMock()
    return resp


def _mock_output_response(
    run_id: str = "1700000042",
    status: str = "completed",
    output: str = "Paris is the capital of France.",
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"run_id": run_id, "status": status, "output": output}
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Test 1: factory returns object with process_message
# ---------------------------------------------------------------------------


def test_create_runs_agent_returns_object():
    """create_runs_agent() must return an object with an async process_message method."""

    async def _run():
        agent = await create_runs_agent("session-001")
        assert hasattr(agent, "process_message")
        assert callable(agent.process_message)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 2: happy path — POST run → poll once → completed
# ---------------------------------------------------------------------------


def test_process_message_success():
    """process_message returns reply with status=completed when run succeeds."""
    run_id = "1700000042"

    async def _run():
        agent = await create_runs_agent("session-001")
        envelope = _make_envelope()

        post_resp = _mock_post_response(run_id)
        output_resp = _mock_output_response(run_id, "completed", "Paris is the capital of France.")

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=post_resp), \
             patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=output_resp):
            result = await agent.process_message(envelope)

        assert result["status"] == "completed"
        assert "Paris" in result["reply"]
        assert result["run_id"] == run_id

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 3: run fails → error reply
# ---------------------------------------------------------------------------


def test_process_message_run_failure():
    """process_message returns a failure reply when run status=failed."""
    run_id = "1700000043"

    async def _run():
        agent = await create_runs_agent("session-002")
        envelope = _make_envelope("crash this")

        post_resp = _mock_post_response(run_id)
        failed_resp = _mock_output_response(run_id, "failed", None)
        failed_resp.json.return_value = {"run_id": run_id, "status": "failed", "output": None}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=post_resp), \
             patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=failed_resp):
            result = await agent.process_message(envelope)

        assert result["status"] == "failed"
        assert isinstance(result["reply"], str)
        assert len(result["reply"]) > 0

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 4: POST /api/runs itself errors → graceful error reply
# ---------------------------------------------------------------------------


def test_process_message_post_error():
    """process_message returns error reply when POST /api/runs raises."""

    async def _run():
        agent = await create_runs_agent("session-003")
        envelope = _make_envelope("hello")

        with patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            side_effect=httpx.RequestError("connection refused"),
        ):
            result = await agent.process_message(envelope)

        assert result["status"] == "error"
        assert isinstance(result["reply"], str)
        assert len(result["reply"]) > 0

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 5: polling times out → timeout reply
# ---------------------------------------------------------------------------


def test_process_message_timeout():
    """process_message returns timeout reply when run never completes within deadline."""
    run_id = "1700000044"

    async def _run():
        agent = await create_runs_agent("session-004")
        envelope = _make_envelope("slow query")

        post_resp = _mock_post_response(run_id)
        running_resp = _mock_output_response(run_id, "running", None)
        running_resp.json.return_value = {"run_id": run_id, "status": "running", "output": None}

        import gateway.router as router_mod

        with patch.object(router_mod, "_POLL_TIMEOUT_S", 0.01), \
             patch.object(router_mod, "_POLL_INTERVAL_S", 0.001), \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=post_resp), \
             patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=running_resp):
            result = await agent.process_message(envelope)

        assert result["status"] == "timeout"
        assert isinstance(result["reply"], str)
        assert len(result["reply"]) > 0

    asyncio.run(_run())
