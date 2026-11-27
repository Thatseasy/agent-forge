"""Three-layer path resolution: Package Assets -> AGENT_FORGE_HOME -> Target Workspace."""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import platformdirs


@dataclass(frozen=True)
class ForgePaths:
    """Resolved three-layer paths for a forge CLI or MCP execution."""

    package_assets_dir: Path
    home_dir: Path
    state_root: Path
    workspace_dir: Path
    is_checkout_mode: bool


def resolve_package_assets_dir() -> Path:
    """Locate bundled read-only package assets via `importlib.resources`."""
    try:
        traversable = resources.files("forge.assets")
        candidate = Path(str(traversable)).resolve()
        if candidate.is_dir():
            return candidate
    except (ModuleNotFoundError, TypeError, OSError):
        pass
    return (Path(__file__).resolve().parent / "assets").resolve()


def resolve_platformdirs_home() -> Path:
    """Return the OS-native user data directory from `platformdirs`."""
    return Path(platformdirs.user_data_dir("agent-forge", appauthor=False)).expanduser().resolve()


def resolve_agent_forge_home() -> Path:
    """Resolve writable user state directory (`AGENT_FORGE_HOME` or `~/.agent-forge`)."""
    env_home = os.environ.get("AGENT_FORGE_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser().resolve()
    try:
        home = Path.home()
        if str(home).strip() not in {"", "/"}:
            return (home / ".agent-forge").resolve()
    except (RuntimeError, OSError):
        pass
    return resolve_platformdirs_home()


def find_forge_checkout(start: Path | None = None) -> Path | None:
    """Return the local `agent-forge` checkout root if running inside it, else `None`."""
    env_root = os.environ.get("FORGE_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file() and (
            candidate / "src" / "forge" / "__init__.py"
        ).is_file():
            return candidate
    return None


def resolve_target_workspace(target: Path | str | None = None) -> Path:
    """Resolve target workspace directory, defaulting to `Path.cwd()`."""
    if target is None:
        return Path.cwd().resolve()
    return Path(target).expanduser().resolve()


def resolve_state_root(root: Path | None = None) -> tuple[Path, bool]:
    """Resolve the writable forge root (`(path, is_checkout_mode)`).

    Precedence:
    1. Explicit `root` argument (preserves test/checkout isolation).
    2. `FORGE_ROOT` environment variable.
    3. Explicit `AGENT_FORGE_HOME` environment variable.
    4. Local `agent-forge` checkout detected from `Path.cwd()`.
    5. Default `AGENT_FORGE_HOME` (`~/.agent-forge` or `platformdirs`).
    """
    if root is not None:
        resolved = Path(root).expanduser().resolve()
        is_checkout = (resolved / "pyproject.toml").is_file() and (
            resolved / "src" / "forge" / "__init__.py"
        ).is_file()
        return resolved, is_checkout

    env_root = os.environ.get("FORGE_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve(), True

    env_home = os.environ.get("AGENT_FORGE_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser().resolve(), False

    checkout = find_forge_checkout()
    if checkout is not None:
        return checkout, True

    return resolve_agent_forge_home(), False


def ensure_agent_forge_home(home_dir: Path | None = None) -> Path:
    """Idempotently initialize standard directories inside `AGENT_FORGE_HOME`."""
    target = (home_dir or resolve_agent_forge_home()).expanduser().resolve()
    subdirs = (
        "state",
        "staging/inbox",
        "staging/archive_audit",
        "staging/cache",
        "units",
        "runs",
        "logs",
        "library/skills",
        "library/profiles",
        "library/nudges/backlog",
        "library/clis",
    )
    for rel in subdirs:
        (target / rel).mkdir(parents=True, exist_ok=True)
    return target


def resolve_paths(root: Path | None = None, target: Path | str | None = None) -> ForgePaths:
    """Resolve all three architectural path layers in one immutable record."""
    state_root, is_checkout = resolve_state_root(root)
    return ForgePaths(
        package_assets_dir=resolve_package_assets_dir(),
        home_dir=resolve_agent_forge_home(),
        state_root=state_root,
        workspace_dir=resolve_target_workspace(target),
        is_checkout_mode=is_checkout,
    )


def parse_env_file_into_environ(env_path: Path) -> None:
    """Load non-empty `KEY=VALUE` pairs from `env_path` using `os.environ.setdefault`."""
    if not env_path.is_file():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key_clean = key.strip()
        val_clean = value.strip()
        if len(val_clean) >= 2 and val_clean[0] == val_clean[-1] and val_clean[0] in {"'", '"'}:
            val_clean = val_clean[1:-1]
        if key_clean and val_clean:
            os.environ.setdefault(key_clean, val_clean)
