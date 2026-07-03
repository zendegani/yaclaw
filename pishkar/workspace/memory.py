"""Semantic memory — embedding recall over the conversation log.

The pre-paid seam from the design docs, filled: an `embedding BLOB`
column on `messages`, a background indexer that embeds new rows after
each completed turn, and vector search for the `search_memory` tool.

Embeddings come from any litellm embedding model
(`PISHKAR_EMBEDDING_MODEL`, e.g. `openai/text-embedding-3-small` or
`gemini/text-embedding-004`). Vectors are stored as float32
little-endian blobs — sqlite-vec's native layout. Similarity search
uses sqlite-vec's `vec_distance_cosine` when the extension loads
(`uv sync --extra memory`); otherwise a pure-Python cosine scan, which
is fine for a personal butler's message volume.

Rows whose blob length differs from the query vector's are excluded in
both paths, so switching `PISHKAR_EMBEDDING_MODEL` to a model with a
different dimension degrades to "old rows invisible until re-indexed"
rather than garbage rankings. To force a full re-index:
`UPDATE messages SET embedding = NULL`.
"""

import logging
import math
import struct
from typing import Any, Protocol, runtime_checkable

from pishkar.gateway.hooks import ON_TURN_COMPLETE, HookManager
from pishkar.workspace.store import SessionStore

logger = logging.getLogger(__name__)

# Empty blob = "processed, nothing to embed" (blank/whitespace content).
# Distinguishes skipped rows from NULL (= not yet indexed).
_SKIPPED = b""


@runtime_checkable
class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class LiteLLMEmbedder:
    """Embed via `litellm.aembedding` — any provider litellm supports."""

    def __init__(self, model: str) -> None:
        self._model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import litellm

        resp: Any = await litellm.aembedding(model=self._model, input=texts)
        vectors: list[list[float]] = []
        for item in resp["data"]:
            raw = item["embedding"] if isinstance(item, dict) else item.embedding
            vectors.append([float(x) for x in raw])
        return vectors


def encode_vector(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def decode_vector(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0
    return 1.0 - dot / (norm_a * norm_b)


class MemoryIndex:
    def __init__(
        self,
        store: SessionStore,
        embedder: Embedder,
        *,
        batch_size: int = 32,
        prefer_sqlite_vec: bool = True,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._batch_size = batch_size
        self._prefer_vec = prefer_sqlite_vec
        self._vec_loaded = False
        self._busy = False

    # ---- schema ------------------------------------------------------------

    async def ensure_schema(self) -> None:
        """Add the `embedding` column (idempotent) and try to load sqlite-vec."""
        import aiosqlite

        try:
            await self._store.db.execute(
                "ALTER TABLE messages ADD COLUMN embedding BLOB"
            )
            await self._store.db.commit()
        except aiosqlite.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
        if self._prefer_vec:
            self._vec_loaded = await self._try_load_sqlite_vec()

    async def _try_load_sqlite_vec(self) -> bool:
        try:
            import sqlite_vec  # type: ignore[import-untyped]
        except ImportError:
            return False
        try:
            await self._store.db.enable_load_extension(True)
            await self._store.db.load_extension(sqlite_vec.loadable_path())
            await self._store.db.enable_load_extension(False)
        except Exception:  # noqa: BLE001 — fall back to the Python scan
            logger.info("sqlite-vec could not be loaded; using Python cosine scan")
            return False
        return True

    # ---- indexing ------------------------------------------------------------

    def attach(self, hooks: HookManager) -> None:
        hooks.on(ON_TURN_COMPLETE, self._on_turn_complete)

    async def _on_turn_complete(self, **_: Any) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            await self.index_pending()
        except Exception:  # noqa: BLE001 — fail-open, like every hook consumer
            logger.exception("memory indexing failed; will retry next turn")
        finally:
            self._busy = False

    async def index_pending(self, limit: int = 200) -> int:
        """Embed messages that don't have a vector yet. Returns rows indexed."""
        async with self._store.db.execute(
            "SELECT message_id, content FROM messages "
            "WHERE embedding IS NULL ORDER BY timestamp LIMIT ?",
            (limit,),
        ) as cur:
            rows = list(await cur.fetchall())
        if not rows:
            return 0

        indexed = 0
        for start in range(0, len(rows), self._batch_size):
            batch = rows[start : start + self._batch_size]
            texts = [(r[1] or "").strip() for r in batch]
            to_embed = [t for t in texts if t]
            vectors = await self._embedder.embed(to_embed) if to_embed else []
            it = iter(vectors)
            for (message_id, _), text in zip(batch, texts, strict=True):
                blob = encode_vector(next(it)) if text else _SKIPPED
                await self._store.db.execute(
                    "UPDATE messages SET embedding = ? WHERE message_id = ?",
                    (blob, message_id),
                )
                indexed += 1
            await self._store.db.commit()
        return indexed

    # ---- search --------------------------------------------------------------

    async def search(
        self, query: str, *, k: int = 5, user_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Top-k messages by cosine similarity to `query`."""
        [query_vec] = await self._embedder.embed([query])
        blob = encode_vector(query_vec)
        if self._vec_loaded:
            return await self._search_vec(blob, k=k, user_id=user_id)
        return await self._search_python(query_vec, len(blob), k=k, user_id=user_id)

    async def _search_vec(
        self, blob: bytes, *, k: int, user_id: str | None
    ) -> list[dict[str, Any]]:
        user_clause = "AND user_id = ?" if user_id else ""
        params: list[Any] = [blob, len(blob)]
        if user_id:
            params.append(user_id)
        params.append(k)
        async with self._store.db.execute(
            f"SELECT content, session_id, direction, timestamp, "
            f"       vec_distance_cosine(embedding, ?) AS distance "
            f"FROM messages "
            f"WHERE embedding IS NOT NULL AND length(embedding) = ? {user_clause} "
            f"ORDER BY distance LIMIT ?",
            params,
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_result(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    async def _search_python(
        self, query_vec: list[float], blob_len: int, *, k: int, user_id: str | None
    ) -> list[dict[str, Any]]:
        user_clause = "AND user_id = ?" if user_id else ""
        params: list[Any] = [blob_len]
        if user_id:
            params.append(user_id)
        async with self._store.db.execute(
            f"SELECT content, session_id, direction, timestamp, embedding "
            f"FROM messages "
            f"WHERE embedding IS NOT NULL AND length(embedding) = ? {user_clause}",
            params,
        ) as cur:
            rows = await cur.fetchall()
        scored = sorted(
            (
                (
                    _cosine_distance(query_vec, decode_vector(r[4])),
                    r,
                )
                for r in rows
            ),
            key=lambda pair: pair[0],
        )
        return [
            self._row_to_result(r[0], r[1], r[2], r[3], distance)
            for distance, r in scored[:k]
        ]

    @staticmethod
    def _row_to_result(
        content: str, session_id: str, direction: str, timestamp: str, distance: float
    ) -> dict[str, Any]:
        return {
            "content": content,
            "session_id": session_id,
            "direction": direction,
            "timestamp": timestamp,
            "distance": float(distance),
        }


__all__ = [
    "Embedder",
    "LiteLLMEmbedder",
    "MemoryIndex",
    "decode_vector",
    "encode_vector",
]
