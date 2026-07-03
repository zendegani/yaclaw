import plistlib
from pathlib import Path

import pytest

from pishkar.daemon import (
    LABEL,
    SYSTEMD_UNIT,
    install,
    launchagent_path,
    main,
    render_launchagent,
    render_systemd_unit,
    systemd_unit_path,
    uninstall,
)


def test_launchagent_wraps_the_single_entrypoint(tmp_path: Path) -> None:
    raw = render_launchagent(
        python="/venv/bin/python", workdir=tmp_path, home=tmp_path
    )
    plist = plistlib.loads(raw)
    assert plist["Label"] == LABEL
    assert plist["ProgramArguments"] == ["/venv/bin/python", "-m", "pishkar.server"]
    assert plist["WorkingDirectory"] == str(tmp_path)
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] == {"SuccessfulExit": False}


def test_systemd_unit_wraps_the_single_entrypoint(tmp_path: Path) -> None:
    unit = render_systemd_unit(python="/venv/bin/python", workdir=tmp_path)
    assert "ExecStart=/venv/bin/python -m pishkar.server" in unit
    assert f"WorkingDirectory={tmp_path}" in unit
    assert "Restart=on-failure" in unit
    assert "WantedBy=default.target" in unit


def test_install_darwin_writes_launchagent(tmp_path: Path) -> None:
    path = install(
        platform="darwin", python="/p", workdir=tmp_path, home=tmp_path
    )
    assert path == launchagent_path(tmp_path)
    assert plistlib.loads(path.read_bytes())["Label"] == LABEL
    assert (tmp_path / ".pishkar" / "logs").is_dir()


def test_install_linux_writes_systemd_user_unit(tmp_path: Path) -> None:
    path = install(platform="linux", python="/p", workdir=tmp_path, home=tmp_path)
    assert path == systemd_unit_path(tmp_path)
    assert path.name == SYSTEMD_UNIT
    assert "pishkar.server" in path.read_text(encoding="utf-8")


def test_install_is_idempotent(tmp_path: Path) -> None:
    first = install(platform="linux", python="/p", workdir=tmp_path, home=tmp_path)
    second = install(platform="linux", python="/q", workdir=tmp_path, home=tmp_path)
    assert first == second
    assert "ExecStart=/q" in second.read_text(encoding="utf-8")


def test_install_unsupported_platform_raises(tmp_path: Path) -> None:
    with pytest.raises(NotImplementedError):
        install(platform="win32", python="/p", workdir=tmp_path, home=tmp_path)


def test_uninstall_removes_written_file(tmp_path: Path) -> None:
    path = install(platform="linux", python="/p", workdir=tmp_path, home=tmp_path)
    assert uninstall(platform="linux", home=tmp_path) == path
    assert not path.exists()


def test_uninstall_without_install_is_noop(tmp_path: Path) -> None:
    assert uninstall(platform="linux", home=tmp_path) is None


def test_main_without_command_prints_usage() -> None:
    assert main([]) == 2


def test_main_rejects_unknown_command() -> None:
    assert main(["restart"]) == 2
