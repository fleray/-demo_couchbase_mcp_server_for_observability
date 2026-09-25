# Couchbase Observability Agent — Streamlit UI

A minimal chat UI that connects to the Couchbase MCP server and uses
**OpenAI** to answer questions about cluster health and query performance.

## Two separate credentials — don't mix them up

| Credential | Lives in | Used for |
|---|---|---|
| Couchbase `Administrator` / `password123` | `mcp-server` container env (`docker-compose.yml` in the parent folder) | The MCP server's own connection to Couchbase. You never enter this here. |
| OpenAI API key | `.env` in **this** folder | This script's calls to OpenAI. The MCP server never sees this. |

## Setup

1. Make sure the main demo stack is already running (`docker compose up -d`
   in the parent folder) — this script talks to `http://localhost:8000/mcp`.

2. Get an OpenAI API key: [platform.openai.com/account/api-keys](https://platform.openai.com/account/api-keys)
   → Create new secret key. (Free to create; you'll need a payment method
   on the account before requests succeed.)

3. Configure your key:
   ```bash
   cp .env.example .env
   # edit .env, paste your key after OPENAI_API_KEY=
   ```

`streamlit-agent` is already one of the services in the parent
`docker-compose.yml`, so `docker compose up -d` builds and runs it
automatically at `http://localhost:8501` — steps 1-3 above are all you need.
Restart it after editing `.env` so it picks up the key:

```bash
docker compose restart streamlit-agent
```

### Running it locally instead (without Docker)

Useful if you're actively editing `app.py` / `mcp_agent.py` and don't want
to rebuild the image on every change:

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```
It opens at `http://localhost:8501` — stop the Docker container first
(`docker compose stop streamlit-agent`) so the two don't fight over the port.

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

- `config.yaml` — non-secret defaults: MCP URL, model (`gpt-4o` by default —
  see `MODEL_OPTIONS` in `app.py` for the other choices), system prompt,
  tool loop limits. Fine to commit to git, edit freely, override from the
  sidebar.
- `.env` — the OpenAI API key only. **Do not commit this file.** It's
  already excluded via `streamlit-agent/.env` in the repo's `.gitignore`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Sidebar shows "No OPENAI_API_KEY found" | You haven't created `.env`, or it's empty, or the container hasn't been restarted since — see step 3 |
| "Test MCP connection" fails | Confirm `docker compose ps` shows `mcp-server` running; confirm the URL is `http://localhost:8000/mcp` |
| Answers say tool calls failed with an auth-looking error | The MCP server's own Couchbase credentials (in the parent `docker-compose.yml`) don't match the cluster — not something to fix in this folder |
| Streamlit says a request to OpenAI failed / invalid API key | Check the key is valid and the account has billing/credits set up at [platform.openai.com](https://platform.openai.com) |
