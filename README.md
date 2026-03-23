# Sultan Engine V2

Sultan Engine V2 adalah rewrite total dari engine lama.

Status operasi saat ini:

- fokus eksekusi v1 dipusatkan ke MT5 untuk CFD/forex,
- adapter exchange/Binance tetap disimpan di repo sebagai jalur fase berikutnya,
- semua hardening, validasi runtime, dan auto-execution aktif sekarang difokuskan ke CFD dulu.

Keputusan arsitektur awal yang dikunci:

1. Satu deterministic trading core untuk grammar CMP-VR-CF.
2. Dua adapter venue di atas core yang sama:
   - MT5 untuk CFD/forex.
   - Exchange untuk crypto.
3. Satu AI manager/operator layer untuk VPS supervision, audit, report, dan orchestration.

## Jawaban Arsitektur

Engine tidak dipisah menjadi dua otak trading yang berbeda.

Yang benar:

- Satu core engine.
- Dua adapter market/execution.
- Satu runtime bisa mengaktifkan satu venue atau beberapa venue sesuai mode operasi.

Catatan MT5 v1:

- runtime MT5 akan attach ke terminal/account MT5 yang sedang aktif,
- bukan membaca semua account MT5 sekaligus.

Catatan prioritas fase sekarang:

- venue utama operasional adalah MT5 CFD,
- dashboard, execution runner, dan manager report default diarahkan ke snapshot MT5,
- Binance tidak dihapus, tapi tidak menjadi jalur operasi utama sampai CFD path benar-benar matang.

Artinya, CMP-VR-CF, domain resolution, target SNR, dan risk grammar tetap satu.
Yang berbeda hanya cara membaca data dan mengeksekusi order pada masing-masing venue.

## Folder Awal

- core: core logic CMP-VR-CF dan state engine.
- adapters/mt5: adapter data dan execution untuk MT5.
- adapters/exchange: adapter data dan execution untuk exchange crypto.
- runtime: proses yang menjalankan loop market data, state update, journal, dan execution.
- manager: AI manager/operator layer untuk VPS.
- docs: catatan arsitektur dan doctrine.
- tests: replay test dan unit test.

## Prinsip Build

1. Core tidak boleh tahu detail broker atau exchange.
2. Adapter tidak boleh mengubah grammar trading.
3. Manager tidak boleh menjadi sumber keputusan trading bebas pada v1.
4. Semua venue harus tunduk ke vocabulary yang sama: breakout, CMP, CMP MASTER, VR, CF, domain, target SNR, invalidation.

## Urutan Build Disarankan

1. Freeze doctrine.
2. Implement breakout detector.
3. Implement TF state and relation engine.
4. Implement target/risk rules.
5. Implement paper runtime.
6. Implement MT5 adapter.
7. Implement exchange adapter.
8. Implement AI manager dan Telegram operator channel.

## Operator Telegram Dua Arah

Sekarang layer operator sudah diarahkan ke model dua arah:

- Telegram tetap dipakai untuk notifikasi keluar dari runtime.
- Bot operator terpisah bisa membaca pesan masuk dari chat operator yang diizinkan.
- Command aman yang didukung: STATUS, AUDIT, PAUSE, RESUME, HELP.
- Freeform question sekarang diarahkan ke operator agent yang baca snapshot runtime, audit manager, logs, dan doctrine Daily Deploy.
- PAUSE hanya memblok new entry. Lifecycle posisi yang sudah terbuka tetap jalan.
- OpenRouter bisa dipakai sementara untuk menginterpretasi bahasa natural operator, tapi tidak boleh menjadi sumber keputusan trading.
- Agent core dibuat channel-agnostic, jadi nanti bisa dipakai lagi untuk panel web, browser extension, atau channel operator lain tanpa ganti otak analisa.

Runner operator:

- jalankan `python sultan_engine_v2/run_telegram_operator.py`

Environment minimal:

- `SULTAN_TELEGRAM_ENABLED=1`
- `SULTAN_TELEGRAM_BOT_TOKEN=...`
- `SULTAN_TELEGRAM_CHAT_ID=...`

Fallback yang juga didukung:

