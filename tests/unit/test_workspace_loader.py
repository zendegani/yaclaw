from pathlib import Path

from pishkar.workspace.loader import (
    Skill,
    Workspace,
    WorkspaceLoader,
    compose_system_prompt,
)


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


def test_compose_injects_skills_into_system_prompt(tmp_path: Path) -> None:
    ws = Workspace(
        user_id="ali",
        root=tmp_path,
        soul="Be Pishkar.",
        skills=[
            Skill(
                name="greet",
                description="say hello",
                body="Wave warmly.",
                path=tmp_path / "skills" / "greet" / "SKILL.md",
            ),
            Skill(
                name="cook",
                description="",
                body="Boil water.",
                path=tmp_path / "skills" / "cook" / "SKILL.md",
            ),
        ],
    )
    out = compose_system_prompt(ws, base="base")
    assert "# Skills" in out
    assert "## greet — say hello" in out
    assert "Wave warmly." in out
    assert "## cook" in out
    assert "Boil water." in out


def test_compose_omits_skills_section_when_empty(tmp_path: Path) -> None:
    ws = Workspace(user_id="ali", root=tmp_path, soul="Persona.")
    out = compose_system_prompt(ws, base="base")
    assert "# Skills" not in out


def test_compose_injects_memory_when_present(tmp_path: Path) -> None:
    ws = Workspace(
        user_id="ali",
        root=tmp_path,
        memory="- User likes tea.\n- Allergic to nuts.\n",
    )
    out = compose_system_prompt(ws, base="base")
    assert "# Remembered facts (MEMORY.md)" in out
    assert "User likes tea." in out


def test_memory_round_trips_via_write(tmp_path: Path) -> None:
    loader = WorkspaceLoader(tmp_path)
    loader.write("ali", "MEMORY", "- fact 1\n- fact 2\n")
    ws = loader.load("ali")
    assert ws.memory == "- fact 1\n- fact 2\n"
