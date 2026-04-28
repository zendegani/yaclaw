import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pishkar.core.messages import InboundMessage
from pishkar.triggers.base import TriggerSource
from pishkar.triggers.heartbeat import HeartbeatTrigger


def _write_cron(path: Path, tasks: list[dict]) -> None:
    path.write_text(json.dumps(tasks))


def _collector() -> tuple[list[InboundMessage], "object"]:
    captured: list[InboundMessage] = []

    async def submit(msg: InboundMessage) -> None:
        captured.append(msg)

    return captured, submit


def _task(**overrides) -> dict:
    base = {
        "id": "t1",
        "user_id": "ali",
        "session_id": "s-default",
        "interval_seconds": 3600,
        "prompt": "do the thing",
    }
    base.update(overrides)
    return base


async def test_satisfies_protocol(tmp_path: Path) -> None:
    trig = HeartbeatTrigger(cron_path=tmp_path / "cron.json")
    assert isinstance(trig, TriggerSource)


async def test_no_cron_file_is_silent(tmp_path: Path) -> None:
    trig = HeartbeatTrigger(cron_path=tmp_path / "cron.json")
    captured, submit = _collector()
    assert await trig.tick(submit) == 0
    assert captured == []


async def test_first_tick_fires_never_fired_task(tmp_path: Path) -> None:
    cron = tmp_path / "cron.json"
    _write_cron(cron, [_task()])
    trig = HeartbeatTrigger(cron_path=cron)

    captured, submit = _collector()
    fired = await trig.tick(submit)

    assert fired == 1
    assert len(captured) == 1
    msg = captured[0]
    assert msg.content == "do the thing"
    assert msg.user_id == "ali"
    assert msg.channel == "heartbeat"
    assert msg.metadata["trigger_id"] == "t1"
    assert "was_due_at" in msg.metadata


async def test_persists_last_fired_at(tmp_path: Path) -> None:
    cron = tmp_path / "cron.json"
    _write_cron(cron, [_task()])
    trig = HeartbeatTrigger(cron_path=cron)

    _, submit = _collector()
    await trig.tick(submit)

    saved = json.loads(cron.read_text())
    assert saved[0]["last_fired_at"]
    datetime.fromisoformat(saved[0]["last_fired_at"])  # parses


async def test_does_not_fire_within_interval(tmp_path: Path) -> None:
    cron = tmp_path / "cron.json"
    fixed = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    _write_cron(cron, [_task(last_fired_at=fixed.isoformat())])
    trig = HeartbeatTrigger(
        cron_path=cron, now=lambda: fixed + timedelta(seconds=60)
    )
    captured, submit = _collector()
    assert await trig.tick(submit) == 0
    assert captured == []


async def test_fires_after_interval_elapsed(tmp_path: Path) -> None:
    cron = tmp_path / "cron.json"
    fixed = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    _write_cron(cron, [_task(interval_seconds=60, last_fired_at=fixed.isoformat())])
    later = fixed + timedelta(seconds=120)
    trig = HeartbeatTrigger(cron_path=cron, now=lambda: later)

    captured, submit = _collector()
    assert await trig.tick(submit) == 1
    expected_due = (fixed + timedelta(seconds=60)).isoformat()
    assert captured[0].metadata["was_due_at"] == expected_due


async def test_startup_catchup_for_long_downtime(tmp_path: Path) -> None:
    """Task whose interval elapsed many times during downtime fires once
    on the next tick — agent decides whether to act now or skip stale."""
    cron = tmp_path / "cron.json"
    fixed = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    _write_cron(cron, [_task(interval_seconds=3600, last_fired_at=fixed.isoformat())])
    much_later = fixed + timedelta(days=2)
    trig = HeartbeatTrigger(cron_path=cron, now=lambda: much_later)

    captured, submit = _collector()
    assert await trig.tick(submit) == 1
    assert captured[0].metadata["was_due_at"] == (fixed + timedelta(seconds=3600)).isoformat()


async def test_heartbeat_md_attached_when_present(tmp_path: Path) -> None:
    cron = tmp_path / "cron.json"
    hb = tmp_path / "HEARTBEAT.md"
    _write_cron(cron, [_task()])
    hb.write_text("- 09:00 stand-up\n- 10:00 deploy review\n")
    trig = HeartbeatTrigger(cron_path=cron, heartbeat_path=hb)

    captured, submit = _collector()
    await trig.tick(submit)
    assert "stand-up" in captured[0].metadata["heartbeat_context"]


async def test_invalid_interval_skipped(tmp_path: Path) -> None:
    cron = tmp_path / "cron.json"
    _write_cron(cron, [_task(interval_seconds=0), _task(id="t2", interval_seconds=60)])
    trig = HeartbeatTrigger(cron_path=cron)
    captured, submit = _collector()
    assert await trig.tick(submit) == 1
    assert captured[0].metadata["trigger_id"] == "t2"


async def test_corrupt_cron_is_silent(tmp_path: Path) -> None:
    cron = tmp_path / "cron.json"
    cron.write_text("{not json")
    trig = HeartbeatTrigger(cron_path=cron)
    captured, submit = _collector()
    assert await trig.tick(submit) == 0
    assert captured == []


async def test_run_loops_until_stopped(tmp_path: Path) -> None:
    cron = tmp_path / "cron.json"
    _write_cron(cron, [_task(interval_seconds=1)])
    fixed = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    trig = HeartbeatTrigger(
        cron_path=cron, tick_interval=0.01, now=lambda: fixed
    )
    captured, submit = _collector()

    task = asyncio.create_task(trig.run(submit))
    await asyncio.sleep(0.05)
    await trig.stop()
    await asyncio.wait_for(task, timeout=1.0)

    # Fires once on the first tick; subsequent ticks see fresh last_fired_at
    # equal to `fixed` so they do not re-fire (interval not yet elapsed).
    assert len(captured) == 1
