# Sacred Doctrine → Engine Map

Dokumen ini menerjemahkan doctrine Commander ke perilaku engine Sultan v2 tanpa mengubah core frozen CMP/VR/CF. Tujuan dokumen ini adalah:

1. menetapkan apa yang dianggap hukum doctrine,
2. memetakan hukum itu ke implementasi engine saat ini,
3. menandai area yang sudah selaras,
4. menandai area yang masih berpotensi bocor di layer runtime / analytics / replay / execution.

---

## 1. Sacred Doctrine Ringkas

### 1.1 Hukum Besi
- tidak ada CF sebelum VR
- tidak ada CMP setelah VR
- tidak ada entri tanpa CF

### 1.2 Storyline baca setup
- VR ditemukan dulu
- dari VR terbaca CMP aktif yang sedang diserang
- lalu CF mengonfirmasi balik ke CMP
- baru entry boleh dieksekusi

### 1.3 Hukum pembentukan breakout
- line chart / body close adalah penentu
- minor SNR adalah level valid
- wick tidak menentukan breakout sah
- buy sah: close di atas minor resistance
- sell sah: close di bawah minor support

### 1.4 Hukum pengawalan
- M5 menjaga M15
- M15 menjaga H1
- H1 menjaga H4
- H4 menjaga Daily
- Daily menjaga Weekly
- pengawal valid adalah TF yang punya minimal 3 candle pada TF atasnya
- M30 tidak sah sebagai pengawal H1 karena hanya terdiri dari 2 candle

### 1.5 Fractal cascade
- jika pengawal gagal menegakkan CF, tekanan naik ke TF atas
- semakin panjang cascade gagal, semakin besar ledakan setup

### 1.6 Prediction law
- breakout berulang dari TF bawah dapat membentuk warna / arah candle TF atas
- entry TF kecil dipakai untuk mengambil target TF besar

### 1.7 Exit law
- 1x breakout lawan arah = waspada
- 2x breakout lawan arah = keluar / take profit

### 1.8 Trap utama
- setiap VR harus memaksa reset pembacaan CMP
- failure terbesar sering muncul saat sistem masih menganggap CMP lama aktif setelah VR kuat terjadi

---

## 2. Map Ke Engine Saat Ini

## 2.1 Breakout detector

### Doctrine
Breakout sah ditentukan oleh body close terhadap minor support/resistance.

### Engine
File: `sultan_engine_v2/core/breakout_detector.py`

Yang sudah selaras:
- minor support/resistance dibentuk dari transisi body bearish↔bullish
- breakout bootstrap dan update memakai `close > resistance` atau `close < support`
- wick bukan penentu utama

Status:
- SELARAS dengan doctrine close-price / minor-SNR breakout

Catatan:
- ini adalah fondasi paling kuat dan tidak perlu diacak-acak selama doctrine tidak berubah.

---

## 2.2 Role grammar: CMP / VR / CF

### Doctrine
- VR datang dulu sebagai tanda serangan
- CF hanya valid sebagai pemulihan balik ke CMP yang diserang
- tidak boleh ada entry sebelum CF

### Engine
Files:
- `sultan_engine_v2/core/doctrine.py`
- `sultan_engine_v2/core/state_engine.py`
- `sultan_engine_v2/runtime/execution_engine.py`

Yang sudah selaras:
- engine punya role formal `CMP`, `VR`, `CF`
- engine punya `UNDER_ATTACK`, `RECOVERED_BY_CF`, `IN_CONTROL`
- planner hanya membentuk intent saat parent sequence `RECOVERED_BY_CF`
- artinya entry memang baru lahir setelah CF terkonfirmasi

Status:
- SELARAS secara struktur tinggi

Catatan:
- ini alasan kenapa engine tidak spam entry CMP-only.

---

## 2.3 Status fokus setup

### Doctrine
Urutan baca aktif adalah VR -> CMP -> CF.

### Engine
File: `sultan_engine_v2/core/state_engine.py`

Engine saat ini menurunkan focus status menjadi:
- `NO_ACTIVE_VR`
- `WAIT_CF`
- `CF_CONFIRMED`

Makna praktis:
- `NO_ACTIVE_VR` = belum ada serangan aktif, hanya ikuti rantai CMP
- `WAIT_CF` = VR aktif, sedang menunggu CF yang sah
- `CF_CONFIRMED` = parent sudah dipulihkan oleh CF, entry boleh dipertimbangkan

