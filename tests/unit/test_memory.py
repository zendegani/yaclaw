from pathlib import Path

import pytest

import pishkar.tools.memory as memory_tool
from pishkar.core.context import current_user_id
from pishkar.core.messages import InboundMessage, OutboundMessage
from pishkar.gateway.hooks import ON_TURN_COMPLETE, HookManager
from pishkar.workspace.memory import (
    Embedder,
    MemoryIndex,
    decode_vector,
    encode_vector,
)
from pishkar.workspace.store import SessionStore

# Deterministic 3-d embeddings keyed on distinctive substrings; anything
# unknown lands on a far-away default vector.
_VECTORS = {
    "pizza": [1.0, 0.0, 0.0],
    "food": [0.9, 0.1, 0.0],
    "server": [0.0, 1.0, 0.0],
}
_DEFAULT = [0.0, 0.0, 1.0]


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        out = []
        for text in texts:
            vec = next(
                (v for key, v in _VECTORS.items() if key in text.lower()), _DEFAULT
            )
            out.append(list(vec))
        return out


@pytest.fixture
async def store(tmp_path: Path):
    s = SessionStore(tmp_path / "sessions.db")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


async def _index(store: SessionStore) -> tuple[MemoryIndex, FakeEmbedder]:
    embedder = FakeEmbedder()
    index = MemoryIndex(store, embedder, prefer_sqlite_vec=False)
    await index.ensure_schema()
    return index, embedder


async def _add_inbound(store: SessionStore, content: str, *, user_id: str = "ali") -> str:
    msg = InboundMessage(
        user_id=user_id, session_id="s1", channel="test", content=content
    )
    await store.enqueue_inbound(msg)
    return msg.message_id


def test_vector_roundtrip() -> None:
    vec = [0.25, -1.5, 3.0]
    assert decode_vector(encode_vector(vec)) == vec


def test_fake_embedder_satisfies_protocol() -> None:
    assert isinstance(FakeEmbedder(), Embedder)


async def test_ensure_schema_is_idempotent(store: SessionStore) -> None:
    index, _ = await _index(store)
    await index.ensure_schema()  # second run must not raise
    async with store.db.execute("PRAGMA table_info(messages)") as cur:
        cols = [r[1] for r in await cur.fetchall()]
    assert "embedding" in cols


async def test_index_pending_embeds_new_messages(store: SessionStore) -> None:
    index, embedder = await _index(store)
    await _add_inbound(store, "I love pizza")
    await _add_inbound(store, "restart the server")

    assert await index.index_pending() == 2
    assert await index.index_pending() == 0  # nothing left
    assert embedder.calls == [["I love pizza", "restart the server"]]


async def test_blank_messages_marked_skipped_not_reembedded(
    store: SessionStore,
) -> None:
    index, embedder = await _index(store)
    await _add_inbound(store, "   ")

    assert await index.index_pending() == 1
    assert await index.index_pending() == 0
    assert embedder.calls == [[]] or embedder.calls == []


async def test_search_ranks_by_similarity(store: SessionStore) -> None:
    index, _ = await _index(store)
    await _add_inbound(store, "I love pizza margherita")
    await _add_inbound(store, "restart the pi server tonight")
    await index.index_pending()

    results = await index.search("what food do I like", k=1)
    assert len(results) == 1
    assert "pizza" in results[0]["content"]
    assert results[0]["distance"] < 0.5


async def test_search_filters_by_user(store: SessionStore) -> None:
    index, _ = await _index(store)
    await _add_inbound(store, "ali likes pizza", user_id="ali")
    await _add_inbound(store, "bob likes pizza too", user_id="bob")
    await index.index_pending()

    results = await index.search("food", k=5, user_id="ali")
    assert [r["content"] for r in results] == ["ali likes pizza"]


async def test_search_skips_mismatched_dimensions(store: SessionStore) -> None:
    index, _ = await _index(store)
    mid = await _add_inbound(store, "old-model pizza row")
    # Simulate a row embedded by a model with a different dimension.
    await store.db.execute(
        "UPDATE messages SET embedding = ? WHERE message_id = ?",
        (encode_vector([1.0, 0.0]), mid),
    )
    await store.db.commit()

    assert await index.search("pizza", k=5) == []


async def test_outbound_messages_are_indexed_too(store: SessionStore) -> None:
    index, _ = await _index(store)
    await store.record_outbound(
        OutboundMessage(
            user_id="ali", session_id="s1", channel="test",
            content="Your pizza order is confirmed",
        )
    )
    await index.index_pending()

    results = await index.search("pizza", k=1)
    assert results and results[0]["direction"] == "outbound"


async def test_turn_complete_hook_triggers_indexing(store: SessionStore) -> None:
    index, _ = await _index(store)
    await _add_inbound(store, "I love pizza")
    hooks = HookManager()
    index.attach(hooks)

    hooks.emit(ON_TURN_COMPLETE, turn_id="t1", session_id="s1",
               user_id="ali", stop_reason="end_turn")
    await hooks.drain()

    assert await index.index_pending() == 0  # already done by the hook


async def test_sqlite_vec_path_matches_python_ranking(store: SessionStore) -> None:
    pytest.importorskip("sqlite_vec", reason="sqlite-vec not installed")
    embedder = FakeEmbedder()
    index = MemoryIndex(store, embedder, prefer_sqlite_vec=True)
    await index.ensure_schema()
    if not index._vec_loaded:
        pytest.skip("sqlite extension loading unavailable in this Python build")
    await _add_inbound(store, "I love pizza margherita")
    await _add_inbound(store, "restart the pi server tonight")
    await index.index_pending()

    results = await index.search("what food do I like", k=2)
    assert "pizza" in results[0]["content"]


# ---- the `search_memory` tool ----------------------------------------------


async def test_tool_unconfigured_explains_setup() -> None:
    memory_tool.configure(None)
    result = await memory_tool.search_memory("anything")
    assert "PISHKAR_EMBEDDING_MODEL" in result


async def test_tool_formats_results(store: SessionStore) -> None:
    index, _ = await _index(store)
    await _add_inbound(store, "I love pizza margherita")
    await index.index_pending()
    memory_tool.configure(index)
    token = current_user_id.set("ali")
    try:
        result = await memory_tool.search_memory("food I like", k=3)
    finally:
        current_user_id.reset(token)
        memory_tool.configure(None)
    assert "User: I love pizza margherita" in result


async def test_tool_reports_no_matches(store: SessionStore) -> None:
    index, _ = await _index(store)
    memory_tool.configure(index)
    try:
        result = await memory_tool.search_memory("anything at all")
    finally:
        memory_tool.configure(None)
    assert result == "No matching messages found."
