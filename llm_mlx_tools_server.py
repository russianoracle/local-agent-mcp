"""MCP server exposing embedding, reranking, summarize, and cosine similarity tools.

Registered in:
  - llm_tools.json       → Qwen/vllm-mlx can call these tools
  - .gemini/settings.json → Claude Code has direct access

Models are lazy-loaded on first call to avoid startup latency.
  Embedding:  mlx-community/all-MiniLM-L6-v2-4bit  (~20 MB, ANE)
  Reranking:  cross-encoder/ms-marco-MiniLM-L6-v2  (~85 MB, CPU/ANE)
  Summarize:  vllm-mlx Qwen2.5-Coder via HTTP (already running on port 8000)
"""
from __future__ import annotations
import math
import json
import os
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("llm-mlx-tools")

VLLM_URL = os.environ.get("OMLX_URL", "http://localhost:8000")

_embed_model = None
_embed_processor = None
_reranker = None


@mcp.tool()
def embed_texts(
    texts: list[str],
    model: str = "mlx-community/all-MiniLM-L6-v2-4bit",
) -> list[list[float]]:
    """Generate dense vector embeddings for a list of texts.

    Uses mlx-embeddings with a 4-bit quantized MiniLM model on Apple Silicon.
    Returns one float list per input text (384 dimensions for MiniLM-L6).
    """
    global _embed_model, _embed_processor
    if _embed_model is None or _embed_processor is None:
        from mlx_embeddings import load, generate as _gen  # noqa: F401
        _embed_model, _embed_processor = load(model)
    from mlx_embeddings import generate
    output = generate(_embed_model, _embed_processor, texts=texts)
    return output.text_embeds.tolist()


@mcp.tool()
def rerank_documents(
    query: str,
    documents: list[str],
    top_k: int = 5,
) -> list[dict]:
    """Rerank documents by relevance to a query using a CrossEncoder model.

    Returns up to top_k results sorted by descending relevance score.
    Each result: {"index": int, "score": float, "text": str}
    """
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L6-v2")
    ranks = _reranker.rank(query, documents, return_documents=True, top_k=top_k)
    return [
        {"index": int(r["corpus_id"]), "score": float(r["score"]), "text": r["text"]}
        for r in ranks
    ]


@mcp.tool()
async def summarize_text(text: str, max_length: int = 200) -> str:
    """Summarize text using local Qwen 2.5 Coder via vllm-mlx.

    Requires vllm-mlx to be running on localhost:8000. max_length is in words.
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{VLLM_URL}/v1/chat/completions",
            json={
                "model": "default",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a precise summarizer. Output only the summary, no preamble.",
                    },
                    {
                        "role": "user",
                        "content": f"Summarize the following text in at most {max_length} words:\n\n{text}",
                    },
                ],
                "max_tokens": max_length * 2,
                "temperature": 0.3,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


@mcp.tool()
def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two embedding vectors.

    Returns a float in [-1, 1]. Returns 0.0 if either vector is all zeros.
    """
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(x * x for x in vec_a))
    norm_b = math.sqrt(sum(x * x for x in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


if __name__ == "__main__":
    mcp.run()