Status:
- SELARAS

---

## 2.4 Timeframe guardian doctrine vs relation map engine

### Doctrine
Guardian valid:
- M5 -> M15
- M15 -> H1
- H1 -> H4
- H4 -> Daily
- Daily -> Weekly

Guardian tidak valid:
- M30 -> H1 (gugur secara matematika 2 candle)

### Engine saat ini
File: `sultan_engine_v2/core/timeframe_relations.py`

Map sekarang:
- `H1 -> M30` sebagai VR child / low-risk CF path
- `M30 -> M15`
- `M15 -> M5`

Artinya engine internal masih menganggap M30 punya peran struktural besar di bawah H1.

Status:
- BELUM 100% SELARAS dengan doctrine Commander terbaru

Implikasi besar:
- doctrine Commander menempatkan M15 sebagai pengawal sah H1
- engine saat ini masih memberi jurisdiksi penting ke M30
- ini bisa menjelaskan kenapa lane `M15 execution timeframe` pada stress test tampil lemah / bocor, karena domain confirmation dan target path belum sepenuhnya mematuhi hierarchy pengawalan Commander

Catatan penting:
- file ini termasuk area core doctrine yang saat ini dianggap frozen
- jadi mismatch ini harus diperlakukan sebagai TEMUAN doctrine gap, bukan langsung diubah sembarang di runtime

---

## 2.5 High-risk vs low-risk CF

### Doctrine
Ringkasan doctrine malam ini menekankan:
- VR harus punya CF yang jelas untuk parent yang diserang
- entry lebih presisi datang dari TF kecil, tapi tetap harus tunduk ke hukum pengawalan

### Engine saat ini
File: `sultan_engine_v2/core/timeframe_relations.py`

Contoh mapping sekarang:
- parent H1:
  - low-risk CF = M30
  - high-risk CF = M15
- parent M30:
  - low-risk CF = M15
  - high-risk CF = M5

Status:
- SEBAGIAN SELARAS, SEBAGIAN BELUM

Yang selaras:
- engine memang membedakan agresivitas entry lane
- engine mencoba memberi jalur lebih cepat dan lebih aman

Yang belum selaras penuh:
- konsep “low-risk” untuk H1 masih bergantung pada M30, padahal doctrine guardian Commander menolak M30 sebagai pengawal sah H1

Ini adalah salah satu kandidat kebocoran edge terbesar.

---

## 2.6 Prediction law dan target timeframe

### Doctrine
- breakout berulang TF kecil bisa memprediksi warna / arah TF atas
- entry kecil boleh mengejar target besar

### Engine saat ini
File: `sultan_engine_v2/core/timeframe_relations.py`

Target map sekarang:
- M5 -> M15
- M15 -> M30
- M30 -> H1
- H1 -> H4
- H4 -> D1

Status:
- SELARAS secara umum dengan ide projection ke TF atas
- BELUM lengkap terhadap formula Commander “2x breakout bawah membentuk candle atas”

Catatan:
- engine saat ini memakai target mapping statis
- belum ada modul eksplisit yang menghitung “2x breakout bawah = probabilitas terbentuk candle atas” sebagai analytic law tersendiri
- ini cocok dijadikan layer analytics / report, bukan harus mengubah core lebih dulu

---

## 2.7 Exit law vs current replay logic

### Doctrine
- 1x breakout lawan arah = waspada
- 2x breakout lawan arah = take profit
- butuh penjagaan aktif 24/7

### Engine saat ini
Files:
- `sultan_engine_v2/adapters/mt5/execution/position_manager.py`
- replay harness baru di runtime

Yang sekarang sudah ada:
- structural invalidation handling
- break-even, partial close, trailing pada position manager MT5
- replay harness memakai bracket exit sederhana: stop loss / take profit / force close

Status:
- LIVE position manager: SEBAGIAN SELARAS
- replay backtest harness: BELUM SELARAS PENUH

Alasannya:
- replay harness sekarang masih memakai bracket-based exits
- belum memodelkan doctrine exit law “2x BO lawan arah = keluar” sebagai mekanisme exit utama

Ini penting:
- hasil stress test saat ini lebih merepresentasikan ketajaman setup entry + static bracket behavior
- belum merepresentasikan full Commander exit doctrine

---

## 2.8 Reset CMP after VR

### Doctrine
Commander menekankan bahwa begitu VR kuat muncul, CMP lama tidak boleh dibaca mentah lagi.

