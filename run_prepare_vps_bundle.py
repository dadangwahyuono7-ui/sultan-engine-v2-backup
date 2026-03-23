from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sultan_engine_v2.runtime.env_loader import load_runtime_env

load_runtime_env(__file__)


ROOT = Path(__file__).resolve().parent.parent


ROOT_FILES = [
    "AGENTS.md",
    "daily_deploy_rules.md",
    "banker_rules.md",
    "implementation_plan.md",
    "walkthrough.md",
    "task.md",
    "SEMINAR_daily_deploy.pdf",
    "__pdf_extract_seminar.txt",
    "sultan.env",
    "sultan.env.example",
    "sultan_load_env.ps1",
    "tradingview_symbol_map.json",
    "tradingview_symbol_map.example.json",
    "run_sultan.bat",
    "run_sultan_all.bat",
    "run_sultan_all_binance_worker.bat",
    "run_sultan_all_mt5_worker.bat",
    "run_sultan_crypto.bat",
    "run_sultan_dashboard.bat",
    "run_sultan_fleet_dashboard.bat",
    "run_sultan_fleet_web.bat",
    "run_sultan_monitor.bat",
    "run_sultan_production.bat",
    "run_sultan_reset.bat",
    "run_sultan_vps.bat",
    "setup_sultan_vps.bat",
]


BACKUP_MINIMAL_FILES = [
    "ngrok.exe",
    "ngrok_internal.yml",
    "forex/telegram_config.py",
]


RUNTIME_DIRS_TO_RECREATE = [
    "state",
    "state/locks",
    "logs",
    "history",
    "archive",
    "journal",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a clean Windows VPS transfer bundle for Sultan without .venv, .tmp, and local runtime residue."
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "_deploy"),
        help="Directory that will receive the staged bundle and zip archive.",
    )
    parser.add_argument(
        "--name",
        default="sultan_vps_bundle",
        help="Base name for the staged bundle.",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Skip zip generation and only create the staged folder.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output_dir)
    bundle_dir = output_dir / f"{args.name}_{timestamp}"
    zip_path = output_dir / f"{args.name}_{timestamp}.zip"

    bundle_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Preparing clean VPS bundle at {bundle_dir}")
    _copy_root_files(bundle_dir)
    _copy_tree(ROOT / "sultan_engine_v2", bundle_dir / "sultan_engine_v2", ignore=_ignore_sultan_tree)
    _copy_minimal_backup(bundle_dir / "BACKUP_-02-2026_23-20")
    _prepare_runtime_dirs(bundle_dir / "sultan_engine_v2" / "runtime")
    requirements_path = _export_requirements(bundle_dir)
    transfer_doc = _write_transfer_readme(bundle_dir, requirements_path)

    if not args.no_zip:
        _zip_bundle(bundle_dir, zip_path)
        print(f"ZIP ready: {zip_path}")

    print(f"Bundle ready: {bundle_dir}")
    print(f"Guide: {transfer_doc}")
    return 0


def _copy_root_files(bundle_dir: Path) -> None:
    for relative_path in ROOT_FILES:
        source = ROOT / relative_path
        if not source.exists():
            continue
        destination = bundle_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _copy_minimal_backup(destination_root: Path) -> None:
    for relative_path in BACKUP_MINIMAL_FILES:
        source = ROOT / "BACKUP_-02-2026_23-20" / relative_path
        if not source.exists():
            continue
        destination = destination_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _copy_tree(source: Path, destination: Path, *, ignore) -> None:
    shutil.copytree(source, destination, dirs_exist_ok=True, ignore=ignore)


def _ignore_sultan_tree(current_dir: str, names: list[str]) -> set[str]:
    current_path = Path(current_dir)
    ignored: set[str] = set()
    relative = current_path.relative_to(ROOT / "sultan_engine_v2")
    relative_posix = relative.as_posix()

    common_ignored = {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
    }
    for name in names:
        if name in common_ignored:
            ignored.add(name)
            continue
        if name.endswith((".pyc", ".pyo", ".pyd")):
            ignored.add(name)

    if relative_posix == "runtime":
        ignored.update({"state", "logs", "history", "archive", "journal", "__pycache__"})

    return ignored


def _prepare_runtime_dirs(runtime_dir: Path) -> None:
    for relative_dir in RUNTIME_DIRS_TO_RECREATE:
        (runtime_dir / relative_dir).mkdir(parents=True, exist_ok=True)


def _export_requirements(bundle_dir: Path) -> Path | None:
    python_path = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python_path.exists():
        return None

    try:
        result = subprocess.run(
            [str(python_path), "-m", "pip", "freeze"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None

    if result.returncode != 0:
        return None

    requirements_path = bundle_dir / "requirements.vps.txt"
    requirements_path.write_text(result.stdout, encoding="utf-8")
    return requirements_path


def _write_transfer_readme(bundle_dir: Path, requirements_path: Path | None) -> Path:
    lines = [
        "# VPS Transfer Guide",
        "",
        "Bundle ini sudah dibersihkan dari `.tmp`, `.venv`, `__pycache__`, dan runtime residue lokal.",
        "",
        "## Isi Penting",
        "",
        "- launcher Sultan lengkap",
        "- `sultan_engine_v2/` source code",
        "- `sultan.env` aktif",
        "- `BACKUP_-02-2026_23-20/ngrok.exe`",
        "- `BACKUP_-02-2026_23-20/ngrok_internal.yml`",
        "- `BACKUP_-02-2026_23-20/forex/telegram_config.py`",
        "- runbook agent di `AGENTS.md` dan `sultan_engine_v2/docs/vps_agent_runbook.md`",
        "",
        "## Langkah Di VPS",
        "",
        "1. Extract zip ini ke folder tujuan.",
        "2. Install Python versi yang cocok.",
        "3. Jalankan `setup_sultan_vps.bat`.",
        "4. Pastikan MT5 terminal sudah terpasang dan login.",
        "5. Jalankan `run_sultan_reset.bat` sekali.",
        "6. Jalankan `run_sultan_all.bat`.",
        "",
        "## Catatan",
        "",
        "- Kalau launcher bilang `Python venv not found`, itu normal sebelum setup awal.",
        "- Auto-entry tetap diatur dari `sultan.env`.",
        "- Web UI fleet default di port `5000`.",
        "- Ngrok dan token monitor mengikuti config di `sultan.env`.",
        "- Core CMP-VR-CF tidak boleh diubah.",
    ]
    if requirements_path is None:
        lines.extend(
            [
                "",
                "## Dependency Note",
                "",
                "File `requirements.vps.txt` tidak berhasil dibuat otomatis. Kalau perlu, install dependency dari mesin sumber secara manual.",
            ]
        )

    transfer_doc = bundle_dir / "VPS_TRANSFER_GUIDE.md"
    transfer_doc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return transfer_doc


def _zip_bundle(bundle_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(bundle_dir.rglob("*")):
            if path.is_dir():
                continue
            archive.write(path, path.relative_to(bundle_dir))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
