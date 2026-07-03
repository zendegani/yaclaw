"""Trust-based tool filtering.

Every `InboundMessage` carries a `trust_level` (`full`, `limited`,
`untrusted`). A `TrustPolicy` maps each tool to the *minimum* trust it
requires and is enforced in two places:

* the schemas offered to the LLM are filtered per turn, so a low-trust
  message never even sees a tool it may not call;
* `SubprocessToolRunner` re-checks before dispatch, so a hallucinated
  or replayed tool call is denied even if it slipped past filtering.

Tools not listed in the policy default to requiring `full` trust — a
newly registered tool (including anything an MCP server contributes)
is never exposed to low-trust input by accident.
"""

from typing import Any

from pishkar.core.messages import TrustLevel

TRUST_RANK: dict[TrustLevel, int] = {"untrusted": 0, "limited": 1, "full": 2}

# Minimum trust required per built-in tool. Read-only / side-effect-free
# tools are callable from `limited` input; anything that mutates state,
# runs code, or reaches the network arbitrarily stays `full`-only.
DEFAULT_TOOL_TRUST: dict[str, TrustLevel] = {
    "read_file": "limited",
    "read_url": "limited",
    "search": "limited",
    "plan": "limited",
    "speak": "limited",
}

DEFAULT_MIN_TRUST: TrustLevel = "full"


def _schema_tool_name(schema: dict[str, Any]) -> str | None:
    """Extract the tool name from an anthropic- or openai-shaped schema."""
    function = schema.get("function")
    if isinstance(function, dict):
        name = function.get("name")
        return name if isinstance(name, str) else None
    name = schema.get("name")
    return name if isinstance(name, str) else None


class TrustPolicy:
    def __init__(
        self,
        tool_trust: dict[str, TrustLevel] | None = None,
        *,
        default_min_trust: TrustLevel = DEFAULT_MIN_TRUST,
    ) -> None:
        self._tool_trust = (
            dict(DEFAULT_TOOL_TRUST) if tool_trust is None else dict(tool_trust)
        )
        self._default = default_min_trust

    def required(self, tool_name: str) -> TrustLevel:
        return self._tool_trust.get(tool_name, self._default)

    def allows(self, tool_name: str, trust: TrustLevel) -> bool:
        return TRUST_RANK[trust] >= TRUST_RANK[self.required(tool_name)]

    def filter_schemas(
        self, schemas: list[dict[str, Any]], trust: TrustLevel
    ) -> list[dict[str, Any]]:
        """Drop schemas for tools the given trust level may not call.

        A schema whose name can't be extracted is dropped for anything
        below `full` — unidentifiable means unvetted.
        """
        if TRUST_RANK[trust] >= TRUST_RANK["full"]:
            return schemas
        kept: list[dict[str, Any]] = []
        for schema in schemas:
            name = _schema_tool_name(schema)
            if name is not None and self.allows(name, trust):
                kept.append(schema)
        return kept


__all__ = [
    "DEFAULT_MIN_TRUST",
    "DEFAULT_TOOL_TRUST",
    "TRUST_RANK",
    "TrustPolicy",
]
