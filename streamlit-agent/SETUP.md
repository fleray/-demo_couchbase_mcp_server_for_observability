# Couchbase Observability Agent — Streamlit UI

A minimal chat UI that connects to the Couchbase MCP server and uses Claude
to answer questions about cluster health and query performance.

## Two separate credentials — don't mix them up

| Credential | Lives in | Used for |
|---|---|---|
| Couchbase `Administrator` / `password123` | `mcp-server` container env (`docker-compose.yml` in the parent folder) | The MCP server's own connection to Couchbase. You never enter this here. |
| Anthropic API key | `.env` in **this** folder | This script's calls to Claude. The MCP server never sees this. |

## Setup

1. Make sure the main demo stack is already running (`docker compose up -d`
   in the parent folder) — this script talks to `http://localhost:8000/mcp`.

2. Get an Anthropic API key: [console.anthropic.com](https://console.anthropic.com)
   → Settings → API Keys → Create Key. (Free to create; you'll need a
   payment method on the account before requests succeed.)

3. Configure your key:
   ```bash
   cp .env.example .env
   # edit .env, paste your key after ANTHROPIC_API_KEY=
   ```

4. Install dependencies (a virtual environment is recommended):
   ```bash
   python3 -m venv venv
   source venv/bin/activate        # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

5. Run it:
   ```bash
   streamlit run app.py
   ```
   It opens at `http://localhost:8501`.

## Using it

- Sidebar → **Test MCP connection** first, to confirm it can reach the
  server and see its tool list before you ask anything.
- Ask things like:
  - "What's the health of my Couchbase cluster?"
  - "What are the longest-running queries right now?"
  - "Are any queries using the primary index instead of a covering index?"
- Expand **🔧 N tool call(s)** under any answer to see exactly which MCP
  tools were called, with what arguments, and what came back — useful for
  showing people this isn't a canned response.

## Config file vs. .env — what goes where

- `config.yaml` — non-secret defaults: MCP URL, model, system prompt, tool
  loop limits. Fine to commit to git, edit freely, override from the sidebar.
- `.env` — the Anthropic API key only. **Do not commit this file.** It's
  already excluded if you're using the `.gitignore` pattern `*.env`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Sidebar shows "No ANTHROPIC_API_KEY found" | You haven't created `.env`, or it's empty — see step 3 |
| "Test MCP connection" fails | Confirm `docker compose ps` shows `mcp-server` running; confirm the URL is `http://localhost:8000/mcp` |
| Answers say tool calls failed with an auth-looking error | The MCP server's own Couchbase credentials (in the parent `docker-compose.yml`) don't match the cluster — not something to fix in this folder |
| Streamlit says "connection refused" calling Claude | Check the key is valid and the account has billing/credits set up in the Anthropic Console |
