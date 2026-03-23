# MT5 Adapter

Adapter MT5 pada Sultan Engine V2 harus otomatis membaca lingkungan MT5 user tanpa memaksa pemilihan broker atau jenis akun tertentu.

## Requirement Utama

1. Otomatis mendeteksi terminal MT5 yang aktif atau terpasang.
2. Otomatis membaca account yang sedang login pada terminal tersebut.
3. Tidak mengikat engine ke broker tertentu.
4. Tidak mengikat engine ke currency account tertentu seperti IDR, USD, cent, atau variasi lainnya.
5. Otomatis me-resolve symbol broker-specific seperti suffix/prefix.
6. Fokus v1 adalah membaca terminal/account MT5 yang sedang aktif saja, tanpa user pilih manual.
7. Harus tetap sanggup berkembang ke banyak account di masa depan, tetapi dengan desain yang realistis terhadap limitasi Python MT5 API.

## Batas Teknis Penting

MetaTrader5 Python binding secara praktik menempel ke satu terminal session per process.

Artinya:

- satu process worker = satu koneksi terminal/account aktif,
- v1 paling sehat memang membaca account aktif saja,
- multi-account tidak boleh dipaksa lewat satu stateful process yang sama,
- untuk banyak account/broker di masa depan, kita butuh fleet manager yang menjalankan beberapa worker secara terpisah.

## Arsitektur Adapter

### discovery

Mencari:

- terminal MT5 yang sedang aktif,
- jalur `terminal64.exe` yang terpasang,
- metadata terminal dan account aktif.

### session

Membuka sesi ke satu terminal tertentu dan membaca:

- `account_info`,
- `terminal_info`,
- symbol availability,
- ticks,
- rates,
- positions,
- orders.

### symbols

Me-resolve nama symbol broker-specific secara otomatis.

Tidak cukup hanya hardcode beberapa suffix; v2 harus bisa scan symbol list broker dan mencocokkan base instrument secara lebih umum.

### fleet

Mengelola banyak terminal/account secara paralel.

Setiap worker mewakili satu account/terminal.

Pada v1 folder ini belum menjadi jalur utama operasi. Ini disiapkan sebagai ekspansi setelah mode single-active-session stabil.

## Prinsip Design

1. Core engine tidak tahu detail MT5.
2. MT5 adapter tidak mengubah grammar trading.
3. Multi-account dilakukan via banyak worker, bukan satu session campur aduk.
4. Semua metadata account harus dibawa ke journal dan manager layer.

## Minimal Metadata Yang Harus Dibaca

- login
- server
- company / broker
- currency
- leverage
- balance
- equity
- margin mode
- terminal path
- symbol list / mapped symbols

## V1 Behavior

1. Bisa attach ke active/default MT5 secara otomatis.
2. Bisa resolve symbol broker-specific.
3. Bisa baca account aktif tanpa user pilih manual.
4. Hanya membaca terminal/account yang sedang aktif.
5. Siap diperluas ke multi-account fleet nanti.

## V2+ Behavior

1. Discovery banyak terminal.
2. Worker per account.
3. Aggregated fleet status untuk AI manager.