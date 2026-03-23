# Sultan VPS Agent Runbook

Dokumen ini dibuat supaya agent AI yang berjalan di VPS paham tujuan sistem, batas teknikal yang tidak boleh diubah, area tune-up yang boleh disentuh, dan cara membaca performa engine tanpa merusak SOP CMP-VR-CF.

## Misi Utama

Tujuan fase sekarang bukan langsung mengejar live money besar.

Tujuan fase ini:

1. Menjalankan engine 24 jam secara stabil di local PC atau Windows VPS.
2. Menguji performa real market dengan saldo virtual/paper.
3. Mengumpulkan data burn-in yang cukup untuk membaca kualitas stabilitas, win rate, drawdown, dan kualitas eksekusi.
4. Menyiapkan fondasi supaya nanti transisi ke small-risk live bisa dilakukan dengan disiplin.

## Batas Yang Tidak Boleh Dilanggar

Agent AI di VPS **tidak boleh mengubah core teknikal CMP**.

File dan area yang dianggap inti dan harus diperlakukan sebagai frozen doctrine:

- `sultan_engine_v2/core/state_engine.py`
- `sultan_engine_v2/core/breakout_detector.py`
- `sultan_engine_v2/core/doctrine.py`
- seluruh grammar CMP -> VR -> CF
- seluruh parent-child control sequence
- makna `WAIT_CF`, `CF_CONFIRMED`, `NO_ACTIVE_VR`, dan status teknikal lain

Kalau ada tune-up, tune-up hanya boleh dilakukan di lapisan:

- runtime
- production supervisor
- production guard
- performance tracker
- dashboard
- fleet monitor
- config env
- data adapter venue
- logging, reporting, analytics

## Peta Sistem

Arsitektur yang dipakai sekarang:

- `MT5` untuk `CFD`
- `BINANCE` untuk `FUTURES`
- `TradingView` hanya untuk `mapping` dan `replay analisa`, bukan venue eksekusi

Core engine tetap satu:

- CMP
- VR
- CF
- hierarchy parent-child

Yang berbeda hanya jalur adapter data dan jalur eksekusi.

## File Operasional Paling Penting

Launcher utama:

- `run_sultan_all.bat`
- `run_sultan_production.bat`
- `run_sultan_crypto.bat`
- `run_sultan_reset.bat`

Runner Python:

- `sultan_engine_v2/run_production_stack.py`
- `sultan_engine_v2/run_binance_production.py`
- `sultan_engine_v2/run_fleet_monitor.py`
- `sultan_engine_v2/run_fleet_dashboard.py`
- `sultan_engine_v2/run_reset_runtime_state.py`

State dan telemetry:

- `sultan_engine_v2/runtime/state/latest_snapshot.json`
- `sultan_engine_v2/runtime/state/binance_<symbol>_snapshot.json`
- `sultan_engine_v2/runtime/state/vps_stack_<symbol>_status.json`
- `sultan_engine_v2/runtime/state/binance_stack_<symbol>_status.json`
- `sultan_engine_v2/runtime/state/performance_mt5_<symbol>.json`
- `sultan_engine_v2/runtime/state/performance_binance_<symbol>.json`
- `sultan_engine_v2/runtime/history/performance_<venue>_<symbol>.jsonl`

## Cara Menjalankan Sistem

Mode paling lengkap:

- jalankan `run_sultan_all.bat`

Flow satu klik ini:

1. start MT5 production supervisor
2. start Binance Futures production supervisor
3. start fleet web UI
4. start ngrok jika `SULTAN_START_NGROK=1`
5. jadikan CMD utama sebagai dashboard gabungan

Web UI default:

- `http://127.0.0.1:5000`

Kalau monitor token aktif, akses publik harus pakai:

- `?token=<SULTAN_WEB_MONITOR_TOKEN>`

## Prinsip Monitoring

Dashboard CMD dan fleet web UI harus dipakai untuk membaca:

- status service
- connected / disconnected
- burn-in status
- burn-in score
- balance / equity
- spread / bid
- open structure
- `CMP / VR / CF Matrix`
- `Parent Control`
- log terbaru per venue

Kalau dashboard hidup tapi matrix CMP kosong, prioritas cek:

1. snapshot venue ter-update atau tidak
2. symbol mapping benar atau tidak
3. venue connected atau tidak
4. bootstrap selesai atau tidak

## Tujuan Tune-Up Yang Diizinkan

Tune-up boleh dilakukan untuk memperbaiki hal-hal ini:

