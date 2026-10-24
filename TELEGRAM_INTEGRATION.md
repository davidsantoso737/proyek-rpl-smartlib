# Integrasi Telegram SmartLib

Dokumen ini menjelaskan cara kerja integrasi Telegram, batas keamanannya,
perintah operasional, dan verifikasi yang tersedia.

## Ringkasan

Integrasi memakai Telegram Bot API melalui long polling. Tidak ada webhook
publik, Celery, Redis, atau scheduler internal.

Komponen utama:

- `TelegramAccount`: satu tautan Telegram per pengguna SmartLib.
- `TelegramLinkToken`: tautan sekali pakai, disimpan sebagai hash.
- `TelegramBotState`: lease agar hanya satu proses polling aktif.
- `TelegramOutbox`: antrean pengiriman notifikasi dan pengingat.

## Menjalankan SmartLib

1. Jalankan Laragon dan pastikan MySQL menerima koneksi.
2. Pastikan `.env` berisi variabel `DB_*` dan variabel Telegram yang
   diperlukan. Jangan menampilkan nilainya di terminal atau log.
3. Gunakan interpreter `.venv\Scripts\python.exe`.
4. Jalankan migrasi hanya jika ada migrasi baru.
5. Jalankan `manage.py runserver` untuk aplikasi web.
6. Untuk penautan akun, jalankan poller `telegram_bot`. Worker outbox
   dijalankan terpisah oleh Windows Task Scheduler, bukan dari proses web.

Worker Telegram tidak boleh dipanggil dari view, middleware, atau
`AppConfig.ready()`. Setiap request HTTP tidak boleh membuat worker baru.

## Alur menautkan akun

1. Pengguna membuka **Profil** dan menekan **Buat tautan Telegram**.
2. Server membuat token 10 menit. Kode mentah hanya disimpan di sesi
   pengguna; database hanya menyimpan hash-nya.
3. Tombol **Buka bot Telegram** diarahkan ke endpoint internal yang
   memerlukan login, lalu meneruskan pengguna ke deep link bot.
4. Pengguna menekan **Start** di chat pribadi bot.
5. Bot mencatat klaim token. Kode tidak dapat dipakai akun Telegram lain.
6. Pengguna kembali ke **Profil**, memuat ulang status, lalu menekan
   **Konfirmasi tautan**.
7. Setelah konfirmasi, `TelegramAccount` dibuat dan notifikasi berikutnya
   dapat dikirim ke chat pribadi tersebut.

Kode tautan mentah tidak ditampilkan sebagai teks di halaman. `Bot Token`,
`Chat ID`, dan hash token juga tidak pernah ditampilkan ke pengguna.

## Preferensi

Preferensi disimpan pada `TelegramAccount`:

- aktif/nonaktif seluruh notifikasi otomatis;
- peminjaman;
- pengembalian;
- denda;
- pengingat jatuh tempo;
- informasi keterlambatan.

Mematikan preferensi tidak memutus tautan. Memutus tautan menghapus
`TelegramAccount` dan menghentikan notifikasi ke chat tersebut.

## Notifikasi transaksi

Pembuatan notifikasi in-app dan enqueue Telegram terjadi di dalam transaksi
bisnis yang sama. Enqueue tidak menghubungi Telegram.

Event yang terhubung:

- pengajuan disetujui;
- pengajuan ditolak;
- peminjaman aktif setelah serah terima;
- pengembalian berhasil;
- e-book disetujui;
- e-book ditolak.

Tidak ada event bisnis baru dan tidak ada penggantian notifikasi in-app.
Deduplikasi memakai SHA-256 dari pengguna, jenis notifikasi, referensi, dan
event key.

Pengiriman dilakukan terpisah:

```powershell
.\.venv\Scripts\python.exe manage.py telegram_outbox
.\.venv\Scripts\python.exe manage.py telegram_outbox --limit 100
.\.venv\Scripts\python.exe manage.py telegram_outbox --review
.\.venv\Scripts\python.exe manage.py telegram_outbox --retry-failed
.\.venv\Scripts\python.exe manage.py telegram_outbox --retry-id 123
```

Status outbox:

- `PENDING`: menunggu jadwal kirim.
- `SENDING`: sedang diklaim satu proses.
- `RETRY`: menunggu percobaan berikutnya.
- `SENT`: berhasil dikirim.
- `FAILED`: gagal permanen.
- `UNCERTAIN`: timeout atau respons tidak valid; status kirim tidak pasti.
- `SKIPPED`: penerima sudah tidak mengizinkan notifikasi tersebut.

Maksimum lima percobaan. `429` mengikuti `retry_after`; `5xx`, gangguan
jaringan, dan kegagalan tak terduga memakai backoff. `400`, `401`, dan `403`
dianggap permanen. Timeout tidak di-retry otomatis karena pesan mungkin
sudah terkirim.

## Pengingat jatuh tempo

Pengingat H-3, H-1, dan terlambat dijalankan oleh command terpisah:

```powershell
.\.venv\Scripts\python.exe manage.py kirim_pengingat_telegram
.\.venv\Scripts\python.exe manage.py kirim_pengingat_telegram --no-send
```

