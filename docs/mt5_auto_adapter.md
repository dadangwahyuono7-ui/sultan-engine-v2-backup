# MT5 Auto Adapter Doctrine

## Target User Requirement

Engine harus membaca MT5 user secara otomatis seperti backup live engine:

- broker apa pun,
- account apa pun,
- currency account apa pun,
- symbol broker-specific apa pun,
- tanpa mewajibkan user pilih broker/account secara manual pada flow normal.

Clarification terbaru dari user:

- yang wajib untuk v1 adalah membaca MT5 yang sedang aktif saja,
- bukan membaca semua account sekaligus dalam satu waktu.

## Yang Akan Dibangun

### 1. Active Terminal Attach

Worker MT5 akan mencoba:

1. `mt5.initialize()` biasa untuk attach ke terminal aktif/default.
2. Jika gagal, scan jalur terminal umum dan candidate terminal yang sedang aktif.
3. Probe `account_info()` dan `terminal_info()` untuk memastikan sesi valid.

Ini meniru filosofi backup live engine, tetapi dibuat lebih general.

### 2. Account-Agnostic Metadata Read

Adapter tidak akan peduli account tersebut:

- IDR,
- USD,
- cent,
- broker A,
- broker B.

Semua dibaca sebagai metadata account dan diproses ke format internal yang seragam.

### 3. Broker-Agnostic Symbol Resolution

Backup lama memakai pendekatan suffix sederhana seperti:

- `EURUSD`
- `EURUSDm`
- `EURUSD.`
- `EURUSD_`
- `EURUSDi`

V2 akan mempertahankan ide itu, tetapi menambah mekanisme scan symbol list agar lebih tahan terhadap broker yang memakai prefix/suffix lain.

### 4. Multi-Account Reality

Keinginan user awal: engine bisa berkembang ke banyak account karena account banyak.

Clarification final untuk v1:

- engine cukup membaca account yang sedang aktif,
- tidak perlu membaca semua account sekaligus.

Fakta teknis:

- satu MT5 Python session tidak ideal untuk mengelola banyak terminal/account sekaligus,
- karena itu arsitektur yang benar adalah `fleet manager + worker per terminal/account`.

Jadi:

- user cukup menjalankan account yang aktif dan engine akan attach otomatis ke situ,
- jika nanti ingin banyak account sekaligus, implementasinya dilakukan lewat beberapa worker terpisah.

## Output Internal Standar

Setiap worker MT5 harus menghasilkan struktur standar seperti:

- `account_id`
- `server`
- `broker_name`
- `currency`
- `balance`
- `equity`
- `terminal_path`
- `connected`
- `resolved_symbols`
- `health_status`

## Keterhubungan Dengan AI Manager

AI manager nanti harus bisa:

1. melihat account mana yang aktif,
2. melihat broker/server mana yang terhubung,
3. melihat symbol mana yang berhasil di-resolve,
4. mendeteksi account yang error atau disconnect,
5. memutuskan apakah worker perlu restart,
6. melaporkan semua itu ke Telegram.

## Jawaban Desain

Ya, requirement user akan dipenuhi.

Tetapi implementasinya harus dengan pola:

- auto attach,
- auto detect,
- auto symbol resolve,
- single active session pada v1,
- dan fleet untuk multi-account pada fase lanjut.

Bukan satu session MT5 tunggal yang dipaksa membaca semua account sekaligus.