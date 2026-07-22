"""
app.py — Couchbase Observability Agent (Streamlit + MCP + OpenAI)

Run with:  streamlit run app.py

Two separate credentials are in play, and they never mix:
  - Couchbase credentials: live in the MCP server container (docker-compose.yml)
  - OpenAI API key:        lives in .env, read by THIS script only

The MCP server just answers tool calls. This script is the only thing that
talks to OpenAI and decides, turn by turn, which tools to call.
"""

import os

import streamlit as st
import yaml
from dotenv import load_dotenv

from mcp_agent import check_connection_sync, run_turn_sync

load_dotenv()  # reads .env into os.environ

st.set_page_config(page_title="Couchbase Observability Agent", page_icon="🛰️", layout="wide")


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
@st.cache_data
def load_config() -> dict:
    with open("config.yaml", "r") as f:
        return yaml.safe_load(f)


config = load_config()

MODEL_OPTIONS = [
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-4",
    "gpt-3.5-turbo",
]

# ---------------------------------------------------------------------------
# Sidebar — connection + settings
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("🛰️ Settings")

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if api_key:
        st.success("OpenAI API key loaded from .env")
    else:
        st.error("No OPENAI_API_KEY found. Copy .env.example to .env and fill it in.")
        api_key = st.text_input("Or paste a key for this session only", type="password")

    mcp_url = st.text_input(
        "MCP server URL",
        value=os.environ.get("MCP_SERVER_URL", config["mcp_server_url"]),
        help="The official Couchbase MCP server, Streamable HTTP endpoint.",
    )

    default_model = os.environ.get("OPENAI_MODEL", config["model"])
    model = st.selectbox(
        "Model",
        MODEL_OPTIONS,
        index=MODEL_OPTIONS.index(default_model) if default_model in MODEL_OPTIONS else 0,
    )

    max_iterations = st.slider("Max tool calls per turn", 1, 12, config["max_tool_iterations"])

    if st.button("Test MCP connection"):
        with st.spinner("Connecting..."):
            ok, message = check_connection_sync(mcp_url)
        (st.success if ok else st.error)(message)

    st.divider()
    st.caption(
        "This agent is read-only — it can observe cluster health and query "
        "performance, but has no write tools. Safe to point at a real cluster."
    )

    if st.button("Clear conversation"):
        st.session_state.raw_messages = []
        st.session_state.turns = []
        st.rerun()


# ---------------------------------------------------------------------------
# Session state
#   - raw_messages: full OpenAI format history (incl. every tool call /
#     tool result round trip) — this is what actually gets sent back to
#     OpenAI on the next turn, so it keeps full context.
#   - turns: a simplified list purely for rendering the chat UI — one entry
#     per user question, with the final answer and the tool calls it made.
# ---------------------------------------------------------------------------
if "raw_messages" not in st.session_state:
    st.session_state.raw_messages = []
if "turns" not in st.session_state:
    st.session_state.turns = []


# ---------------------------------------------------------------------------
# Main chat UI
# ---------------------------------------------------------------------------
st.title("Couchbase Observability Agent")
st.caption(
    "Ask about cluster health, buckets, schema, or query performance. "
    "Backed by the official Couchbase MCP server."
)

for turn in st.session_state.turns:
    with st.chat_message("user"):
        st.markdown(turn["user"])
    with st.chat_message("assistant"):
        st.markdown(turn["assistant_text"])
        if turn["tool_calls"]:
            with st.expander(f"🔧 {len(turn['tool_calls'])} tool call(s)"):
                for call in turn["tool_calls"]:
                    icon = "⚠️" if call.is_error else "✅"
                    st.markdown(f"{icon} **{call.name}**`({call.arguments})`")
                    st.code(call.result_text, language="json")

user_input = st.chat_input("e.g. What's the health of my Couchbase cluster?")

if user_input:
    if not api_key:
        st.error("Set an OpenAI API key in the sidebar or .env before asking questions.")
        st.stop()

    with st.chat_message("user"):
        st.markdown(user_input)

    st.session_state.raw_messages.append({"role": "user", "content": user_input})

    with st.chat_message("assistant"):
        with st.spinner("Thinking, and calling MCP tools as needed..."):
            try:
                result = run_turn_sync(
                    mcp_server_url=mcp_url,
                    openai_api_key=api_key,
                    model=model,
                    max_tokens=config["max_tokens"],
                    system_prompt=config["system_prompt"],
                    max_tool_iterations=max_iterations,
                    messages=st.session_state.raw_messages,
                )
            except Exception as exc:  # noqa: BLE001
                import traceback
                error_details = traceback.format_exc()
                st.error(f"Something went wrong: {exc}")
                st.error(f"Details:\n```\n{error_details}\n```")
                st.stop()

        st.markdown(result.final_text)
        if result.tool_calls:
            with st.expander(f"🔧 {len(result.tool_calls)} tool call(s)"):
                for call in result.tool_calls:
                    icon = "⚠️" if call.is_error else "✅"
                    st.markdown(f"{icon} **{call.name}**`({call.arguments})`")
                    st.code(call.result_text, language="json")

    # Persist full API context for the next turn, and a clean summary for the UI.
    st.session_state.raw_messages = result.updated_messages
    st.session_state.turns.append(
        {
            "user": user_input,
            "assistant_text": result.final_text,
            "tool_calls": result.tool_calls,
        }
    )
