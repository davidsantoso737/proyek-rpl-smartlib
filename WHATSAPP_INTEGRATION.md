# Integrasi WhatsApp SmartLib

Dokumen ini menjelaskan cara kerja notifikasi WhatsApp SmartLib melalui Fonnte, batas keamanannya, dan cara menjalankannya.

## Ringkasan

Notifikasi eksternal dikirim melalui Fonnte HTTP API. Tidak ada bot polling, webhook, atau proses yang berjalan di dalam request pengguna. Aplikasi hanya mencatat kejadian ke outbox, lalu worker terpisah mengirimkannya.

Notifikasi internal di aplikasi tetap berjalan seperti biasa. WhatsApp adalah kanal tambahan, bukan pengganti notifikasi internal.

## Model data

- `WhatsAppConnection`: riwayat penautan nomor milik satu pengguna.
  - `pending`: menunggu persetujuan pustakawan.
  - `approved`: disetujui, menunggu verifikasi OTP.
  - `active`: nomor terverifikasi dan menerima notifikasi.
  - `rejected`: pengajuan ditolak.
  - `revoked`: pengajuan dibatalkan, tautan diputus, atau batas OTP tercapai.
- `active_phone`: hanya diisi saat status `active`, unik, sehingga satu nomor tidak dapat aktif pada dua akun.
- `WhatsAppOutbox`: antrean pengiriman dengan deduplikasi, percobaan ulang, dan status pengiriman.

## Prasyarat

1. Akun Fonnte dengan token aktif.
2. Nomor pengirim yang sudah terhubung di dashboard Fonnte.
3. Database MySQL yang sudah dimigrasikan.
4. Variabel environment berikut di `.env`:

```env
WHATSAPP_NOTIFICATIONS_ENABLED=True
FONNTE_API_TOKEN=token-dari-fonnte
FONNTE_API_URL=https://api.fonnte.com/send
FONNTE_COUNTRY_CODE=62
FONNTE_TIMEOUT_SECONDS=15
```

Jangan menulis token ke log, dokumentasi, atau pesan commit.

## Instalasi

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py whatsapp_check
```

`whatsapp_check` hanya memeriksa konfigurasi dan tidak mengirim pesan.

Untuk uji koneksi ke nomor operator:

```powershell
.\.venv\Scripts\python.exe manage.py whatsapp_send_test --phone 08xxxxxxxxxx
```

Command ini hanya mengirim ke nomor yang diberikan secara eksplisit.

## Alur penautan

1. Pengguna membuka halaman Profil dan mengajukan nomor WhatsApp.
2. Nomor dinormalisasi ke format `62...` dan disimpan sebagai pengajuan `pending`.
3. Pustakawan membuka **Verifikasi WhatsApp**, memeriksa identitas pengguna, lalu menyetujui atau menolak.
4. Persetujuan mengubah status menjadi `approved` dan mengirim kode OTP 6 digit melalui Fonnte.
5. Pengguna memasukkan OTP pada halaman Profil. Kode berlaku 10 menit, maksimal 5 percobaan, dan dapat dikirim ulang setelah 60 detik.
6. Setelah OTP benar, status menjadi `active`. Nomor lama yang masih aktif diputus pada tahap ini, bukan saat pengajuan baru dibuat.
7. Pengguna dapat meminta kode baru, membatalkan pengajuan, atau memutus tautan dari halaman Profil.

Nomor yang sedang `pending` atau `approved` pada akun lain tidak dapat diajukan. Nomor aktif juga tidak dapat dipakai akun kedua.

## Preferensi

Preferensi disimpan pada baris koneksi yang aktif:

- `notifications_enabled`: saklar utama.
- `notify_loans`: persetujuan, penolakan, dan peminjaman aktif.
- `notify_returns`: konfirmasi pengembalian.
- `notify_fines`: informasi denda.
- `notify_due_soon`: pengingat H-3 dan H-1.
- `notify_overdue`: informasi keterlambatan.

Notifikasi sistem `WHATSAPP_LINK_ACTIVE` tetap dikirim sebagai konfirmasi aktivasi selama saklar utama dan pengiriman global menyala.

## Outbox

Pembuatan outbox terjadi di dalam transaksi bisnis dan tidak memanggil Fonnte. Worker mengirim pesan secara terpisah.

```powershell
.\.venv\Scripts\python.exe manage.py whatsapp_outbox
.\.venv\Scripts\python.exe manage.py whatsapp_outbox --limit 100
.\.venv\Scripts\python.exe manage.py whatsapp_outbox --review
.\.venv\Scripts\python.exe manage.py whatsapp_outbox --retry-failed
.\.venv\Scripts\python.exe manage.py whatsapp_outbox --retry-id 123
```

Status outbox:

- `PENDING`: menunggu dikirim.
- `SENDING`: sedang diklaim worker.
- `RETRY`: menunggu percobaan ulang.
- `SENT`: terkirim.
- `FAILED`: gagal permanen.
- `UNCERTAIN`: timeout atau respons tidak valid, status pengiriman tidak pasti.
- `SKIPPED`: penerima tidak lagi memiliki nomor aktif atau mematikan preferensi.

Timeout dan respons tidak valid tidak diulang otomatis karena pesan mungkin sudah terkirim. Tinjau baris `UNCERTAIN` sebelum memakai `--retry-id`.

## Scheduler Windows

Task yang dipakai:

- `SmartLib WhatsApp Outbox`: setiap menit.
- `SmartLib WhatsApp Reminders`: harian pukul 08:00.

Task harus dibuat manual di Task Scheduler dengan action yang menjalankan:

```text
wscript.exe //B //NoLogo "<project>\scripts\run_hidden.vbs" "<project>\scripts\run_whatsapp_outbox.bat"
wscript.exe //B //NoLogo "<project>\scripts\run_hidden.vbs" "<project>\scripts\run_whatsapp_reminders.bat"
```

Setelah task dibuat, selaraskan action dan setting:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ensure_whatsapp_tasks.ps1 -DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ensure_whatsapp_tasks.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ensure_whatsapp_tasks.ps1 -Enable
```

