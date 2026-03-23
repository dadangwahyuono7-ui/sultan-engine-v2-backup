from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_text(path: str | Path, content: str, *, encoding: str = "utf-8") -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temp_path.write_text(content, encoding=encoding)
    os.replace(temp_path, target)


def atomic_write_json(path: str | Path, payload: Any, *, encoding: str = "utf-8", ensure_ascii: bool = True, indent: int = 2, default: Any = str) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=ensure_ascii, indent=indent, default=default),
        encoding=encoding,
    )
