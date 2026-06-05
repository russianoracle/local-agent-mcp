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

### Plugin (MCP + skill, recommended)

```bash
# Installs MCP server + qwen-coder skill in one command
claude plugin install russianoracle/local-agent-mcp
```

### MCP only

```bash
# Register the MCP server (stdio transport)
claude mcp add -e OMLX_URL=http://localhost:8000 -e OMLX_API_KEY=1986 \
  local-agent -- uvx local-agent-mcp
```

> **Note:** `uvx local-agent-mcp` requires the package to be published on PyPI.
> Until then, use the direct path:
> ```bash
> claude mcp add -e OMLX_URL=http://localhost:8000 -e OMLX_API_KEY=1986 \
>   local-agent -- uv run --with "mcp[fastmcp]" --with httpx \
>   python /path/to/local_agent_bridge.py
> ```

### Skill only

```bash
npx skills add russianoracle/local-agent-mcp@qwen-coder
```

### Manual (project scope)

Copy `.mcp.json` to your project root and add to project scope:

```bash
claude mcp add --scope project ...
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OMLX_URL` | `http://localhost:8000` | oMLX server URL |
| `OMLX_API_KEY` | `1986` | oMLX API key (set via env, never hardcode) |
| `MODEL_ID` | `llm_default` | Model alias on oMLX |

Set via MCP config env block or shell export:

```bash
export OMLX_URL=http://localhost:8000
export OMLX_API_KEY=your-key
```

Change model server-side via `omlx alias set llm_default <model-id>` — no code changes needed.

## MCP Registry

Published at: `io.github.russianoracle/local-agent-mcp`

## License

MIT
