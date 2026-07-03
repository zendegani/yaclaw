import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from pishkar.core.messages import InboundMessage
from pishkar.triggers.webhook import (
    PAYLOAD_CAP,
    build_webhook_router,
    load_webhook_config,
)


def _write_config(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps(entries), encoding="utf-8")


def _make_client(config_path: Path) -> tuple[TestClient, list[InboundMessage]]:
    submitted: list[InboundMessage] = []

    async def submit(msg: InboundMessage) -> None:
        submitted.append(msg)

    app = FastAPI()
    app.include_router(build_webhook_router(submit, config_path))
    return TestClient(app), submitted


def _entry(**overrides: object) -> dict:
    entry: dict = {"name": "gh", "user_id": "ali", "secret": "s3cret"}
    entry.update(overrides)
    return entry


# ---- config loading --------------------------------------------------------


def test_missing_config_file_yields_no_entries(tmp_path: Path) -> None:
    assert load_webhook_config(tmp_path / "webhooks.json") == {}


def test_malformed_config_yields_no_entries(tmp_path: Path) -> None:
    path = tmp_path / "webhooks.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_webhook_config(path) == {}


def test_entry_without_secret_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "webhooks.json"
    _write_config(path, [{"name": "gh", "user_id": "ali"}])
    assert load_webhook_config(path) == {}


# ---- route behavior --------------------------------------------------------


def test_valid_secret_submits_message(tmp_path: Path) -> None:
    path = tmp_path / "webhooks.json"
    _write_config(path, [_entry(prompt="CI finished.")])
    client, submitted = _make_client(path)

    resp = client.post(
        "/webhook/gh",
        headers={"X-Pishkar-Secret": "s3cret"},
        json={"status": "green"},
    )

    assert resp.status_code == 202
    assert len(submitted) == 1
    msg = submitted[0]
    assert resp.json() == {"status": "accepted", "message_id": msg.message_id}
    assert msg.user_id == "ali"
    assert msg.channel == "webhook"
    assert msg.session_id == "webhook-gh"
    assert msg.trust_level == "untrusted"
    assert msg.metadata == {"webhook_name": "gh"}
    assert "CI finished." in msg.content
    assert '"status": "green"' in msg.content


def test_wrong_secret_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "webhooks.json"
    _write_config(path, [_entry()])
    client, submitted = _make_client(path)

    resp = client.post("/webhook/gh", headers={"X-Pishkar-Secret": "nope"})
    assert resp.status_code == 403
    assert submitted == []


def test_missing_secret_header_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "webhooks.json"
    _write_config(path, [_entry()])
    client, submitted = _make_client(path)

    resp = client.post("/webhook/gh")
    assert resp.status_code == 403
    assert submitted == []


def test_unknown_hook_is_404(tmp_path: Path) -> None:
    path = tmp_path / "webhooks.json"
    _write_config(path, [_entry()])
    client, submitted = _make_client(path)

    resp = client.post("/webhook/ghost", headers={"X-Pishkar-Secret": "s3cret"})
    assert resp.status_code == 404
    assert submitted == []


def test_empty_body_omits_payload_block(tmp_path: Path) -> None:
    path = tmp_path / "webhooks.json"
    _write_config(path, [_entry()])
    client, submitted = _make_client(path)

    resp = client.post("/webhook/gh", headers={"X-Pishkar-Secret": "s3cret"})
    assert resp.status_code == 202
    assert submitted[0].content == "Webhook 'gh' fired."
    assert "Payload" not in submitted[0].content


def test_non_json_body_is_embedded_verbatim(tmp_path: Path) -> None:
    path = tmp_path / "webhooks.json"
    _write_config(path, [_entry()])
    client, submitted = _make_client(path)

    client.post(
        "/webhook/gh",
        headers={"X-Pishkar-Secret": "s3cret"},
        content=b"plain text ping",
    )
    assert "plain text ping" in submitted[0].content


def test_oversized_payload_is_truncated(tmp_path: Path) -> None:
    path = tmp_path / "webhooks.json"
    _write_config(path, [_entry()])
    client, submitted = _make_client(path)

    client.post(
        "/webhook/gh",
        headers={"X-Pishkar-Secret": "s3cret"},
        content=b"x" * (PAYLOAD_CAP * 2),
    )
    assert "[truncated]" in submitted[0].content
    assert len(submitted[0].content) < PAYLOAD_CAP + 200


def test_entry_overrides_session_and_trust(tmp_path: Path) -> None:
    path = tmp_path / "webhooks.json"
    _write_config(
        path, [_entry(session_id="s-main", trust_level="limited")]
    )
    client, submitted = _make_client(path)

    client.post("/webhook/gh", headers={"X-Pishkar-Secret": "s3cret"})
    assert submitted[0].session_id == "s-main"
    assert submitted[0].trust_level == "limited"


def test_invalid_trust_level_falls_back_to_untrusted(tmp_path: Path) -> None:
    path = tmp_path / "webhooks.json"
    _write_config(path, [_entry(trust_level="root")])
    client, submitted = _make_client(path)

    client.post("/webhook/gh", headers={"X-Pishkar-Secret": "s3cret"})
    assert submitted[0].trust_level == "untrusted"


def test_config_edits_apply_without_restart(tmp_path: Path) -> None:
    path = tmp_path / "webhooks.json"
    _write_config(path, [_entry()])
    client, submitted = _make_client(path)

    assert client.post(
        "/webhook/ha", headers={"X-Pishkar-Secret": "other"}
    ).status_code == 404

    _write_config(path, [_entry(), _entry(name="ha", secret="other")])
    assert client.post(
        "/webhook/ha", headers={"X-Pishkar-Secret": "other"}
    ).status_code == 202
    assert submitted[-1].session_id == "webhook-ha"
