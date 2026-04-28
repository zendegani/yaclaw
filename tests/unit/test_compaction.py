from pishkar.core.compaction import compact


def _u(text: str) -> dict:
    return {"role": "user", "content": text}


def _a(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _ac(call_id: str, name: str = "bash") -> dict:
    return {
        "role": "assistant",
        "tool_calls": [{"id": call_id, "type": "function",
                        "function": {"name": name, "arguments": "{}"}}],
    }


def _t(call_id: str, content: str = "ok") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _s(text: str) -> dict:
    return {"role": "system", "content": text}


def test_below_max_returns_unchanged() -> None:
    msgs = [_u("a"), _a("b")]
    assert compact(msgs, max_messages=10) == msgs


def test_drops_oldest_assistant_prose_first() -> None:
    msgs = [_u("u1"), _a("a1"), _u("u2"), _a("a2"), _u("u3")]
    out = compact(msgs, max_messages=4)
    # a1 (oldest assistant prose) dropped
    assert _a("a1") not in out
    assert _u("u3") in out


def test_preserves_tool_call_pairs_when_possible() -> None:
    msgs = [_u("u1"), _ac("c1"), _t("c1"), _a("prose"), _u("u2")]
    out = compact(msgs, max_messages=4)
    # 'prose' (assistant prose) is dropped first, leaving the tool pair intact
    assert _ac("c1") in out and _t("c1") in out
    assert _a("prose") not in out


def test_drops_tool_call_pair_together_when_needed() -> None:
    msgs = [_u("u1"), _ac("c1"), _t("c1"), _u("u2"), _ac("c2"), _t("c2"), _u("u3")]
    out = compact(msgs, max_messages=5)
    # The earliest tool pair (c1) should be dropped as a unit
    ids = [m.get("tool_call_id") for m in out if m["role"] == "tool"]
    assert "c1" not in ids
    assert "c2" in ids


def test_never_drops_system_messages() -> None:
    msgs = [_s("sys"), _u("u1"), _a("a1"), _a("a2"), _a("a3"), _u("u2")]
    out = compact(msgs, max_messages=3)
    assert _s("sys") in out


def test_never_drops_last_user_message() -> None:
    msgs = [_a("a1"), _a("a2"), _a("a3"), _u("last")]
    out = compact(msgs, max_messages=2)
    assert _u("last") in out
