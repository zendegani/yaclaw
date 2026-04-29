from pathlib import Path

from pishkar.workspace.loader import Workspace, WorkspaceLoader, compose_system_prompt


def test_ensure_starter_seeds_missing_files(tmp_path: Path) -> None:
    loader = WorkspaceLoader(base_dir=tmp_path)
    ws = loader.ensure_starter("ali")

    root = tmp_path / "users" / "ali"
    for name in ("SOUL.md", "USER.md", "HEARTBEAT.md", "AGENTS.md"):
        assert (root / name).is_file(), f"{name} missing"

    assert "Pishkar" in ws.soul
    assert ws.user.strip() != ""  # has placeholder body
    assert ws.user_id == "ali"


def test_ensure_starter_does_not_overwrite_existing(tmp_path: Path) -> None:
    loader = WorkspaceLoader(base_dir=tmp_path)
    user_root = tmp_path / "users" / "ali"
    user_root.mkdir(parents=True)
    (user_root / "SOUL.md").write_text("custom soul")
    (user_root / "USER.md").write_text("custom user")

    ws = loader.ensure_starter("ali")

    assert ws.soul == "custom soul"
    assert ws.user == "custom user"
    # New files still seeded for the ones that didn't exist.
    assert (user_root / "HEARTBEAT.md").is_file()


def test_ensure_starter_is_idempotent(tmp_path: Path) -> None:
    loader = WorkspaceLoader(base_dir=tmp_path)
    loader.ensure_starter("ali")
    soul_before = (tmp_path / "users" / "ali" / "SOUL.md").read_text()
    loader.ensure_starter("ali")
    soul_after = (tmp_path / "users" / "ali" / "SOUL.md").read_text()
    assert soul_before == soul_after


def test_compose_system_prompt_stitches_soul_and_user() -> None:
    ws = Workspace(user_id="ali", root=Path("/x"), soul="PERSONA", user="FACTS")
    prompt = compose_system_prompt(ws, base="BASE")
    assert prompt.startswith("BASE")
    assert "PERSONA" in prompt
    assert "FACTS" in prompt
    assert prompt.index("PERSONA") < prompt.index("FACTS")


def test_compose_system_prompt_skips_empty_sections() -> None:
    ws = Workspace(user_id="ali", root=Path("/x"))
    prompt = compose_system_prompt(ws, base="BASE")
    assert prompt == "BASE"
    assert "SOUL" not in prompt
    assert "USER.md" not in prompt
