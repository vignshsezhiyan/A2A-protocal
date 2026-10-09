# a2a-agent

A small LLM agent exposed over the **A2A protocol (v1.0, `a2a-sdk==1.2.2`)**. Other agents
discover it from its Agent Card and call its skills.

| Tool               | Skill on the card | Example                             |
|--------------------|-------------------|-------------------------------------|
| `calculate`        | `calculate`       | "what is 18% of 2450?"              |
| `get_current_time` | `current_time`    | "what time is it in Asia/Kolkata?"  |

```
agent.py     LLM + 2 tools (no A2A code)
server.py    Agent Card + A2A JSON-RPC endpoint + X-API-Key auth + /healthz
client.py    discover the agent and send it a message
render.yaml  Render deployment
```

## Endpoints

| Method | Path                           | Auth        | Purpose                                     |
|--------|--------------------------------|-------------|---------------------------------------------|
| GET    | `/.well-known/agent-card.json` | public      | discovery                                   |
| POST   | `/`                            | `X-API-Key` | A2A JSON-RPC (`SendMessage`, streaming, `GetTask`, ...) |
| GET    | `/healthz`                     | public      | health check                                |

## Run locally

```bash
cd a2a-agent
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # fill LLM_* and A2A_API_KEY
.venv/bin/python server.py    # http://127.0.0.1:10010

# in another terminal (reads A2A_API_KEY from .env)
.venv/bin/python client.py "what is 18% of 2450?"
```

## Deploy on Render

1. Push this folder to GitHub.
2. In Render, go to **New + → Blueprint** and pick the repo; it reads `render.yaml`.
   Or create a **Web Service** by hand:
   - Root dir: `a2a-agent`
   - Build: `pip install -r requirements.txt`
   - Start: `python server.py`
   - Health check: `/healthz`
3. Set `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL`. `A2A_API_KEY` is auto-generated; copy it from the dashboard.
4. You don't need to set the URL. Render provides `PORT` and `RENDER_EXTERNAL_URL`, and the Agent Card
   advertises `https://<name>.onrender.com/` automatically.

Check it:

```bash
curl https://<name>.onrender.com/.well-known/agent-card.json
A2A_API_KEY=<key> .venv/bin/python client.py "time in UTC?" --url https://<name>.onrender.com
```

> On Render's **free** plan the service sleeps after 15 minutes idle, so the first call can take 30–60 seconds.
> Use a client timeout of at least 90 seconds.

## Connect from your local supervisor (`mas-a2a-production`)

Add this entry to `mas-a2a-production/config/agents.yaml`:

```yaml
  - name: assistant-agent
    url: ${ASSISTANT_AGENT_URL:-http://127.0.0.1:10010}
    api_key_env: ASSISTANT_AGENT_CLIENT_KEY
    enabled: true
```

and to `mas-a2a-production/.env`:

```
ASSISTANT_AGENT_URL=https://<name>.onrender.com
ASSISTANT_AGENT_CLIENT_KEY=<the A2A_API_KEY from Render>
```

The supervisor fetches the card, sees the `X-API-Key` scheme, and sends the key automatically.
Each skill on the card becomes a tool the supervisor's LLM can call.

## Add another tool

1. Write a function in `agent.py` and add it to `TOOLS` and `TOOL_SCHEMAS`.
2. Add a matching `AgentSkill` in `build_card()` in `server.py` so other agents can discover it.