- portability antar mesin
- auto-start dan auto-restart
- watchdog / supervisor
- telemetry dan observability
- dashboard / web UI / ngrok
- retry dan failure handling
- security token monitor
- performance tracker
- burn-in reporting
- paper account realism
- journaling dan analytics

Tune-up tidak boleh mengubah keputusan teknikal CMP itu sendiri.

## Fokus Evaluasi Burn-In

Fase paper/demo harus menghasilkan jawaban untuk pertanyaan berikut:

1. Apakah engine benar-benar hidup 24 jam tanpa freeze?
2. Apakah reconnect MT5 dan data Binance stabil?
3. Apakah dashboard dan web UI selalu punya state terbaru?
4. Apakah auto-entry benar-benar jalan saat setup valid?
5. Apakah error network tidak menjatuhkan runtime utama?
6. Apakah drawdown paper dan win rate memberi sinyal strategi layak dinaikkan ke small-risk live?

Metric utama yang harus dibaca:

- `runtime_hours`
- `total_cycles`
- `connected_ratio_percent`
- `bootstrapped_ratio_percent`
- `net_return_percent`
- `max_drawdown_percent`
- `closed_trades`
- `win_rate_percent`
- `executions_success`
- `executions_failed`

## Kapan Agent VPS Harus Tune-Up

Agent AI di VPS boleh melakukan tune-up saat menemukan gejala seperti:

- service sering restart sendiri
- lock file stale terus muncul
- snapshot berhenti update
- burn-in status lama di `COLLECTING_DATA` karena masalah operasional, bukan karena belum cukup data
- web UI tidak aman atau tidak bisa diakses lewat ngrok
- Telegram report gagal terus
- Binance paper state rusak atau tidak konsisten
- supervisor child path salah
- dashboard kehilangan urutan CMP / VR / CF

Kalau masalahnya murni performa strategi, agent **tidak boleh** mengakali CMP.
Yang boleh dilakukan hanya memperbaiki observability dan kualitas pengukuran.

## Jalur Tuning Yang Direkomendasikan

Urutan tuning yang aman:

1. stabilitas runtime
2. kualitas restart / watchdog
3. kejelasan dashboard dan fleet web UI
4. kualitas burn-in tracker dan report
5. kualitas paper account dan analytics
6. keamanan akses monitor/ngrok
7. baru setelah itu evaluasi apakah small-risk live layak dicoba

## SOP Reset Sebelum Tes Dari Nol

Kalau mau mulai burn-in baru atau pindah ke VPS baru, reset state paper/demo dulu.

Command:

```powershell
.venv\Scripts\python.exe sultan_engine_v2\run_reset_runtime_state.py
```

Atau:

```bat
run_sultan_reset.bat
```

Reset ini akan:

- mengarsipkan snapshot runtime
- mengarsipkan status service/stack/guard
- mengarsipkan performance tracker state
- mengarsipkan performance history
- mengarsipkan execution ledger
- mengarsipkan Binance paper account state
- membersihkan stale lock file

Reset ini **tidak** menyentuh:

- `sultan.env`
- token Telegram
- token monitor web
- config ngrok
- mapping TradingView
- core CMP

Kalau proses Sultan masih hidup, reset harus dibatalkan dulu.

## Tugas Agent AI Di VPS

Agent AI di VPS idealnya bekerja dengan prioritas berikut:

1. menjaga sistem tetap hidup
2. menjaga data telemetry tetap bersih
3. menjaga akses monitor tetap aman
4. membaca burn-in report secara jujur
5. mengusulkan tune-up operasional saat perlu
6. menolak perubahan yang mengubah doctrine CMP

## Target Akhir Fase Ini

Target akhirnya bukan sekadar "bot menyala".

Target akhir fase ini:

- engine bisa dipindah ke mesin mana pun
- engine bisa jalan 24 jam
- dua venue bisa dipantau dari satu dashboard dan satu web UI
- burn-in paper menghasilkan data performa yang bisa dipercaya
- ada dasar objektif untuk menilai apakah win rate, drawdown, dan stabilitas layak masuk small-risk live

## Ringkasan Singkat Untuk Agent

Kalau agent hanya membaca satu bagian, pakai ringkasan ini:

- jangan ubah core CMP-VR-CF
- MT5 adalah lane CFD
- Binance adalah lane Futures
- TradingView hanya untuk mapping/replay
- fokus tune-up hanya pada operasional, safety, observability, security, dan analytics
- gunakan `run_sultan_all.bat` untuk mode gabungan
- gunakan `run_sultan_reset.bat` sebelum burn-in baru
- nilai keberhasilan fase ini dari stabilitas + kualitas data burn-in, bukan dari asumsi profit instan
