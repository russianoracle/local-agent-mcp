import asyncio
import json
import os
import httpx
import sys
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from pydantic import Field
from mcp.server.fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations

OMLX_URL = os.environ.get("OMLX_URL", "http://localhost:8000")
OMLX_API_KEY = os.environ.get("OMLX_API_KEY", "1986")
AUTH_HEADERS = {"Authorization": f"Bearer {OMLX_API_KEY}"}

# Model alias — change on the oMLX server to switch models without touching code
MODEL_ID = "llm_default"

MAX_TOOL_ITERS = 10

# Fallback limits if admin API is unavailable
_DEFAULT_MAX_TOKENS = 4096
_omlx_limits_cache: tuple[int, int, float] | None = None
_DEFAULT_CONTEXT_BUDGET = 112_000  # ~32k tokens * 3.5 chars/token
_DEFAULT_TEMPERATURE = 0.3
_MAX_TOOL_RESULT_CHARS = 16_000
_MAX_PROMPT_CHARS = 80_000

SYSTEM_PROMPT = (
    "You are a professional code auditor running locally on M3 Pro GPU.\n"
    "Rules:\n"
    "1. If code, logs, or config are already present in the task (in triple backticks or similar), "
    "analyze them directly — do NOT call filesystem tools for content you already have.\n"
    "2. If the task references a file path, call filesystem__read_file ONCE for that path. "
    "If the file does not exist, immediately say so and ask the user to provide the content — "
    "do NOT retry with variations or search for alternatives.\n"
    "3. Read each file at most once. Do NOT re-read a file you have already read.\n"
    "4. Do not invent field names, endpoint paths, or schema details — only report what you read.\n"
    "5. If file content is truncated, note '[TRUNCATED]' and describe only what you saw.\n"
    "6. Cite exact field names, line numbers, and file names from the actual content.\n"
    "7. Your task is analysis only — do NOT write or modify any files."
)

WRITE_SYSTEM_PROMPT = (
    "You are a file manipulation assistant running locally on M3 Pro GPU.\n"
    "Rules:\n"
    "- Execute tool calls directly — NEVER generate template expressions "
    "like {{tool(arg)}} or {{filesystem__read_text_file('path')}}. Those are not tool calls.\n"
    "- For read-then-write tasks: call the read tool first, then call filesystem__write_file.\n"
    "- Write each requested file EXACTLY ONCE. Do not write the same file twice.\n"
    "- Do not expand scope: only write what was explicitly requested.\n"
    "- Confirm each written file with: DONE: <filepath>"
)

_AUDIT_TOOL_PREFIXES = ("filesystem__read",)
_WRITE_TOOL_PREFIXES = ("filesystem__",)

# Long read timeout for thinking models (8192 thinking + 4096 output ≈ 500s at 24 tok/s)
_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)


async def _alog(msg: str, ctx: Context | None = None) -> None:
    """Log to stderr always; also forward to MCP host as info log when ctx available."""
    print(msg, file=sys.stderr)
    if ctx:
        try:
            await ctx.info(msg)
        except Exception as e:
            print(f"[bridge] ctx.info failed: {e}", file=sys.stderr)


async def _ensure_server() -> None:
    async with httpx.AsyncClient() as c:
        try:
            r = await c.get(f"{OMLX_URL}/health", headers=AUTH_HEADERS, timeout=3.0)
            if r.status_code < 500:
                return
        except Exception:
            pass
    raise RuntimeError("oMLX not running on :8000. Start with: omlx start")


async def _load_omlx_limits() -> tuple[int, int, float]:
    """Return (max_tokens, context_budget_chars, temperature) from oMLX admin API.

    Result is cached after the first successful call — admin login is not repeated.
    """
    global _omlx_limits_cache
    if _omlx_limits_cache is not None:
        return _omlx_limits_cache
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            login = await c.post(
                f"{OMLX_URL}/admin/api/login",
                json={"api_key": OMLX_API_KEY},
            )
            cookies = login.cookies
            settings = await c.get(
                f"{OMLX_URL}/admin/api/global-settings",
                cookies=cookies,
            )
            data = settings.json()
            sampling = data.get("sampling", {})
            max_ctx = int(sampling.get("max_context_window", 32768))
            max_tok = min(4096, int(sampling.get("max_tokens", _DEFAULT_MAX_TOKENS)))
            temp = float(sampling.get("temperature", _DEFAULT_TEMPERATURE))
            budget = int(max_ctx * 3.5)
            print(f"[bridge] oMLX limits: max_tokens={max_tok}, context_budget={budget:,} chars, temp={temp}", file=sys.stderr)
            _omlx_limits_cache = (max_tok, budget, temp)
            return _omlx_limits_cache
    except Exception as e:
        print(f"[bridge] admin API unavailable ({e}), using defaults", file=sys.stderr)
        return _DEFAULT_MAX_TOKENS, _DEFAULT_CONTEXT_BUDGET, _DEFAULT_TEMPERATURE


