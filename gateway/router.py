"""Message router for the Arcturus omni-channel gateway.

Routes normalized MessageEnvelopes to the appropriate agent instances
based on channel, conversation ID, and session affinity policies.
"""

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

import httpx

from gateway.envelope import MessageEnvelope

if TYPE_CHECKING:
    from gateway.formatter import MessageFormatter

logger = logging.getLogger(__name__)

# Default bot mention token checked when group_activation == "mention-only"
_DEFAULT_BOT_MENTION = "@arcturus"


class MessageRouter:
    """Routes messages to agent instances based on conversation context.

    Implements session affinity: ensures messages from the same conversation
    are routed to the same agent instance for continuity.

    Group activation policies (per channel):
    - ``"always-on"``: every message is routed to the agent (default).
    - ``"mention-only"``: message is only routed when it contains the bot
      mention token (default: ``@arcturus``).  Messages that don't mention
      the bot return ``{"routed": False, "reason": "mention_required"}``.
    """

    def __init__(
        self,
        agent_factory: Callable[..., Any],
        formatter: Optional["MessageFormatter"] = None,
        group_activation: Optional[Dict[str, str]] = None,
        bot_mention: str = _DEFAULT_BOT_MENTION,
    ):
        """Initialize the message router.

        Args:
            agent_factory: Callable that creates or retrieves an agent instance.
            formatter: Optional MessageFormatter for outbound reply formatting.
            group_activation: Per-channel activation policy map, e.g.
                ``{"telegram": "mention-only", "webchat": "always-on"}``.
                Channels not present default to ``"always-on"``.
            bot_mention: Token that counts as a mention in ``mention-only``
                mode (case-insensitive).  Defaults to ``"@arcturus"``.
        """
        self.agent_factory = agent_factory
        self.formatter = formatter
        self.group_activation: Dict[str, str] = group_activation or {}
        self.bot_mention = bot_mention.lower()
        self.sessions: Dict[str, Any] = {}  # In-memory session map

    def _is_activated(self, envelope: MessageEnvelope) -> bool:
        """Return True if the envelope should be routed to an agent.

        Checks the group_activation policy for the envelope's channel:
        - ``"always-on"`` (or not configured): always True.
        - ``"mention-only"``: True only when bot_mention appears in the
          message content (case-insensitive).
        """
        policy = self.group_activation.get(envelope.channel, "always-on")
        if policy == "mention-only":
            return self.bot_mention in envelope.content.lower()
        return True  # "always-on" or unknown policy

    async def route(self, envelope: MessageEnvelope) -> Dict[str, Any]:
        """Route a message envelope to the appropriate agent.

        Args:
            envelope: MessageEnvelope to route

        Returns:
            Dict with routing result, agent response, and metadata
        """
        # Group activation gate — skip agent for mention-only channels with no mention
        if not self._is_activated(envelope):
            logger.info(
                f"Skipping message from {envelope.sender_name} on {envelope.channel} "
                f"(mention-only policy, no '{self.bot_mention}' in message)"
            )
            return {
                "routed": False,
                "session_id": None,
                "channel": envelope.channel,
                "message_id": envelope.channel_message_id,
                "agent_response": None,
                "status": "skipped",
                "reason": "mention_required",
            }

        # Determine session ID for routing
        session_id = envelope.session_id or envelope.conversation_id or envelope.thread_id
        if not session_id:
            # Fallback: create session from channel + sender
            session_id = f"{envelope.channel}_{envelope.sender_id}"

        logger.info(
            f"Routing message from {envelope.sender_name} on {envelope.channel} to session {session_id}"
        )

        # Get or create agent instance for this session
        agent = await self._get_or_create_agent(session_id)

        # Process message through agent
        result = await self._process_message(agent, envelope)

        # Format the reply text for the envelope's channel if a formatter is wired in
        if self.formatter and isinstance(result, dict) and "reply" in result:
            result["reply"] = self.formatter.format(result["reply"], envelope.channel)

        return {
            "routed": True,
            "session_id": session_id,
            "channel": envelope.channel,
            "message_id": envelope.channel_message_id,
            "agent_response": result,
            "status": "success",
        }

    async def _get_or_create_agent(self, session_id: str) -> Any:
        """Get existing agent or create new one for session.

        Implements session affinity: same session always routes to same agent.

        Args:
            session_id: Unique session identifier

        Returns:
            Agent instance for this session
        """
        if session_id in self.sessions:
            logger.debug(f"Reusing agent for session {session_id}")
            return self.sessions[session_id]

        logger.debug(f"Creating new agent for session {session_id}")
        agent = await self.agent_factory(session_id=session_id)
        self.sessions[session_id] = agent
        return agent

    async def _process_message(self, agent: Any, envelope: MessageEnvelope) -> Dict[str, Any]:
        """Process a message through an agent.

        Args:
            agent: Agent instance
            envelope: MessageEnvelope to process

        Returns:
            Agent response dict
        """
        # Call agent's message processing method
        # The agent object should have an async method like process_message()
        # or we can call it as a callable
        if hasattr(agent, "process_message") and callable(agent.process_message):
            response = await agent.process_message(envelope)
        elif callable(agent):
            response = await agent(envelope)
        else:
            # Fallback: return mock response
            response = self._mock_agent_response(envelope)

        return response

    @staticmethod
    def _mock_agent_response(envelope: MessageEnvelope) -> Dict[str, Any]:
        """Generate a mock agent response for testing/stub purposes.

        Args:
            envelope: MessageEnvelope that was processed

        Returns:
            Mock agent response dict
        """
        return {
            "status": "processed",
            "message_id": envelope.channel_message_id,
            "reply": f"Echo: {envelope.content}",
            "channel": envelope.channel,
            "sender_id": envelope.sender_id,
        }

    async def shutdown(self) -> None:
        """Gracefully shutdown all agent sessions."""
        logger.info(f"Shutting down {len(self.sessions)} agent sessions")
        # Clean up any resources associated with agents if needed
        self.sessions.clear()


