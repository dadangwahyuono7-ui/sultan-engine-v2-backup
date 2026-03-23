# VPS Autonomous Notes

Dokumen ini khusus untuk jalur operasi VPS headless.

## Runner

- `sultan_engine_v2/run_mt5_service.py`
  - loop headless untuk attach MT5, bootstrap, poll, execution, journaling, dan write heartbeat.
- `sultan_engine_v2/run_telegram_operator.py`
  - operator Telegram dua arah.
- `sultan_engine_v2/run_vps_stack.py`
  - satu process yang menjalankan MT5 service dan operator Telegram bersama.

## Heartbeat

State heartbeat ditulis ke folder `sultan_engine_v2/runtime/state/`.

Contoh file:

- `mt5_xauusd_service_status.json`
- `vps_stack_xauusd_status.json`

Isi heartbeat meliputi:

- status service,
- cycle count,
- error count,
- symbol,
- execution mode,
- auto execute,
- connected / bootstrapped,
- snapshot timestamp,
- last error,
- details tambahan seperti `telegram_enabled`, `entries_enabled`, dan `operator_paused`.

## Lock File

Lock file lokal ditulis ke `sultan_engine_v2/runtime/state/locks/`.

Tujuannya:

- mencegah dua MT5 service lokal berjalan bersamaan,
- mencegah dua Telegram operator lokal polling token yang sama,
- mencegah satu VPS stack dobel untuk symbol yang sama.

## Rekomendasi Deploy Windows VPS

1. Pastikan MT5 terminal aktif dan account target sudah login.
2. Pastikan Python venv proyek ini siap.
3. Isi env Telegram/OpenRouter sesuai kebutuhan.
4. Jalankan `run_vps_stack.py` kalau mau satu process.
5. Alternatif: jalankan `run_mt5_service.py` dan `run_telegram_operator.py` sebagai dua service terpisah.
6. Pantau file heartbeat dan journal kalau perlu watchdog eksternal.

## Catatan Safety

- agent operator hanya untuk analisa dan kontrol operator, bukan sumber keputusan trade bebas,
- execution tetap tunduk ke state engine deterministic dan CFD guard,
- pause operator hanya memblok new entry,
- lifecycle posisi yang sudah aktif tetap boleh dikelola runtime.
