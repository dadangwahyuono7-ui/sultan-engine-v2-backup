from __future__ import annotations

import os
from pathlib import Path


ENV_FILE_NAMES = (".env", "sultan.env", ".env.local")
_LOADED_ROOTS: set[str] = set()


def resolve_project_root(start_path: str | Path | None = None) -> Path:
    path = Path(start_path).resolve() if start_path else Path(__file__).resolve()
    current = path if path.is_dir() else path.parent

    for candidate in (current, *current.parents):
        if (candidate / "sultan_engine_v2").exists():
            return candidate
    return current


def load_runtime_env(start_path: str | Path | None = None, *, override: bool = False, force_reload: bool = False) -> list[Path]:
    root = resolve_project_root(start_path)
    root_key = str(root).lower()
    if not force_reload and root_key in _LOADED_ROOTS:
        return [root / name for name in ENV_FILE_NAMES if (root / name).exists()]

    loaded_files: list[Path] = []
    for name in ENV_FILE_NAMES:
        env_path = root / name
        if env_path.exists():
            load_env_file(env_path, override=override)
            loaded_files.append(env_path)

    _LOADED_ROOTS.add(root_key)
    return loaded_files


def load_env_file(path: str | Path, *, override: bool = False) -> dict[str, str]:
    env_path = Path(path)
    loaded: dict[str, str] = {}
    if not env_path.exists():
        return loaded

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = parse_env_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        if override or key not in os.environ:
            os.environ[key] = value
        loaded[key] = value
    return loaded


def parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith(";"):
        return None

    if stripped.lower().startswith("export "):
        stripped = stripped[7:].lstrip()

    if "=" not in stripped:
        return None

    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key or not _is_valid_env_key(key):
        return None

    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]

    return key, value


def _is_valid_env_key(key: str) -> bool:
    if not key:
        return False
    if not (key[0].isalpha() or key[0] == "_"):
        return False
    return all(char.isalnum() or char == "_" for char in key)
