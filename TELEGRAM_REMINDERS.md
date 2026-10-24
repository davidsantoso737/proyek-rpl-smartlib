# Pengingat Jatuh Tempo Telegram

Command `kirim_pengingat_telegram` memeriksa pinjaman fisik `ACTIVE`
yang masih memiliki eksemplar belum dikembalikan, lalu mencatat
pengingat ke outbox Telegram.

Jadwal pengingat:

- H-3 sebelum tanggal jatuh tempo.
- H-1 sebelum tanggal jatuh tempo.
- Terlambat, satu kali per transaksi pinjam selama masih ada eksemplar
  yang belum dikembalikan.

Tanggal dihitung memakai `TIME_ZONE` Django. Buku dalam satu transaksi
pinjam digabung menjadi satu ringkasan. Deduplikasi memakai penerima,
jenis notifikasi, transaksi pinjam, dan periode pengingat.

## Menjalankan manual

Dari akar proyek:

```powershell
.\.venv\Scripts\python.exe manage.py kirim_pengingat_telegram
```

Hanya membuat antrean, tanpa menghubungi Telegram:

```powershell
.\.venv\Scripts\python.exe manage.py kirim_pengingat_telegram --no-send
```

Meninjau status pengiriman dan kegagalan:

```powershell
.\.venv\Scripts\python.exe manage.py telegram_outbox --review
```

Mengembalikan pengiriman gagal atau tidak pasti ke antrean:

```powershell
.\.venv\Scripts\python.exe manage.py telegram_outbox --retry-failed
```

Konfigurasi minimum di `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=token-dari-BotFather
TELEGRAM_BOT_USERNAME=nama_bot_tanpa_at
TELEGRAM_NOTIFICATIONS_ENABLED=true
```

## Windows Task Scheduler

Proyek belum memiliki scheduler internal. Command lama
`kirim_pengingat_jatuh_tempo` hanya membuat notifikasi dalam aplikasi
H-2 dan terlambat. Pengingat Telegram memakai outbox yang sama dengan
notifikasi transaksi, lalu dijalankan berkala oleh Windows Task
Scheduler.

Task yang dipakai:

- `SmartLib Telegram Outbox`: setiap menit.
- `SmartLib Telegram Reminders`: harian pukul 08:00.

Agar tidak memunculkan jendela CMD, action task memakai
`scripts\run_hidden.vbs` melalui `wscript.exe`. Wrapper menjalankan
batch tersembunyi, menunggu sampai selesai, dan meneruskan exit code.
Batch tetap menulis stdout/stderr ke log. Jangan menjalankan batch
langsung dari Task Scheduler, memakai `start /b`, atau mengganti
`python.exe` dengan `pythonw.exe`.

Siapkan atau periksa task secara idempoten:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ensure_telegram_tasks.ps1 -DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ensure_telegram_tasks.ps1
```

Skrip tidak membuat task baru, tidak memulai task, dan tidak mengaktifkan
task kecuali diberi `-Enable`. Backup XML task disimpan di
`backups\task-scheduler\<timestamp>\`.

Aktivasi setelah pengujian disetujui:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ensure_telegram_tasks.ps1 -Enable
```

Mengaktifkan task dapat memicu eksekusi segera karena
`StartWhenAvailable`. Pastikan antrean dan penerima sudah diperiksa
sebelum aktivasi.

Pengaturan task yang dipakai:

- `MultipleInstances = IgnoreNew` agar worker tidak bertumpuk.
- `ExecutionTimeLimit = PT15M`.
- `StartWhenAvailable = True`.
- `AllowHardTerminate = True`.

Jika task harus berjalan tanpa sesi login, ubah manual principal task
menjadi **Run whether user is logged on or not** dengan password yang
disimpan. Jangan memilih **Do not store password** jika worker
memerlukan akses jaringan ke Telegram. Skrip tidak meminta atau
menyimpan password Windows.

Periksa status dan hasil:

```powershell
Get-ScheduledTask -TaskName "SmartLib Telegram Reminders" |
    Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskName "SmartLib Telegram Reminders" |
    Select-Object LastRunTime,LastTaskResult,NextRunTime
Get-Content .\logs\telegram-reminders.log -Tail 50
Get-Content .\logs\task-scheduler-bootstrap.log -Tail 50
```

Log worker dirotasi satu generasi ke `.1` jika ukurannya melewati 5 MB.

Setelah komputer restart:

1. Pastikan Laragon/MySQL aktif.
2. Jalankan `ensure_telegram_tasks.ps1` tanpa `-Enable` untuk memeriksa
   task dan action.
3. Jika task masih nonaktif dan pengujian sudah disetujui, jalankan
   dengan `-Enable`.
4. Periksa `Get-ScheduledTaskInfo` dan log worker.

Untuk menghentikan notifikasi otomatis sementara, nonaktifkan kedua task
melalui Task Scheduler atau:

```powershell
Disable-ScheduledTask -TaskName "SmartLib Telegram Outbox"
Disable-ScheduledTask -TaskName "SmartLib Telegram Reminders"
```

Mematikan task tidak menghapus antrean dan tidak memutus tautan akun.
Untuk menguji koneksi bot tanpa mengirim pesan, gunakan
`manage.py telegram_check`. Untuk meninjau antrean tanpa mengubah status,
gunakan `manage.py telegram_outbox --review`.

## Catatan operasional

- Jalankan minimal sekali setiap hari agar pengingat H-3 dan H-1 tidak
  terlewat. Jika task tertunda sampai tanggal berikutnya, tahap H-3 atau
  H-1 untuk tanggal itu tidak dikirim ulang.
- Pengingat terlambat dikirim sekali per transaksi. Setelah kegagalan
  permanen, gunakan `telegram_outbox --retry-failed` setelah penyebabnya
  diperbaiki.
- `TIME_ZONE` proyek adalah `Asia/Jakarta`. Batas H-3, H-1, dan terlambat
  mengikuti tanggal kalender WIB, bukan UTC.
- Timeout Telegram ditandai `UNCERTAIN` karena status pengiriman tidak
  pasti. Tinjau sebelum retry manual untuk menghindari pesan ganda.
- Satu ringkasan dibatasi 10 judul. Buku selebihnya diringkas sebagai
  jumlah "buku lainnya".
- Jangan menjalankan dua salinan command bersamaan. Outbox memiliki
  klaim per baris, tetapi penjadwalan ganda tetap tidak diperlukan.
