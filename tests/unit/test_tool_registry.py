from pathlib import Path

import pytest
from pydantic import BaseModel

from pishkar.tools.fs import read_file, write_file
from pishkar.tools.registry import ToolRegistry, get_spec, tool


@tool(description="add two ints")
async def add(a: int, b: int = 1) -> int:
    return a + b


def test_decorator_attaches_spec_with_signature_schema() -> None:
    spec = get_spec(add)
    assert spec.name == "add"
    assert spec.description == "add two ints"
    props = spec.input_schema["properties"]
    assert props["a"]["type"] == "integer"
    assert props["b"]["default"] == 1
    assert spec.input_schema["required"] == ["a"]


def test_decorator_falls_back_to_docstring_when_no_description() -> None:
    @tool()
    async def greet(name: str) -> str:
        """Say hello."""
        return f"hi {name}"

    assert get_spec(greet).description == "Say hello."


def test_decorator_supports_explicit_args_model() -> None:
    class Args(BaseModel):
        x: int
        y: int

    @tool(name="addxy", args_model=Args)
    async def fn(x: int, y: int) -> int:
        return x + y

    spec = get_spec(fn)
    assert spec.name == "addxy"
    assert spec.args_model is Args


def test_get_spec_raises_for_undecorated() -> None:
    async def nope() -> None: ...
    with pytest.raises(TypeError):
        get_spec(nope)


def test_registry_register_and_dispatch() -> None:
    reg = ToolRegistry()
    reg.register(add)
    assert reg.names() == ["add"]
    assert reg.get("add").name == "add"


def test_registry_rejects_duplicate_registration() -> None:
    reg = ToolRegistry()
    reg.register(add)
    with pytest.raises(ValueError):
        reg.register(add)


def test_registry_get_unknown_raises_keyerror() -> None:
    with pytest.raises(KeyError):
        ToolRegistry().get("ghost")


def test_registry_anthropic_schema_shape() -> None:
    reg = ToolRegistry()
    reg.register(add)
    [s] = reg.schemas("anthropic")
    assert set(s) == {"name", "description", "input_schema"}


def test_registry_openai_schema_shape() -> None:
    reg = ToolRegistry()
    reg.register(add)
    [s] = reg.schemas("openai")
    assert s["type"] == "function"
    assert s["function"]["name"] == "add"
    assert "parameters" in s["function"]


async def test_registry_call_validates_and_dispatches() -> None:
    reg = ToolRegistry()
    reg.register(add)
    assert await reg.call("add", {"a": 2, "b": 3}) == 5
    assert await reg.call("add", {"a": 2}) == 3  # default b=1


async def test_registry_call_rejects_bad_args() -> None:
    from pydantic import ValidationError

    reg = ToolRegistry()
    reg.register(add)
    with pytest.raises(ValidationError):
        await reg.call("add", {"a": "not-an-int"})


async def test_fs_round_trip(tmp_path: Path) -> None:
    reg = ToolRegistry()
    reg.register_many(read_file, write_file)

    target = tmp_path / "nested" / "f.txt"
    msg = await reg.call("write_file", {"path": str(target), "content": "hello"})
    assert "hello" in msg or "wrote" in msg
    assert await reg.call("read_file", {"path": str(target)}) == "hello"