Batas tanggal memakai `TIME_ZONE` proyek. Detail penjadwalan Windows Task
Scheduler ada di `TELEGRAM_REMINDERS.md`.

Command lama `kirim_pengingat_jatuh_tempo` tetap menjadi sumber notifikasi
in-app H-2 dan terlambat. Kedua mekanisme tidak saling menggantikan.

## Worker, Task Scheduler, dan log

Worker outbox dan pengingat dijalankan oleh Windows Task Scheduler. Agar
tidak memunculkan jendela CMD berulang, action task memakai wrapper
`scripts\run_hidden.vbs` melalui `wscript.exe`:

- `SmartLib Telegram Outbox`: setiap menit.
- `SmartLib Telegram Reminders`: harian pukul 08:00.

Wrapper menjalankan batch dengan window style hidden, menunggu sampai
proses selesai, dan meneruskan exit code. Batch tetap menulis stdout dan
stderr ke log. Jangan mengganti `python.exe` dengan `pythonw.exe` karena
output dan traceback dapat hilang.

Siapkan atau periksa task secara idempoten:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ensure_telegram_tasks.ps1 -DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ensure_telegram_tasks.ps1
```

Skrip tidak membuat task baru, tidak memulai task, dan tidak mengaktifkan
task kecuali diberi `-Enable`. Backup XML task disimpan di
`backups\task-scheduler\<timestamp>\`. Mengaktifkan task dapat memicu
eksekusi segera karena `StartWhenAvailable`; periksa antrean dan penerima
terlebih dahulu.

Log operasional:

- `logs\telegram-outbox.log`
- `logs\telegram-reminders.log`
- `logs\task-scheduler-bootstrap.log`

Log worker dirotasi satu generasi ke `.1` jika ukurannya melewati 5 MB.

Periksa status dan hasil:

```powershell
Get-ScheduledTask -TaskName "SmartLib Telegram Outbox","SmartLib Telegram Reminders" |
    Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskName "SmartLib Telegram Outbox" |
    Select-Object LastRunTime,LastTaskResult,NextRunTime
Get-Content .\logs\telegram-outbox.log -Tail 50
Get-Content .\logs\task-scheduler-bootstrap.log -Tail 50
```

Task memakai `IgnoreNew`, batas eksekusi 15 menit, dan
`StartWhenAvailable`. MySQL harus aktif sebelum worker berjalan. Jika
MySQL tidak tersedia, worker keluar dengan exit code non-zero, antrean
tidak diubah, dan jadwal berikutnya mencoba lagi. Status `UNCERTAIN`
tidak dikirim ulang secara otomatis.

Jika task harus berjalan tanpa sesi login, ubah manual principal task
menjadi **Run whether user is logged on or not** dengan password yang
disimpan. Jangan memilih **Do not store password** jika worker memerlukan
akses jaringan ke Telegram. Skrip bootstrap tidak meminta atau menyimpan
password Windows.

## Konfigurasi

Isi melalui environment atau `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=token-dari-BotFather
TELEGRAM_BOT_USERNAME=nama_bot_tanpa_at
TELEGRAM_NOTIFICATIONS_ENABLED=true
```

Jangan commit nilai token. Jangan menampilkan token, Chat ID, atau kode
tautan aktif di log, pesan error, atau halaman pengguna.

Diagnostik koneksi tanpa mengirim pesan:

```powershell
.\.venv\Scripts\python.exe manage.py telegram_check
```

Menjalankan poller:

```powershell
.\.venv\Scripts\python.exe manage.py telegram_bot
```

Hanya satu poller yang boleh aktif. Hentikan proses lama sebelum memulai
poller baru.

## Zona waktu

Proyek memakai `TIME_ZONE = "Asia/Jakarta"` dan `USE_TZ = True`. Timestamp
tetap disimpan sebagai UTC oleh Django/MySQL, lalu ditampilkan sebagai WIB.

Perubahan zona waktu tidak menulis ulang data lama:

- instant timestamp lama tidak berubah;
- tanggal dan jam yang ditampilkan bergeser mengikuti WIB;
- `late_day` dan `fine_amount` yang sudah tercatat adalah snapshot dan tidak
  dihitung ulang otomatis;
- teks notifikasi/outbox lama yang sudah dibuat tidak diubah.

Logika tanggal bisnis harus memakai `timezone.localdate()` atau
`timezone.localtime(...)`, bukan `.date()` pada datetime UTC. Filter laporan
memakai rentang instant yang dihitung dari WIB, sehingga tidak bergantung
pada tabel zona waktu MySQL (`CONVERT_TZ`).

## Verifikasi

Perintah pemeriksaan:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
```

Test integrasi mencakup linking, keamanan token, preferensi, bot polling,
outbox, retry, pengingat, batas hari WIB, dan UI profil.

Pengujian ke Telegram nyata hanya dilakukan jika `TELEGRAM_BOT_TOKEN`,
username bot, dan penerima uji sudah dikonfigurasi. Tanpa konfigurasi
tersebut, verifikasi berhenti pada test otomatis dan `--no-send`.