- kalau env Telegram kosong, loader akan mencoba baca backup config lama di `BACKUP_-02-2026_23-20/forex/telegram_config.py`
- path fallback bisa dioverride dengan `SULTAN_TELEGRAM_LEGACY_CONFIG_PATH`
- fallback bisa dimatikan dengan `SULTAN_TELEGRAM_ALLOW_LEGACY_CONFIG=0`

Environment OpenRouter opsional:

- `SULTAN_OPENROUTER_API_KEY=...`
- `SULTAN_OPENROUTER_MODEL=openai/gpt-4o-mini`
- `SULTAN_OPENROUTER_ENABLED=1`

## Jalur VPS Autonomous

Supaya engine nanti enak dipasang di VPS dan jalan mandiri, sekarang ada tiga runner utama:

- `python sultan_engine_v2/run_live_mt5.py`
   - untuk terminal dashboard interaktif.
- `python sultan_engine_v2/run_mt5_service.py`
   - untuk MT5 headless autonomous service tanpa UI.
- `python sultan_engine_v2/run_vps_stack.py`
   - untuk jalankan MT5 autonomous service plus operator Telegram dalam satu process VPS.

Environment testing yang berguna:

- `SULTAN_SERVICE_MAX_CYCLES=1`
   - buat smoke test `run_mt5_service.py` satu cycle.
- `SULTAN_VPS_STACK_MAX_CYCLES=1`
   - buat smoke test `run_vps_stack.py` satu cycle.

## Portable Config

Supaya repo ini enak dipindah antar local PC dan Windows VPS, runner sekarang bisa auto-load file config dari root project dengan urutan:

1. `.env`
2. `sultan.env`
3. `.env.local`

File yang paling praktis untuk dibawa antar mesin adalah `sultan.env`.

Template awal sudah disediakan di:

- `sultan.env.example`

Contoh flow:

1. copy `sultan.env.example` jadi `sultan.env`
2. isi token/path sesuai mesin yang dipakai
3. jalankan launcher `.bat` seperti biasa

Catatan keamanan:

- default launcher sekarang tidak otomatis buka `ngrok`,
- web monitor bisa dikunci dengan `SULTAN_WEB_MONITOR_TOKEN`,
- web monitor juga bisa diarahkan ke file performa custom dengan `SULTAN_MONITOR_PERFORMANCE_PATH`,
- kalau token diisi, akses monitor harus pakai query `?token=...` atau header `X-Sultan-Token`.

Tambahan hardening yang sekarang sudah ada:

- single-instance file lock untuk service MT5, operator Telegram, dan VPS stack,
- service heartbeat JSON di folder `sultan_engine_v2/runtime/state/`,
- status snapshot sekarang expose `execution.entries_enabled` dan `health.bootstrapped`,
- tracker performa/burn-in per venue untuk nilai return, drawdown, uptime, dan readiness paper/demo,
- operator tetap bisa pause/resume entry tanpa mematikan lifecycle posisi yang sudah terbuka.

Launcher Windows cepat:

- `run_sultan_vps.bat`
- `run_sultan_production.bat`
- `run_sultan_crypto.bat`
- `run_sultan_all.bat`
- `run_sultan_reset.bat`
- `run_prepare_vps_bundle.bat`
- `setup_sultan_vps.bat`

Catatan operasi:

- untuk VPS yang benar-benar mandiri, jalur paling aman sekarang adalah `run_vps_stack.py` atau `run_mt5_service.py` + `run_telegram_operator.py`,
- kalau ada instance ganda, lock file akan mencegah proses lokal dobel jalan bersamaan,
- status terakhir service bisa dibaca dari file heartbeat di `sultan_engine_v2/runtime/state/`.

## Mode Produksi 24 Jam

Sekarang ada jalur supervisor khusus untuk mode 24 jam:

- `python sultan_engine_v2/run_production_stack.py`
  - menjalankan `run_vps_stack.py` sebagai child process,
  - memantau heartbeat child,
  - restart otomatis kalau child exit atau heartbeat macet.

Launcher Windows yang paling cocok untuk operasi nonstop:

- `run_sultan_production.bat`

Fitur tambahan mode ini:

