from pathlib import Path

from pishkar.workspace.loader import WorkspaceLoader


def test_load_empty_workspace_returns_blank_fields(tmp_path: Path) -> None:
    ws = WorkspaceLoader(tmp_path).load("ali")
    assert ws.user_id == "ali"
    assert ws.soul == ws.agents == ws.user == ws.heartbeat == ""
    assert ws.skills == []


def test_write_then_load_round_trips_markdown_files(tmp_path: Path) -> None:
    loader = WorkspaceLoader(tmp_path)
    loader.write("ali", "SOUL", "I am Pishkar.")
    loader.write("ali", "USER", "Name: Ali")
    loader.write("ali", "AGENTS", "Be helpful.")
    loader.write("ali", "HEARTBEAT", "- task\n")

    ws = loader.load("ali")
    assert ws.soul == "I am Pishkar."
    assert ws.user == "Name: Ali"
    assert ws.agents == "Be helpful."
    assert ws.heartbeat == "- task\n"


def test_load_parses_skill_frontmatter(tmp_path: Path) -> None:
    loader = WorkspaceLoader(tmp_path)
    skill_dir = loader.user_root("ali") / "skills" / "greet"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: greet\n"
        'description: "say hello"\n'
        "model: haiku\n"
        "---\n"
        "When greeting, be warm.\n"
    )

    ws = loader.load("ali")
    assert len(ws.skills) == 1
    skill = ws.skills[0]
    assert skill.name == "greet"
    assert skill.description == "say hello"
    assert skill.model == "haiku"
    assert skill.body.strip() == "When greeting, be warm."


def test_skill_without_frontmatter_uses_directory_name(tmp_path: Path) -> None:
    loader = WorkspaceLoader(tmp_path)
    skill_dir = loader.user_root("ali") / "skills" / "fallback"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Just a body, no frontmatter.\n")

    ws = loader.load("ali")
    assert len(ws.skills) == 1
    assert ws.skills[0].name == "fallback"
    assert ws.skills[0].description == ""
    assert ws.skills[0].model is None


def test_user_isolation(tmp_path: Path) -> None:
    loader = WorkspaceLoader(tmp_path)
    loader.write("ali", "USER", "Ali's facts")
    loader.write("guest", "USER", "Guest's facts")
    assert loader.load("ali").user == "Ali's facts"
    assert loader.load("guest").user == "Guest's facts"
