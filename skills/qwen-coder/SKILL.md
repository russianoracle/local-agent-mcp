---
name: qwen-coder
description: >
  Delegate to local LLM on M3 Pro GPU (oMLX, model alias llm_default) via mcp__local-agent__delegate_to_local_llm.
  Triggers EN: "audit", "review code", "second opinion", "heavy analysis", "local gpu", "@qwen", "run locally".
  Triggers RU: "аудит", "ревью кода", "второе мнение", "тяжёлый анализ", "локально", "на gpu", "@qwen".
---

# qwen-coder

Use `mcp__local-agent__delegate_to_local_llm` to run tasks on the local model via oMLX
(M3 Pro GPU, model alias `llm_default` — change server-side to swap models). Zero cloud calls.

## When to use Qwen (not Claude)

| Task | Use |
|------|-----|
| Code audit / security review of large file set | Qwen |
| Refactor or implement feature by spec | Qwen |
| Parallel second opinion on Claude's output | Qwen |
| Heavy grep/search across codebase | Qwen |
| Quick question, web search, email, calendar | Claude |
| Tasks requiring claude.ai API access | Claude |

## Trigger phrases

**EN:** "audit", "review code", "second opinion", "run locally", "local gpu", "@qwen",
"heavy analysis", "scan codebase", "refactor with local model"

**RU:** "аудит", "ревью", "второе мнение", "локально", "на gpu", "@qwen",
"тяжёлый анализ", "просканируй", "рефактори локально"

## How to invoke

ALWAYS use the MCP tool directly — do NOT spawn a subagent for this.

**Step 1 — Load the tool schema** (required every session before first call):
```
ToolSearch(query="select:mcp__local-agent__delegate_to_local_llm")
```

**Step 2 — Call the tool:**
```
mcp__local-agent__delegate_to_local_llm(prompt=<formatted_prompt>)
```

## Prompt format

Include in every prompt sent to Qwen:
1. **Working directory** — absolute path of current project
2. **Specific task** — what exactly to do (audit / refactor / review)
3. **Scope** — which files or directories to focus on
4. **Output format** — bullet list / markdown table / inline comments

Example:
```
Working directory: /path/to/project
Task: Audit the authentication module for security issues.
Scope: src/auth/
Output: Markdown list of issues with file:line references.
```

## Result handling

| Task type | How Claude processes Qwen output |
|-----------|----------------------------------|
| Code audit | Show raw Qwen report, then add Claude's priority ranking |
| Refactor / implementation | Show Qwen code, Claude verifies tests pass and style matches project |
| Second opinion | Show both outputs side by side, Claude summarises differences |

## Fallback

If `mcp__local-agent__delegate_to_local_llm` returns `"Bridge error: ..."` or
`"All connection attempts failed"`:

1. Tell the user: "Local Qwen server is unavailable."
2. Offer: "I can run this task using Claude instead — shall I proceed?"
3. If yes — execute the same task locally using Claude's own tools.
4. Do NOT retry the MCP tool more than once without user confirmation.

## Server management

Start oMLX: `omlx start`
Check status: `curl -s http://localhost:8000/v1/models`
Change model: update `llm_default` alias in oMLX — `omlx config` — no code changes needed.
