"""`@tool` decorator + `ToolRegistry`.

`@tool` reads a function's signature (or an explicit pydantic model),
builds a JSON-schema for the inputs, and stores a `ToolSpec` on the
function. `ToolRegistry.register()` collects them; `.schemas()` emits the
list shape expected by the provider; `.call()` validates+dispatches.

Tool *execution* (timeout, max-result-size, approval gate) lives in
`ToolRunner`. Tools registered here are plain async functions — the
runner wraps them.
"""

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

ToolFunc = Callable[..., Awaitable[Any]]


class ToolSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    input_schema: dict[str, Any]
    args_model: type[BaseModel]
    func: ToolFunc = Field(exclude=True)


def _model_from_signature(func: ToolFunc, model_name: str) -> type[BaseModel]:
    sig = inspect.signature(func)
    fields: dict[str, Any] = {}
    for p in sig.parameters.values():
        if p.name == "self" or p.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        annotation = p.annotation if p.annotation is not inspect.Parameter.empty else Any
        default = p.default if p.default is not inspect.Parameter.empty else ...
        fields[p.name] = (annotation, default)
    return create_model(model_name, **fields)


def tool(
    *,
    name: str | None = None,
    description: str = "",
    args_model: type[BaseModel] | None = None,
) -> Callable[[ToolFunc], ToolFunc]:
    def decorate(func: ToolFunc) -> ToolFunc:
        tool_name = name or func.__name__
        model = args_model or _model_from_signature(func, f"{tool_name}_args")
        desc = description or (inspect.getdoc(func) or "").strip()
        spec = ToolSpec(
            name=tool_name,
            description=desc,
            input_schema=model.model_json_schema(),
            args_model=model,
            func=func,
        )
        func._tool_spec = spec  # type: ignore[attr-defined]
        return func

    return decorate


def get_spec(func: ToolFunc) -> ToolSpec:
    spec = getattr(func, "_tool_spec", None)
    if spec is None:
        raise TypeError(f"{func.__name__} is not decorated with @tool")
    return spec


SchemaFormat = Literal["anthropic", "openai"]


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, func: ToolFunc) -> ToolSpec:
        spec = get_spec(func)
        if spec.name in self._specs:
            raise ValueError(f"tool {spec.name!r} is already registered")
        self._specs[spec.name] = spec
        return spec

    def register_many(self, *funcs: ToolFunc) -> None:
        for f in funcs:
            self.register(f)

    def get(self, name: str) -> ToolSpec:
        if name not in self._specs:
            raise KeyError(f"unknown tool: {name!r}")
        return self._specs[name]

    def names(self) -> list[str]:
        return list(self._specs)

    def schemas(self, format: SchemaFormat = "anthropic") -> list[dict[str, Any]]:
        if format == "anthropic":
            return [
                {
                    "name": s.name,
                    "description": s.description,
                    "input_schema": s.input_schema,
                }
                for s in self._specs.values()
            ]
        return [
            {
                "type": "function",
                "function": {
                    "name": s.name,
                    "description": s.description,
                    "parameters": s.input_schema,
                },
            }
            for s in self._specs.values()
        ]

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        spec = self.get(name)
        validated = spec.args_model.model_validate(args)
        return await spec.func(**validated.model_dump())


__all__ = ["SchemaFormat", "ToolRegistry", "ToolSpec", "get_spec", "tool"]