### Engine saat ini
File: `sultan_engine_v2/core/state_engine.py`

Yang sudah ada:
- parent sequence berubah ke `UNDER_ATTACK`
- active controller berpindah ke child penyerang
- setup focus menjadi `WAIT_CF`

Status:
- SELARAS secara state machine tinggi

Catatan:
- secara logika event, engine sudah tahu parent sedang diserang
- tapi analytics/operator UX masih perlu menonjolkan bahwa “CMP lama tidak boleh dipakai entry” supaya operator tidak salah tafsir dari dashboard

---

## 3. Hasil Stress Test vs Doctrine

Artifacts:
- `sultan_engine_v2/runtime/state/mt5_replay_backtest_xauusd.json`
- `sultan_engine_v2/runtime/state/mt5_stress_matrix_xauusd.json`

### Ringkasan hasil
- M5 execution lane lebih baik dari M15
- lookback 500: M5 profitable across tested spreads
- lookback 1000: M5 melemah, nyaris flat / sedikit positif pada friction ringan
- lookback 2000: M5 negatif across tested spreads
- M15 lane konsisten lemah / negatif

### Interpretasi doctrine-level
1. Engine punya edge taktis jangka pendek
   - artinya grammar core masih punya daya baca market
2. Edge belum robust lintas history panjang
   - artinya ada kebocoran pada fidelity operational layer
3. M15 lane lemah
   - ini konsisten dengan temuan bahwa guardian hierarchy engine belum sepenuhnya sama dengan doctrine Commander
4. Spread sensitivity nyata
   - berarti entry timing / lane aggressiveness sangat menentukan

---

## 4. Commander-Grade Gap List

Berikut daftar gap yang sekarang paling penting tanpa menyentuh core frozen secara sembrono:

### Gap A — Guardian hierarchy doctrine belum 100% termodelkan
- doctrine: M15 jaga H1, M30 gugur sebagai pengawal H1
- engine current mapping: H1 masih pakai M30 sebagai low-risk path

### Gap B — Exit doctrine belum masuk penuh ke replay
- doctrine: 2x BO lawan arah = keluar
- replay sekarang: masih SL/TP static

### Gap C — Prediction law belum jadi analytics module
- doctrine: 2x breakout bawah memprediksi TF atas
- engine: target mapping statis, belum punya scoring / telemetry prediction law

### Gap D — Audit language belum sepenuhnya pakai bahasa kitab Commander
- engine sudah punya status teknikal
- tapi belum semua report/analytics mengekspresikan gap menurut hukum Commander secara eksplisit

---

## 5. Apa Yang Boleh Dikerjakan Tanpa Melanggar Core

Karena core doctrine frozen, langkah aman berikutnya adalah:

1. tambah analytics layer untuk klasifikasi trade replay:
   - parent timeframe
   - confirmed CF timeframe
   - entry_kind high-risk / low-risk
   - setup_focus_status saat trade lahir
   - outcome PnL
   - friction sensitivity

2. tambah doctrine-audit report:
   - tandai trade yang lahir dari lane yang secara doctrine dianggap lemah
   - tandai apakah trade memakai path M30->H1
   - tandai apakah winner lebih sering datang dari lane M5->M15->H1 dibanding lane lain

3. tambah replay exit variant:
   - mode bracket klasik
   - mode doctrine exit (2x opposite breakout)
   - lalu bandingkan robustness

4. baru setelah evidence kuat terkumpul, ajukan perubahan core doctrine map bila benar-benar perlu

---

## 6. Kesimpulan Brutal

Engine Sultan v2 saat ini:
- sudah selaras dengan doctrine breakout close-based minor-SNR,
- sudah selaras dengan prinsip no-entry-before-CF,
- sudah selaras dengan state machine UNDER_ATTACK -> WAIT_CF -> CF_CONFIRMED,
- tapi BELUM 100% setia pada hierarchy pengawalan Commander terbaru,
- dan BELUM memodelkan full exit law Commander di replay harness.

Makna praktis:
- engine bukan tumpul,
- engine punya taji recent short-horizon,
- namun ketajamannya belum stabil cukup lama untuk disebut final commander-grade.

Arah kerja paling aman berikutnya:
- jangan ubah jiwa sistem,
- perkeras audit doctrine,
- uji exit doctrine,
- dan telanjangi lane mana yang benar-benar setia pada hukum Commander.
