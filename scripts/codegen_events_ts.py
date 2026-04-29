"""Regenerate `ui/src/api/events.ts` from the Pydantic event models.

Run: `uv run python scripts/codegen_events_ts.py`

Requires `pydantic-to-typescript` (dev dep) and `npx json-schema-to-typescript`
on PATH. Schema drift between server and client is a silent failure mode;
re-run this whenever events.py or messages.py changes.
"""

from pathlib import Path

from pydantic2ts import generate_typescript_defs

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "ui" / "src" / "api" / "events.ts"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    generate_typescript_defs("pishkar.core.events", str(OUT))
    extra = ROOT / "ui" / "src" / "api" / "messages.ts"
    generate_typescript_defs("pishkar.core.messages", str(extra))


if __name__ == "__main__":
    main()