- production guard untuk auto-pause entry kalau drawdown, daily loss, disconnect, atau service error lewat batas,
- daily report ke Telegram pada jam yang dikonfigurasi,
- burn-in tracker untuk audit performa paper/demo secara periodik,
- heartbeat supervisor sendiri di `sultan_engine_v2/runtime/state/production_stack_<symbol>_status.json`.

Environment penting untuk mode ini:

- `SULTAN_PRODUCTION_GUARD_ENABLED=1`
- `SULTAN_DAILY_REPORT_ENABLED=1`
- `SULTAN_DAILY_REPORT_HOUR=21`
- `SULTAN_REPORT_UTC_OFFSET=+07:00`
- `SULTAN_KILL_SWITCH_ENABLED=1`
- `SULTAN_KILL_SWITCH_MAX_EQUITY_DRAWDOWN_PERCENT=8`
- `SULTAN_KILL_SWITCH_MAX_DAILY_LOSS_PERCENT=5`
- `SULTAN_KILL_SWITCH_MAX_DISCONNECT_MINUTES=15`
- `SULTAN_PRODUCTION_HEARTBEAT_TIMEOUT_SECONDS=180`
- `SULTAN_PRODUCTION_RESTART_BACKOFF_SECONDS=10`

Prinsip safety-nya:

- kill-switch hanya auto-`PAUSE` new entry,
- lifecycle posisi terbuka tetap berjalan,
- resume tetap butuh intervensi operator kalau kondisi sudah aman.

## All-In-One Launcher

Kalau mau sekali klik langsung jalan semua, sekarang ada:

- `run_sultan_all.bat`

Flow launcher ini:

- start MT5 production supervisor,
- start Binance Futures production supervisor,
- start unified fleet web monitor,
- optional start ngrok ke unified fleet monitor,
- current CMD berubah jadi unified fleet dashboard.

Environment penting:

- `MT5_RUNTIME_SYMBOL=XAUUSD`
- `BINANCE_RUNTIME_SYMBOL=BTCUSDT`
- `SULTAN_FLEET_MONITOR_PORT=5000`
- `SULTAN_WEB_MONITOR_TOKEN=...`
- `SULTAN_START_NGROK=1`

Catatan:

- one-click mode ini tidak mengubah core CMP,
- web UI fleet dirancang untuk pantau dua venue dari satu halaman,
- terminal dashboard fleet dirancang untuk satu CMD, bukan dua dashboard terpisah.

## Reset Burn-In

Kalau mau mulai uji dari nol lagi tanpa menyentuh config, gunakan:

- `python sultan_engine_v2/run_reset_runtime_state.py`
- `run_sultan_reset.bat`

Reset ini akan mengarsipkan state paper/demo aktif ke folder `sultan_engine_v2/runtime/archive/` lalu membersihkan snapshot, ledger, performance tracker, paper account Binance, dan stale lock file.

Catatan:

- reset akan menolak berjalan kalau process Sultan masih aktif,
- `sultan.env`, token, mapping TradingView, dan core CMP tidak disentuh.

## Panduan Agent VPS

Panduan lengkap untuk human/operator dan agent AI VPS sekarang ada di:

- `sultan_engine_v2/docs/vps_agent_runbook.md`

Dokumen ini menjelaskan:

- tujuan fase paper/burn-in,
- batas area yang boleh di-tune,
- area core CMP yang tidak boleh diubah,
- SOP reset,
- jalur monitoring, dashboard, dan evaluasi performa.

## Bundle VPS

Kalau mau pindah workspace ke VPS tanpa membawa `.tmp`, `.venv`, cache, dan residue runtime lokal, gunakan:

- `python sultan_engine_v2/run_prepare_vps_bundle.py`
- `run_prepare_vps_bundle.bat`

Output-nya akan dibuat di folder `_deploy/` sebagai:

- folder staging bersih,
- file zip siap transfer,
- `VPS_TRANSFER_GUIDE.md`,
- `requirements.vps.txt` kalau export dependency dari venv lokal berhasil.

Catatan setup di mesin tujuan:

- bundle tidak membawa `.venv`,
- jalankan `setup_sultan_vps.bat` dulu setelah extract,
- setelah setup selesai baru jalankan launcher seperti `run_sultan_all.bat`.

## Burn-In Tracker

