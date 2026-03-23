# Architecture

## Core Decision

Pilihan yang dipakai adalah:

- satu core engine,
- dua venue adapter,
- satu AI manager layer.

Bukan dua engine terpisah.

## Kenapa Satu Core

Trading SOP user tidak berubah antar venue:

1. Breakout aktif selalu yang paling kanan.
2. CMP tidak pernah netral.
3. VR dan CF adalah relasi antar-timeframe.
4. Trade jurisdiction ditentukan oleh domain parent.
5. Target primer tunduk ke SNR timeframe di atas CF/domain entry.

Karena grammar sama, yang dipisah hanya layer koneksi ke venue.

## Logical Layers

### 1. Trading Core

Tanggung jawab:

- breakout marking,
- TF CMP state,
- CMP MASTER resolution,
- VR/CF relation,
- market-led domain resolution,
- target SNR resolution,
- invalidation logic,
- deterministic journals.

Core harus broker-agnostic.

### 2. Venue Adapters

#### MT5 Adapter

Dipakai untuk:

- CFD forex,
- CFD indices/commodities bila perlu,
- broker-specific symbol resolution,
- MT5 order lifecycle.

#### Exchange Adapter

Dipakai untuk:

- crypto spot/perp,
- fee model maker/taker,
- tick size dan lot size exchange,
- exchange-specific data seperti funding atau open interest bila tersedia.

## Runtime

Runtime bertugas menjalankan:

- market data loop,
- candle building,
- event dispatch ke core,
- paper/live execution,
- state snapshots,
- journals,
- health monitoring.

Satu runtime instance boleh diarahkan ke satu venue atau satu portfolio mode tertentu.

## AI Manager Layer

Layer ini terinspirasi konsep Hermes, tetapi dibangun sendiri dan khusus untuk SOP user.

Tugas utamanya:

- monitor engine,
- audit apakah engine mengikuti SOP,
- jelaskan kesalahan logic/state,
- sarankan tune-up terbatas,
- buat report,
- kirim chat ke Telegram,
- handle restart, pause, resume, mode change, dan ops VPS.

Pada v1, AI manager bukan sumber keputusan trading bebas.

## Recommended Rollout

1. Build both venues into the architecture.
2. Implement paper mode first.
3. Implement live adapter one by one.
4. Keep AI manager as supervisor first.

## Current Recommendation

Jawaban atas pertanyaan venue adalah:

- buat keduanya dalam satu engine,
- tapi jangan dicampur dalam satu adapter,
- pisahkan MT5 dan exchange sebagai connector terpisah di atas core yang sama.