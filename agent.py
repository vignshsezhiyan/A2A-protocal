"""The agent brain: an LLM (OpenAI-compatible) with two small tools.

    user text ──► LLM ──tool_calls──► calculate / get_current_time ──► LLM ──► final answer
                   (repeats until the LLM answers without a tool, max MAX_STEPS)

Nothing here knows about A2A - server.py wraps `run_agent()` as an A2A service.
"""

import ast
import json
import logging
import operator
import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openai import AsyncOpenAI

log = logging.getLogger("a2a-agent")

MAX_STEPS = 5  # hard stop so a confused LLM can't loop forever

SYSTEM_PROMPT = """You are a helpful assistant agent that other agents call over the A2A protocol.
- Use `calculate` for any arithmetic; never do math in your head.
- Use `get_current_time` for questions about the current date or time.
- Answer everything else directly. Keep answers short and factual."""


# ------------------------------------------------------------------ tools

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def calculate(expression: str) -> str:
    """Safely evaluate + - * / // % ** and parentheses (no eval())."""
    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            if isinstance(node.op, ast.Pow) and abs(ev(node.right)) > 100:
                raise ValueError("exponent too large")
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError("only numbers and + - * / // % ** ( ) are allowed")

    try:
        return str(ev(ast.parse(expression, mode="eval").body))
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError) as e:
        return f"error: {e}"


def get_current_time(timezone: str = "UTC") -> str:
    try:
        return datetime.now(ZoneInfo(timezone)).strftime(f"%Y-%m-%d %H:%M:%S ({timezone})")
    except (ZoneInfoNotFoundError, ValueError):
        return f"error: unknown timezone {timezone!r}, use an IANA name like 'Asia/Kolkata'"


TOOLS = {"calculate": calculate, "get_current_time": get_current_time}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "calculate",
        "description": "Evaluate an arithmetic expression, e.g. '(120*3)/7'.",
        "parameters": {"type": "object", "required": ["expression"],
                       "properties": {"expression": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "get_current_time",
        "description": "Current date and time in an IANA timezone, e.g. 'Asia/Kolkata'. Default UTC.",
        "parameters": {"type": "object",
                       "properties": {"timezone": {"type": "string"}}}}},
]


# ------------------------------------------------------------------ LLM loop

_llm: AsyncOpenAI | None = None


def llm() -> AsyncOpenAI:
    global _llm
    if _llm is None:
        _llm = AsyncOpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ["LLM_API_KEY"],
                           timeout=60, max_retries=2)
    return _llm


async def run_agent(user_text: str) -> str:
    messages: list = [{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": user_text}]
    for _ in range(MAX_STEPS):
        resp = await llm().chat.completions.create(
            model=os.environ.get("LLM_MODEL", "gpt-oss-120b"), messages=messages, tools=TOOL_SCHEMAS)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return msg.content or ""

        messages.append(msg.model_dump(exclude_none=True))
        for call in msg.tool_calls:
            fn = TOOLS.get(call.function.name)
            try:
                args = json.loads(call.function.arguments or "{}")
                result = fn(**args) if fn else f"error: unknown tool {call.function.name}"
            except (json.JSONDecodeError, TypeError) as e:
                result = f"error: bad arguments ({e})"
            log.info("tool %s(%s) -> %s", call.function.name, call.function.arguments, result)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    return "Sorry, I could not finish that request within the step limit."