Skrip tidak membuat task baru dan tidak mengubah principal. Untuk berjalan tanpa sesi login, ubah principal secara manual ke "Run whether user is logged on or not" dengan password tersimpan.

Log worker:

- `logs\whatsapp-outbox.log`
- `logs\whatsapp-reminders.log`
- `logs\task-scheduler-bootstrap.log`

Periksa status task:

```powershell
Get-ScheduledTask -TaskName "SmartLib WhatsApp Outbox","SmartLib WhatsApp Reminders" |
    Format-Table TaskName,State
Get-ScheduledTaskInfo -TaskName "SmartLib WhatsApp Outbox"
Get-Content .\logs\whatsapp-outbox.log -Tail 50
```

## Troubleshooting

- `FONNTE_API_TOKEN belum diatur`: isi token di `.env`, lalu jalankan ulang command.
- `FONNTE_API_URL harus menggunakan HTTPS`: gunakan URL HTTPS yang valid.
- `401` atau `403`: token ditolak atau tidak memiliki akses. Periksa dashboard Fonnte.
- `429`: pengiriman dibatasi. Outbox dijadwalkan ulang secara otomatis.
- Timeout: outbox menjadi `UNCERTAIN`. Jangan langsung mengirim ulang tanpa memeriksa status di Fonnte.
- Pesan tidak masuk meskipun `SENT`: periksa nomor tujuan, status perangkat Fonnte, dan batas pengiriman akun.
- Nomor dianggap sudah dipakai: periksa baris `active`, `pending`, atau `approved` pada nomor tersebut.

Jika pengiriman sedang dinonaktifkan, command worker akan berhenti dengan pesan yang jelas. Pengajuan penautan tetap dapat dibuat, tetapi pesan otomatis belum dikirim.

## Pengujian

```powershell
.\.venv\Scripts\python.exe manage.py test apps.whatsapp
```

Pengujian tidak boleh menghubungi Fonnte. Gunakan client palsu atau mock untuk semua pengiriman.

## Catatan migrasi dari Telegram

Integrasi Telegram sudah dihapus dari aplikasi. Tabel lama di database lokal sudah dibersihkan melalui migrasi sebelum aplikasi Telegram dihapus. Jika Anda memakai database lama dari mesin lain, terapkan migrasi pembersihan dari revisi kode sebelumnya, lalu hapus task scheduler Telegram yang lama secara manual:

```powershell
Unregister-ScheduledTask -TaskName "SmartLib Telegram Outbox" -Confirm:$false
Unregister-ScheduledTask -TaskName "SmartLib Telegram Reminders" -Confirm:$false
```
