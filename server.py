"""Expose the agent over the A2A protocol (a2a-sdk 1.x = A2A protocol v1.0).

    GET  /.well-known/agent-card.json   public    discovery (what I am, what I can do, how to call me)
    POST /                              API key   A2A JSON-RPC (SendMessage, SendStreamingMessage, GetTask...)
    GET  /healthz                       public    health check for Render

Run locally:   python server.py
On Render:     PORT and RENDER_EXTERNAL_URL are set automatically.
"""

import hmac
import json
import logging
import os

import uvicorn
from a2a.helpers import get_message_text, new_task_from_user_message, new_text_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    SecurityRequirement,
    SecurityScheme,
)
from a2a.types.a2a_pb2 import APIKeySecurityScheme, StringList
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent import run_agent

load_dotenv()
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("a2a-agent")

PORT = int(os.environ.get("PORT", "10010"))
PUBLIC_URL = (os.environ.get("A2A_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL")
              or f"http://127.0.0.1:{PORT}").rstrip("/")
API_KEY = os.environ.get("A2A_API_KEY", "")  # empty = no auth (local dev only)
API_KEY_HEADER = "X-API-Key"


# ------------------------------------------------------------------ Agent Card (discovery)

def build_card() -> AgentCard:
    security = {}
    if API_KEY:  # tell callers how to authenticate; A2A clients read this from the card
        security = {
            "security_schemes": {"apiKey": SecurityScheme(api_key_security_scheme=APIKeySecurityScheme(
                location="header", name=API_KEY_HEADER, description="API key for this agent."))},
            "security_requirements": [SecurityRequirement(schemes={"apiKey": StringList()})],
        }
    return AgentCard(
        name="Assistant Agent",
        description="A small LLM assistant that does exact arithmetic and tells the current time "
                    "in any timezone, and answers short general questions.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=f"{PUBLIC_URL}/",
                                             protocol_version="1.0")],
        skills=[
            AgentSkill(id="calculate", name="Calculator",
                       description="Exact arithmetic on expressions or word problems.",
                       tags=["math", "calculator", "arithmetic"],
                       examples=["what is 18% of 2450?", "(120*3)/7"]),
            AgentSkill(id="current_time", name="Current time",
                       description="Current date and time in any IANA timezone.",
                       tags=["time", "date", "timezone"],
                       examples=["what time is it in Asia/Kolkata?", "current UTC time"]),
        ],
        **security,
    )


# ------------------------------------------------------------------ A2A task lifecycle

class AssistantExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:  # every A2A request runs inside a Task
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        await updater.start_work()

        text = get_message_text(context.message) if context.message else ""
        if not text.strip():
            await updater.reject(new_text_message("Send a text message.", context_id=task.context_id,
                                                  task_id=task.id))
            return
        try:
            answer = await run_agent(text)
        except Exception:  # noqa: BLE001 - never leak internals to remote callers
            log.exception("agent failed (task %s)", task.id)
            await updater.failed(new_text_message("Internal error while processing the request.",
                                                  context_id=task.context_id, task_id=task.id))
            return

        await updater.add_artifact(parts=[new_text_part(text=answer, media_type="text/plain")],
                                   name="result")
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


# ------------------------------------------------------------------ auth + app

class ApiKeyMiddleware:
    """Checks X-API-Key on everything except the Agent Card and /healthz. Pure ASGI (SSE-safe)."""

    PUBLIC_PATHS = {AGENT_CARD_WELL_KNOWN_PATH, "/healthz"}

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not API_KEY or scope["path"] in self.PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        presented = dict(scope["headers"]).get(API_KEY_HEADER.lower().encode(), b"").decode()
        if hmac.compare_digest(presented, API_KEY):
            await self.app(scope, receive, send)
            return
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body",
                    "body": json.dumps({"error": f"missing or invalid {API_KEY_HEADER}"}).encode()})


def build_app() -> Starlette:
    card = build_card()
    handler = DefaultRequestHandler(agent_executor=AssistantExecutor(),
                                    task_store=InMemoryTaskStore(), agent_card=card)

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "agent": card.name})

    app = Starlette(routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/"),
                            Route("/healthz", healthz)])
    app.add_middleware(ApiKeyMiddleware)
    return app


app = build_app()

if __name__ == "__main__":
    if not API_KEY:
        log.warning("A2A_API_KEY is not set - anyone can call this agent. Set it before deploying.")
    log.info("Agent Card: %s%s", PUBLIC_URL, AGENT_CARD_WELL_KNOWN_PATH)
    uvicorn.run(app, host="0.0.0.0", port=PORT)
