# local-agent-mcp

MCP server that delegates code audit and file-write tasks to a local LLM running on Apple Silicon via [oMLX](https://omlx.dev). Zero cloud calls — all inference runs on M3 Pro GPU.

## What it does

Provides two MCP tools for Claude Code:

- **`delegate_to_local_llm`** — sends prompts to a local model (default: `llm_default` alias on oMLX). Supports `audit` mode (code review, analysis) and `write` mode (file creation).
- **`check_bridge_health`** — verifies oMLX server is reachable.

Also includes **`llm_mlx_tools_server.py`** — a second MCP server exposing embed, rerank, and summarize tools via mlx-embeddings.

## Requirements

- macOS with Apple Silicon (M1+)
- [oMLX](https://omlx.dev) runtime running on `:8000`
- Python 3.10+

## Install

```bash
uvx local-agent-mcp
```

Or clone and run directly:

```bash
git clone https://github.com/russianoracle/local-agent-mcp
cd local-agent-mcp
uv run local_agent_bridge.py
```

## Claude Code integration

Add to your `~/.claude/mcp.json` or project `.mcp.json`:

```json
{
  "mcpServers": {
    "local-agent": {
      "command": "uvx",
      "args": ["local-agent-mcp"],
      "env": {
        "HTTP_PROXY": "",
        "HTTPS_PROXY": ""
      }
    }
  }
}
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OMLX_URL` | `http://localhost:8000` | oMLX server URL |
| `OMLX_API_KEY` | `1986` | oMLX API key |
| `MODEL_ID` | `llm_default` | Model alias on oMLX |

Change model server-side via `omlx alias set llm_default <model-id>` — no code changes needed.

## MCP Registry

Published at: `io.github.russianoracle/local-agent-mcp`

## License

MIT