Sekarang stack MT5 dan Binance punya tracker performa yang berjalan per cycle tanpa mengubah core CMP.

Yang dicatat:

- balance/equity baseline dan delta,
- net return percent,
- max drawdown,
- connected ratio dan bootstrapped ratio,
- execution success/fail count,
- closed trade, win rate, dan realized PnL kalau venue menyediakan metric trade.

Status burn-in yang dipakai:

- `COLLECTING_DATA`
- `PAPER_STABLE`
- `READY_FOR_SMALL_RISK_TRIAL`
- `NOT_READY`

File utamanya disimpan di:

- `sultan_engine_v2/runtime/state/performance_mt5_<symbol>.json`
- `sultan_engine_v2/runtime/state/performance_binance_<symbol>.json`

History sampling disimpan di:

- `sultan_engine_v2/runtime/history/performance_<venue>_<symbol>.jsonl`

Environment penting:

- `SULTAN_PERFORMANCE_TRACKER_ENABLED=1`
- `SULTAN_BURN_IN_REPORT_ENABLED=1`
- `SULTAN_BURN_IN_REPORT_INTERVAL_MINUTES=240`
- `SULTAN_BURN_IN_MIN_RUNTIME_HOURS=24`
- `SULTAN_BURN_IN_MIN_CYCLES=200`
- `SULTAN_BURN_IN_MIN_CLOSED_TRADES=5`
- `SULTAN_BURN_IN_MAX_DRAWDOWN_PERCENT=8`

Catatan:

- tracker ini dipakai untuk baca performa paper/demo lebih cepat,
- dia tidak mengubah grammar CMP-VR-CF,
- untuk MT5 demo yang tidak expose trade metric lengkap di snapshot, status maksimum normalnya `PAPER_STABLE` sampai observasi tambahan dianggap cukup.

## Crypto Paper On Real Market

Sekarang jalur crypto sudah punya mode yang paling aman untuk fase observasi:

- market data tetap real dari Binance,
- order tetap paper/simulasi,
- balance/equity paper tersimpan dan di-mark-to-market,
- TP/SL paper bisa auto-close di harga market yang dibaca runtime.

Runner yang tersedia:

- `python sultan_engine_v2/run_live_binance.py`
- `python sultan_engine_v2/run_binance_service.py`
- `python sultan_engine_v2/run_binance_stack.py`
- `python sultan_engine_v2/run_binance_production.py`

Launcher Windows:

- `run_sultan_crypto.bat`

Catatan penting:

- mode paper Binance ini adalah simulasi directional account, bukan replika exact spot account Binance,
- short di paper mode tetap diizinkan secara default supaya grammar BUY/SELL engine tetap bisa diuji,
- kalau mau mendekati spot murni, set `SULTAN_BINANCE_PAPER_ALLOW_SHORT=0`.

## TradingView Mapping

Fokus venue tetap hanya:

- `MT5`
- `BINANCE`

TradingView sekarang diposisikan sebagai layer analisa/replay, bukan venue eksekusi.

Jalur yang tersedia:

- `python sultan_engine_v2/run_tradingview_replay.py <TICKER> <TF=csv_path> [<TF=csv_path> ...]`

Contoh:

- `python sultan_engine_v2/run_tradingview_replay.py OANDA:XAUUSD D1=d1.csv H4=h4.csv H1=h1.csv`
- `python sultan_engine_v2/run_tradingview_replay.py BINANCE:BTCUSDT H4=h4.csv H1=h1.csv M30=m30.csv`

Fungsi jalur ini:

- map ticker TradingView ke symbol engine MT5/Binance,
- parse export CSV TradingView,
- bootstrap CMP pada timeframe yang diberikan,
- build snapshot replay untuk audit manager dan validasi chart.

Override mapping opsional:

- isi file seperti [tradingview_symbol_map.example.json](/f:/AI%20STORY%20CHANNEL/tradingview_symbol_map.example.json)
- set `SULTAN_TRADINGVIEW_MAP_PATH=...`

Prinsipnya:

- TradingView hanya bantu validasi analisa chart,
- execution tetap cuma lewat MT5 atau Binance,
- core CMP tetap satu dan tidak berubah.
