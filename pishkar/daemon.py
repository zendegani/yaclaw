"""OS-native daemon installer — keep Pishkar running across logouts/reboots.

`python -m pishkar.daemon install` writes the platform's service file
(a LaunchAgent plist on macOS, a systemd *user* unit on Linux) wrapping
the one stable entrypoint — the service just runs `<this python> -m
pishkar.server` from the current directory, so `.env` keeps being
picked up from the repo root. No second entrypoint exists.

The installer only writes the file and prints the activation commands;
it never calls `launchctl`/`systemctl` itself, so it needs no elevated
rights and works in CI. `uninstall` removes the file and prints the
matching deactivation commands.
"""

import plistlib
import sys
from pathlib import Path

LABEL = "com.pishkar.server"
SYSTEMD_UNIT = "pishkar.service"


def _log_dir(home: Path) -> Path:
    return home / ".pishkar" / "logs"


def launchagent_path(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def systemd_unit_path(home: Path) -> Path:
    return home / ".config" / "systemd" / "user" / SYSTEMD_UNIT


def render_launchagent(*, python: str, workdir: Path, home: Path) -> bytes:
    log = _log_dir(home)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "pishkar.server"],
        "WorkingDirectory": str(workdir),
        "RunAtLoad": True,
        # Restart on crash, but respect a clean exit (e.g. Ctrl-C-less stop).
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(log / "server.log"),
        "StandardErrorPath": str(log / "server.err.log"),
    }
    return plistlib.dumps(plist)


def render_systemd_unit(*, python: str, workdir: Path) -> str:
    return f"""[Unit]
Description=Pishkar personal AI butler
After=network-online.target
Wants=network-online.target

[Service]
ExecStart={python} -m pishkar.server
WorkingDirectory={workdir}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def install(
    *,
    platform: str | None = None,
    python: str | None = None,
    workdir: Path | None = None,
    home: Path | None = None,
) -> Path:
    """Write the service file for this platform and return its path."""
    platform = platform or sys.platform
    python = python or sys.executable
    workdir = workdir or Path.cwd()
    home = home or Path.home()
    _log_dir(home).mkdir(parents=True, exist_ok=True)

    if platform == "darwin":
        path = launchagent_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(render_launchagent(python=python, workdir=workdir, home=home))
        return path
    if platform.startswith("linux"):
        path = systemd_unit_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            render_systemd_unit(python=python, workdir=workdir), encoding="utf-8"
        )
        return path
    raise NotImplementedError(
        f"No daemon template for platform {platform!r} yet "
        "(supported: macOS LaunchAgent, Linux systemd user unit)."
    )


def uninstall(*, platform: str | None = None, home: Path | None = None) -> Path | None:
    """Remove the service file if present; return the removed path."""
    platform = platform or sys.platform
    home = home or Path.home()
    path = (
        launchagent_path(home)
        if platform == "darwin"
        else systemd_unit_path(home)
        if platform.startswith("linux")
        else None
    )
    if path is None or not path.is_file():
        return None
    path.unlink()
    return path


def _activation_help(platform: str, path: Path) -> str:
    if platform == "darwin":
        return (
            f"Wrote {path}\n"
            f"Activate now:   launchctl load -w {path}\n"
            f"Deactivate:     launchctl unload -w {path}\n"
            f"Logs:           ~/.pishkar/logs/server.log"
        )
    return (
        f"Wrote {path}\n"
        f"Activate now:   systemctl --user daemon-reload && "
        f"systemctl --user enable --now {SYSTEMD_UNIT}\n"
        f"Survive logout: sudo loginctl enable-linger $USER\n"
        f"Logs:           journalctl --user -u {SYSTEMD_UNIT} -f"
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else ""
    if command == "install":
        try:
            path = install()
        except NotImplementedError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(_activation_help(sys.platform, path))
        return 0
    if command == "uninstall":
        removed = uninstall()
        if removed is None:
            print("No service file found; nothing to remove.")
        else:
            print(
                f"Removed {removed}. If the service is still loaded, "
                "deactivate it with launchctl unload / systemctl --user "
                "disable --now."
            )
        return 0
    print(
        "Usage: python -m pishkar.daemon install|uninstall\n\n"
        "Writes (or removes) the OS service file that keeps "
        "`python -m pishkar.server` running: a LaunchAgent on macOS, a "
        "systemd user unit on Linux. Run from the repo root so the "
        "service inherits the right working directory for `.env`.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "LABEL",
    "SYSTEMD_UNIT",
    "install",
    "launchagent_path",
    "main",
    "render_launchagent",
    "render_systemd_unit",
    "systemd_unit_path",
    "uninstall",
]