async def _stream_chat(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict,
    model_id: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int,
    temperature: float,
    enable_thinking: bool = False,
    ctx: Context | None = None,
) -> dict | str:
    """Stream a chat completion and aggregate chunks into a complete message dict.

    Returns the assembled message dict on success, or an error string on failure.
    Streaming avoids 'incomplete chunked read' errors from long thinking-model outputs.
    Progress notifications (report_progress) sent every 50 tokens for progress bar.
    Info logs (ctx.info) sent every 200 tokens for visible text updates in CC UI.
    """
    content_parts: list[str] = []
    # tool_calls[index] = {"id": ..., "function": {"name": ..., "arguments": ""}}
    tool_calls_acc: dict[int, dict] = {}
    tokens_seen = 0

    body: dict = {
        "model": model_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "chat_template_kwargs": {"enable_thinking": enable_thinking},
    }
    if tools:
        body["tools"] = tools
    else:
        body["tool_choice"] = "none"

    if ctx:
        try:
            await ctx.info(f"[bridge] generating… model={model_id} max_tokens={max_tokens}")
        except Exception:
            pass

    async with client.stream(
        "POST",
        f"{base_url}/v1/chat/completions",
        headers=headers,
        json=body,
    ) as resp:
        if resp.status_code != 200:
            err_bytes = await resp.aread()
            return f"Error from oMLX: {err_bytes.decode()[:500]}"

        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            # Accumulate text content; emit progress bar every 50 tok, info log every 200 tok
            if delta.get("content"):
                content_parts.append(delta["content"])
                tokens_seen += 1
                if ctx and tokens_seen % 50 == 0:
                    try:
                        await ctx.report_progress(tokens_seen, max_tokens,
                                                  f"streaming… {tokens_seen} tok")
                    except Exception:
                        pass
                if ctx and tokens_seen % 200 == 0:
                    try:
                        await ctx.info(f"[bridge] ▶ {tokens_seen}/{max_tokens} tok")
                    except Exception:
                        pass
            # Accumulate tool_calls by index
            for tc_delta in delta.get("tool_calls", []):
                idx = tc_delta.get("index", 0)
                if idx not in tool_calls_acc:
                    tool_calls_acc[idx] = {
                        "id": tc_delta.get("id", f"call_{idx}"),
                        "type": "function",
                        "function": {"name": tc_delta.get("function", {}).get("name", ""), "arguments": ""},
                    }
                else:
                    if tc_delta.get("id"):
                        tool_calls_acc[idx]["id"] = tc_delta["id"]
                    fn = tc_delta.get("function", {})
                    if fn.get("name"):
                        tool_calls_acc[idx]["function"]["name"] = fn["name"]
                tool_calls_acc[idx]["function"]["arguments"] += tc_delta.get("function", {}).get("arguments", "")

    tool_calls_list = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
    if ctx:
        try:
            if tool_calls_list:
                names = ", ".join(t["function"]["name"] for t in tool_calls_list)
                await ctx.info(f"[bridge] done streaming — calling tools: {names}")
            else:
                await ctx.info(f"[bridge] done streaming — {tokens_seen} tok, final answer")
        except Exception:
            pass
    msg: dict = {"role": "assistant", "content": "".join(content_parts) or None}
    if tool_calls_list:
        msg["tool_calls"] = tool_calls_list
    return msg



@asynccontextmanager
async def _lifespan(_app: FastMCP):  # type: ignore[type-arg]  # noqa: F841
    """Maintain a single persistent httpx client across all tool calls."""
    async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
        yield {"http": client}


mcp = FastMCP(
    "local-agent",
    instructions="Delegate code audit and file-write tasks to local Qwen 2.5 on M3 Pro GPU via oMLX.",
    lifespan=_lifespan,
)


@mcp._mcp_server.set_logging_level()  # only route to declare `logging` in serverCapabilities
async def _handle_set_log_level(level: str) -> None:  # pragma: no cover  # noqa: F841
    print(f"[bridge] log level set to {level}", file=sys.stderr)