async def create_mock_agent(session_id: str) -> Any:
    """Factory function to create a mock agent for testing.

    Args:
        session_id: Unique session identifier

    Returns:
        Mock agent object with process_message method
    """

    class MockAgent:
        """Simple mock agent for testing."""

        def __init__(self, session_id: str):
            self.session_id = session_id
            self.message_count = 0

        async def process_message(self, envelope: MessageEnvelope) -> Dict[str, Any]:
            """Process a message (mock implementation).

            Args:
                envelope: MessageEnvelope to process

            Returns:
                Mock response dict
            """
            self.message_count += 1
            return {
                "status": "processed",
                "message_id": envelope.channel_message_id,
                "reply": f"[Session {self.session_id}] Processed: {envelope.content}",
                "channel": envelope.channel,
                "sender_id": envelope.sender_id,
                "message_number": self.message_count,
            }

    return MockAgent(session_id)


# ---------------------------------------------------------------------------
# Real-agent factory — backed by AgentLoop4 via /api/runs
# ---------------------------------------------------------------------------

# Configurable via env; default assumes co-located FastAPI server
_ARCTURUS_BASE_URL = os.getenv("ARCTURUS_BASE_URL", "http://localhost:8000")
_POLL_INTERVAL_S = 2.0    # seconds between GET /output polls
_POLL_TIMEOUT_S = 120.0   # total wait before giving up


async def create_runs_agent(session_id: str) -> Any:
    """Factory that creates an agent backed by the real AgentLoop4 via /api/runs.

    The returned agent object calls ``POST /api/runs`` for each message and
    polls ``GET /api/runs/{run_id}/output`` until the run completes or times
    out, then returns the extracted text reply.

    Known limitation: each call starts a *fresh* AgentLoop4 run — there is no
    cross-message conversation memory.  This is a documented P01 known gap;
    persistent session continuity requires deeper runs-API changes (P15 scope).

    Args:
        session_id: Nexus session identifier (not forwarded to runs API yet).

    Returns:
        RunsAgentAdapter with ``process_message(envelope) -> Dict`` method.
    """

    class RunsAgentAdapter:
        """Adapter that delegates to AgentLoop4 via the /api/runs HTTP API."""

        def __init__(self, session_id: str):
            self.session_id = session_id

        async def process_message(self, envelope: MessageEnvelope) -> Dict[str, Any]:
            base = _ARCTURUS_BASE_URL
            async with httpx.AsyncClient(timeout=10.0) as client:
                # 1. Submit the run
                try:
                    post_resp = await client.post(
                        f"{base}/api/runs",
                        json={"query": envelope.content},
                    )
                    post_resp.raise_for_status()
                    run_id = post_resp.json()["id"]
                except Exception as exc:
                    logger.error("create_runs_agent: failed to start run: %s", exc)
                    return {
                        "status": "error",
                        "reply": "Sorry, I could not reach the agent. Please try again.",
                        "channel": envelope.channel,
                        "sender_id": envelope.sender_id,
                    }

                # 2. Poll for completion
                deadline = asyncio.get_event_loop().time() + _POLL_TIMEOUT_S
                poll_client = httpx.AsyncClient(timeout=10.0)
                try:
                    while True:
                        if asyncio.get_event_loop().time() >= deadline:
                            logger.warning(
                                "create_runs_agent: run %s timed out after %ss",
                                run_id, _POLL_TIMEOUT_S,
                            )
                            return {
                                "status": "timeout",
                                "reply": "The agent took too long to respond. Please try again.",
                                "channel": envelope.channel,
                                "sender_id": envelope.sender_id,
                            }

                        try:
                            get_resp = await poll_client.get(
                                f"{base}/api/runs/{run_id}/output"
                            )
                            get_resp.raise_for_status()
                            data = get_resp.json()
                        except Exception as exc:
                            logger.error(
                                "create_runs_agent: poll error for run %s: %s", run_id, exc
                            )
                            await asyncio.sleep(_POLL_INTERVAL_S)
                            continue

                        status = data.get("status")
                        if status == "running":
                            await asyncio.sleep(_POLL_INTERVAL_S)
                            continue

                        if status == "failed":
                            return {
                                "status": "failed",
                                "reply": "The agent encountered an error processing your request.",
                                "channel": envelope.channel,
                                "sender_id": envelope.sender_id,
                            }

                        # completed (or unknown status — treat as done)
                        output = data.get("output") or "Done."
                        return {
                            "status": "completed",
                            "reply": output,
                            "channel": envelope.channel,
                            "sender_id": envelope.sender_id,
                            "run_id": run_id,
                        }
                finally:
                    await poll_client.aclose()

    return RunsAgentAdapter(session_id)
