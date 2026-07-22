"""
mcp_agent.py
------------
Thin glue between:
  - the MCP server (Couchbase's official server, Streamable HTTP transport)
  - the OpenAI API (does the reasoning + decides which tools to call)

No LLM credentials ever touch the MCP server. No Couchbase credentials ever
touch OpenAI. This module just shuttles tool schemas and tool results
between the two.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


@dataclass
class ToolCallLog:
    """One tool call, kept around so the UI can show its work."""
    name: str
    arguments: dict
    result_text: str
    is_error: bool = False


@dataclass
class TurnResult:
    """Everything produced by one user turn, for the UI to render."""
    final_text: str
    tool_calls: list[ToolCallLog] = field(default_factory=list)
    updated_messages: list[dict] = field(default_factory=list)


def _mcp_tools_to_openai_format(mcp_tools) -> list[dict]:
    """Convert MCP Tool objects into the OpenAI tools format."""
    converted = []
    for tool in mcp_tools:
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.inputSchema,
                }
            }
        )
    return converted


def _flatten_tool_result(result) -> str:
    """MCP tool results are a list of content blocks (text, image, etc).
    For this demo we only expect text back from the Couchbase MCP server."""
    parts = []
    for block in result.content:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
        else:
            parts.append(str(block))
    return "\n".join(parts) if parts else "(empty result)"


async def run_turn(
    *,
    mcp_server_url: str,
    openai_api_key: str,
    model: str,
    max_tokens: int,
    system_prompt: str,
    max_tool_iterations: int,
    messages: list[dict],
) -> TurnResult:
    """
    Runs one full turn: connect to MCP, list tools, send the conversation to
    OpenAI, execute any tool calls it asks for, feed results back, repeat
    until OpenAI stops calling tools or we hit max_tool_iterations.

    `messages` should already include the new user message appended.
    Returns the final assistant text plus a log of every tool call made,
    and the full updated message list (so the caller can persist it).
    """
    client = AsyncOpenAI(api_key=openai_api_key)
    tool_calls: list[ToolCallLog] = []
    working_messages = list(messages)

    # Prepend system message if not already present
    if not working_messages or working_messages[0].get("role") != "system":
        working_messages.insert(0, {"role": "system", "content": system_prompt})

    async with streamablehttp_client(mcp_server_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_response = await session.list_tools()
            tools = _mcp_tools_to_openai_format(tools_response.tools)

            for _ in range(max_tool_iterations):
                response = await client.chat.completions.create(
                    model=model,
                    max_tokens=max_tokens,
                    tools=tools,
                    messages=working_messages,
                )

                assistant_content = response.choices[0].message.content or ""
                assistant_tool_calls = response.choices[0].message.tool_calls or []

                assistant_message = {
                    "role": "assistant",
                    "content": assistant_content,
                }
                if assistant_tool_calls:
                    assistant_message["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            }
                        }
                        for tc in assistant_tool_calls
                    ]

                working_messages.append(assistant_message)

                if response.choices[0].finish_reason != "tool_calls":
                    return TurnResult(
                        final_text=assistant_content or "(no text in response)",
                        tool_calls=tool_calls,
                        updated_messages=working_messages,
                    )

                for tool_call in assistant_tool_calls:
                    is_error = False
                    arguments = json.loads(tool_call.function.arguments) if isinstance(tool_call.function.arguments, str) else tool_call.function.arguments
                    result_text = ""
                    try:
                        result = await session.call_tool(tool_call.function.name, arguments)
                        result_text = _flatten_tool_result(result)
                        is_error = bool(getattr(result, "isError", False))
                    except Exception as exc:
                        result_text = f"Tool call failed: {exc}"
                        is_error = True

                    tool_calls.append(
                        ToolCallLog(
                            name=tool_call.function.name,
                            arguments=arguments,
                            result_text=result_text,
                            is_error=is_error,
                        )
                    )
                    working_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result_text,
                    })

            return TurnResult(
                final_text=(
                    "Stopped after the maximum number of tool calls for one turn "
                    f"({max_tool_iterations}) without a final answer. Try a narrower question."
                ),
                tool_calls=tool_calls,
                updated_messages=working_messages,
            )


async def check_connection(mcp_server_url: str) -> tuple[bool, str]:
    """Quick health check used by the sidebar 'Test connection' button."""
    try:
        async with streamablehttp_client(mcp_server_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools_response = await session.list_tools()
                names = ", ".join(t.name for t in tools_response.tools[:5])
                more = "..." if len(tools_response.tools) > 5 else ""
                return True, f"Connected. {len(tools_response.tools)} tools available ({names}{more})"
    except Exception as exc:  # noqa: BLE001
        return False, f"Connection failed: {exc}"


def run_turn_sync(**kwargs) -> TurnResult:
    """Sync wrapper so Streamlit's normal callback flow can call the async code."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        import nest_asyncio
        nest_asyncio.apply()

    return loop.run_until_complete(run_turn(**kwargs))


def check_connection_sync(mcp_server_url: str) -> tuple[bool, str]:
    return asyncio.run(check_connection(mcp_server_url))