@mcp.resource("health://status")
async def get_health_status(ctx: Context | None = None) -> str:
    """Get the current health status of the local Qwen bridge."""
    shared_client: httpx.AsyncClient | None = None
    if ctx is not None:
        try:
            shared_client = ctx.request_context.lifespan_context.get("http")
        except Exception:
            pass

    client = shared_client or httpx.AsyncClient()
    try:
        r = await client.get(f"{OMLX_URL}/health", headers=AUTH_HEADERS, timeout=3.0)
        if r.status_code == 200:
            return f"Healthy: oMLX running at {OMLX_URL}"
        return f"Warning: oMLX returned status {r.status_code}"
    except Exception as e:
        return f"Unavailable: oMLX not running or unreachable ({e}). Start with: omlx start"
    finally:
        if shared_client is None:
            await client.aclose()


@mcp.tool(annotations=ToolAnnotations(openWorldHint=False, idempotentHint=True, destructiveHint=False))
async def check_bridge_health(ctx: Context | None = None) -> str:
    """Check the health status of the local Qwen bridge and oMLX server."""
    return await get_health_status(ctx)


@mcp.tool(annotations=ToolAnnotations(openWorldHint=True, idempotentHint=False, destructiveHint=False))
async def delegate_to_local_llm(
    prompt: Annotated[str, Field(
        description=(
            "Task description for Qwen. If you already have the code, logs, or config "
            "content in your context, wrap it in triple backticks and include it here — "
            "do not pass a file path for content you already hold. Use absolute file paths "
            "only for large on-disk files (>10k chars) that actually exist."
        )
    )],
    mode: Annotated[Literal["audit", "write"], Field(
        default="audit",
        description=(
            "'audit': code review, analysis, Q&A. Inline content or pass absolute file paths. "
            "'write': file creation or manipulation. Call once per generation request."
        )
    )] = "audit",
    ctx: Context | None = None,
) -> str:
    """Delegate a task to local Qwen on M3 Pro GPU via oMLX.

    The model has access to filesystem, fetch, and llm-mlx-tools (embed/rerank/summarize).
    Runs a multi-turn tool loop until a final text answer is produced.
    Live progress and log messages are streamed to the MCP host during generation.

    CALLER RULES — read before using:
    - Call this tool ONCE per task. Do NOT retry after a timeout or "Bridge error" — report
      the limitation to the user instead. Retrying causes redundant work and context bloat.
    - For audit: call once and present the result. Only call a second time if the user
      explicitly requests a follow-up or narrower scope.
    - For write: call once per generation request. Do not call again before the user reviews
      the first output.
    - If the result says "Max iterations reached" or "Context budget exhausted", break the
      task into smaller subtasks rather than re-calling with the same prompt.
    - If you already have the code, document, or log content in your context, INLINE IT in
      the prompt (wrapped in triple backticks). Do not pass a file path and expect Qwen to
      fetch content you already hold — that causes timeouts when the path doesn't exist.

    Args:
        prompt: Task description. Include inline content (code, logs, config) in triple
                backticks when you already have it. Use file paths only for files that
                actually exist on disk and are too large to inline (<80k chars total).
        mode:   "audit" (default) — code review, analysis, Q&A.
                    If you have the content, inline it. If not, pass the absolute file path.
                "write" — file creation or manipulation.
                    For files < ~10k chars each, inlining content in the prompt is
                    more reliable than path-based delegation.
    """
    if len(prompt) > _MAX_PROMPT_CHARS:
        return (
            f"Bridge error: prompt too large ({len(prompt):,} chars, limit {_MAX_PROMPT_CHARS:,}). "
            "Pass file paths only — do not inline file contents."
        )
    try:
        await _ensure_server()
    except RuntimeError as e:
        return f"Local Qwen unavailable: {e}"

    max_tokens, context_budget, temperature = await _load_omlx_limits()
    system_prompt = WRITE_SYSTEM_PROMPT if mode == "write" else SYSTEM_PROMPT

    # When the prompt already contains inline content (triple-backtick blocks), filesystem
    # tools are unnecessary and can cause timeout loops on non-existent paths.
    _has_inline_content = "```" in prompt

    # Use lifespan-shared client (ctx.request_context is a public Context property)
    shared_client: httpx.AsyncClient | None = None
    if ctx is not None:
        try:
            shared_client = ctx.request_context.lifespan_context.get("http")
        except Exception:
            pass

    async def _run(client: httpx.AsyncClient) -> str:
        try:
            # Inline audit: skip tool fetch entirely — tool_choice="none" will be sent
            if _has_inline_content and mode == "audit":
                tools: list[dict] = []
                await _alog(f"[bridge] mode={mode} inline content detected — tools suppressed", ctx)
            else:
                tools_resp = await client.get(
                    f"{OMLX_URL}/v1/mcp/tools", headers=AUTH_HEADERS
                )
                raw_tools = tools_resp.json().get("tools", [])
                prefixes = _AUDIT_TOOL_PREFIXES if mode == "audit" else _WRITE_TOOL_PREFIXES
                tools = [
                    {
                        "type": "function",
                        "function": {
                            "name": t["name"],
                            "description": t.get("description", ""),
                            "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                        },
                    }
                    for t in raw_tools
                    if any(t["name"].startswith(p) for p in prefixes)
                ]
                await _alog(f"[bridge] mode={mode} tools={len(tools)} ({[t['function']['name'] for t in tools]})", ctx)

            await _alog(f"[bridge] using model: {MODEL_ID}", ctx)

            messages: list[dict] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
            accumulated_tool_chars = 0
            _last_tool_sig: str | None = None  # detect stuck loops

            # Defined once here — captures client/ctx from _run scope, not per-iteration
            async def _exec_tool(tc: dict) -> dict:
                fname: str = tc["function"]["name"]
                try:
                    args: dict = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError as exc:
                    await _alog(f"[bridge] arg parse error ({fname}): {exc}", ctx)
                    args = {}
                await _alog(f"[bridge] tool_call: {fname}({json.dumps(args)[:120]})", ctx)
                exec_resp = await client.post(
                    f"{OMLX_URL}/v1/mcp/execute",
                    headers=AUTH_HEADERS,
                    json={"tool_name": fname, "arguments": args},
                    timeout=60.0,
                )
                exec_data = exec_resp.json()
                tool_result = exec_data.get("content", json.dumps(exec_data))
                content = tool_result if isinstance(tool_result, str) else json.dumps(tool_result)
                if len(content) > _MAX_TOOL_RESULT_CHARS:
                    await _alog(f"[bridge] tool result truncated: {len(content):,} → {_MAX_TOOL_RESULT_CHARS:,}", ctx)
                    content = content[:_MAX_TOOL_RESULT_CHARS] + f"\n[... truncated {len(content) - _MAX_TOOL_RESULT_CHARS:,} chars ...]"
                return {"role": "tool", "tool_call_id": tc["id"], "content": content}

            for i in range(MAX_TOOL_ITERS):
                await _alog(f"[bridge] iter {i + 1}/{MAX_TOOL_ITERS}", ctx)
                msg = await _stream_chat(
                    client, OMLX_URL, AUTH_HEADERS, MODEL_ID,
                    messages, tools, max_tokens, temperature,
                    enable_thinking=False, ctx=ctx,
                )
                if isinstance(msg, str):
                    return msg

                if not msg.get("tool_calls"):
                    return msg.get("content") or ""

                # Detect stuck loop: same tool+args as previous iteration (after budget check)
                current_sig = json.dumps(
                    [{"n": tc["function"]["name"], "a": tc["function"]["arguments"]}
                     for tc in msg["tool_calls"]], sort_keys=True
                )

                messages.append(msg)
                tool_results = await asyncio.gather(*[_exec_tool(tc) for tc in msg["tool_calls"]])
                for r in tool_results:
                    accumulated_tool_chars += len(r["content"])
                await _alog(f"[bridge] context budget: {accumulated_tool_chars:,}/{context_budget:,} chars", ctx)
                if accumulated_tool_chars > context_budget:
                    return (
                        f"Context budget exhausted after {accumulated_tool_chars:,} chars of tool results "
                        f"(limit {context_budget:,}). Break the task into smaller subtasks."
                    )

                if current_sig == _last_tool_sig:
                    return (
                        "Stopped: model repeated the same tool call with identical arguments. "
                        "No progress was being made. Break the task into smaller steps."
                    )
                _last_tool_sig = current_sig

                messages.extend(tool_results)

            return "Max iterations reached without a final answer."

        except httpx.RemoteProtocolError as e:
            return (
                f"Bridge error: oMLX closed the connection mid-response ({e}). "
                "Try a shorter prompt or check oMLX memory usage."
            )
        except Exception as e:
            return f"Bridge error: {e}"

    if shared_client is not None:
        return await _run(shared_client)
    async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as fallback_client:
        return await _run(fallback_client)


if __name__ == "__main__":
    mcp.run()
