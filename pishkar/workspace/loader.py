"""Workspace loader.

Reads `~/.pishkar/users/<user_id>/{SOUL,AGENTS,USER,HEARTBEAT}.md` and
`skills/<name>/SKILL.md` (Anthropic SKILL.md format with YAML-style
frontmatter: `name`, `description`, optional `model`).

Writes go through `atomic_io.atomic_write_text` so a crash mid-write cannot
corrupt the workspace files the agent edits live (`SOUL.md`, `USER.md`,
`HEARTBEAT.md`).
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from pishkar.workspace.atomic_io import atomic_write_text

WorkspaceFile = Literal["SOUL", "AGENTS", "USER", "HEARTBEAT"]
_FILES: tuple[WorkspaceFile, ...] = ("SOUL", "AGENTS", "USER", "HEARTBEAT")


class Skill(BaseModel):
    name: str
    description: str
    model: str | None = None
    body: str
    path: Path


class Workspace(BaseModel):
    user_id: str
    root: Path
    soul: str = ""
    agents: str = ""
    user: str = ""
    heartbeat: str = ""
    skills: list[Skill] = []


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse `---\\n key: value\\n ---\\n body`. Minimal: string scalars only."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    front, body = text[4:end], text[end + 5 :]
    meta: dict[str, str] = {}
    for line in front.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, body.lstrip("\n")


def _load_skill(skill_md: Path) -> Skill | None:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, body = _parse_frontmatter(text)
    name = meta.get("name") or skill_md.parent.name
    description = meta.get("description", "")
    return Skill(
        name=name,
        description=description,
        model=meta.get("model"),
        body=body,
        path=skill_md,
    )


class WorkspaceLoader:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or (Path.home() / ".pishkar")

    def user_root(self, user_id: str) -> Path:
        return self.base_dir / "users" / user_id

    def load(self, user_id: str) -> Workspace:
        root = self.user_root(user_id)
        ws = Workspace(user_id=user_id, root=root)
        for name in _FILES:
            path = root / f"{name}.md"
            if path.exists():
                attr = name.lower()
                setattr(ws, attr, path.read_text(encoding="utf-8"))
        skills_dir = root / "skills"
        if skills_dir.is_dir():
            for skill_dir in sorted(skills_dir.iterdir()):
                skill_md = skill_dir / "SKILL.md"
                if skill_md.is_file():
                    skill = _load_skill(skill_md)
                    if skill is not None:
                        ws.skills.append(skill)
        return ws

    def write(self, user_id: str, file: WorkspaceFile, content: str) -> None:
        atomic_write_text(self.user_root(user_id) / f"{file}.md", content)

    def ensure_starter(self, user_id: str) -> Workspace:
        """Seed missing workspace files from the bundled starter pack.

        Idempotent: existing files are never overwritten — Pishkar (and the
        user) own those once written. Returns the loaded workspace."""
        starter_dir = Path(__file__).parent / "starter"
        root = self.user_root(user_id)
        root.mkdir(parents=True, exist_ok=True)
        for name in _FILES:
            target = root / f"{name}.md"
            if target.exists():
                continue
            template = starter_dir / f"{name}.md"
            if template.is_file():
                atomic_write_text(target, template.read_text(encoding="utf-8"))
        return self.load(user_id)


def compose_system_prompt(ws: Workspace, *, base: str) -> str:
    """Stitch SOUL.md + USER.md into the system prompt.

    SOUL is the persona and operating instructions; USER is the live
    user-context document the agent both reads and writes. Empty sections
    are skipped so a brand-new workspace doesn't waste tokens on headers."""
    parts = [base.strip()] if base.strip() else []
    if ws.soul.strip():
        parts.append("# Persona (SOUL.md)\n\n" + ws.soul.strip())
    if ws.user.strip():
        parts.append("# About the user (USER.md)\n\n" + ws.user.strip())
    return "\n\n".join(parts)
