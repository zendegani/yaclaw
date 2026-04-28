from pishkar.core.loop_guard import LoopGuard


def test_is_looping_trips_after_threshold() -> None:
    g = LoopGuard(window=10, threshold=3)
    assert not g.is_looping("bash", {"cmd": "ls"})
    g.record("bash", {"cmd": "ls"})
    assert not g.is_looping("bash", {"cmd": "ls"})
    g.record("bash", {"cmd": "ls"})
    assert g.is_looping("bash", {"cmd": "ls"})  # 3rd call would make it 3


def test_different_args_do_not_count() -> None:
    g = LoopGuard(window=10, threshold=3)
    g.record("bash", {"cmd": "ls"})
    g.record("bash", {"cmd": "pwd"})
    assert not g.is_looping("bash", {"cmd": "ls"})


def test_window_evicts_old_entries() -> None:
    g = LoopGuard(window=4, threshold=3)
    for _ in range(2):
        g.record("bash", {"cmd": "ls"})  # 2 occurrences in history
    for _ in range(3):
        g.record("bash", {"cmd": "other"})  # pushes 'ls' out of window
    assert not g.is_looping("bash", {"cmd": "ls"})


def test_per_tool_threshold_overrides_default() -> None:
    g = LoopGuard(window=20, threshold=5, per_tool_threshold={"poll": 100})
    for _ in range(10):
        g.record("poll", {"x": 1})
    assert not g.is_looping("poll", {"x": 1})
    g2 = LoopGuard(window=20, threshold=5)
    for _ in range(4):
        g2.record("poll", {"x": 1})
    assert g2.is_looping("poll", {"x": 1})


def test_reset_clears_history() -> None:
    g = LoopGuard(window=10, threshold=2)
    g.record("x", {})
    g.reset()
    assert not g.is_looping("x", {})
