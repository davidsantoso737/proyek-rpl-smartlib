# Pengingat Jatuh Tempo WhatsApp

Command `kirim_pengingat_whatsapp` memeriksa peminjaman fisik berstatus `ACTIVE`, lalu mencatat pengingat ke outbox WhatsApp.

## Periode pengingat

- **H-3**: buku jatuh tempo dalam 3 hari.
- **H-1**: buku jatuh tempo besok.
- **OVERDUE**: buku sudah melewati tanggal jatuh tempo.

Beberapa buku dalam satu transaksi digabung menjadi satu ringkasan per periode. Buku yang sudah dikembalikan tidak diproses. Tanggal dihitung memakai zona waktu Django, bukan UTC mentah.

## Menjalankan manual

```powershell
.\.venv\Scripts\python.exe manage.py kirim_pengingat_whatsapp
.\.venv\Scripts\python.exe manage.py kirim_pengingat_whatsapp --limit 100
```

Hanya membuat antrean tanpa menghubungi Fonnte:

```powershell
.\.venv\Scripts\python.exe manage.py kirim_pengingat_whatsapp --no-send
```

`--limit` membatasi jumlah outbox yang dikirim dalam satu proses, bukan jumlah pengingat yang dicatat.

## Melihat dan mengulang outbox

```powershell
.\.venv\Scripts\python.exe manage.py whatsapp_outbox --review
.\.venv\Scripts\python.exe manage.py whatsapp_outbox --retry-failed
.\.venv\Scripts\python.exe manage.py whatsapp_outbox --retry-id 123
```

Baca `WHATSAPP_INTEGRATION.md` untuk arti setiap status outbox. Jangan mengulang baris `UNCERTAIN` tanpa memeriksa status di Fonnte.

## Duplikasi

Setiap pengingat memakai `event_key` yang memuat jenis pengingat dan tanggal jatuh tempo. Menjalankan command berkali-kali pada hari yang sama tidak membuat baris baru. Jika tanggal jatuh tempo berubah, pengingat baru dapat dibuat karena kejadiannya berbeda.

## Scheduler

Task `SmartLib WhatsApp Reminders` dijalankan harian pukul 08:00. Batch yang dipanggil:

```text
scripts\run_whatsapp_reminders.bat
```

Log:

```text
logs\whatsapp-reminders.log
```

Untuk membuat atau menyelaraskan task, lihat bagian Scheduler Windows di `WHATSAPP_INTEGRATION.md`.

## Gangguan pengiriman

- `500` atau gangguan jaringan: outbox dijadwalkan ulang dengan backoff.
- `429`: pengiriman dibatasi, outbox dijadwalkan ulang.
- `401` atau `403`: token atau akses ditolak, outbox berhenti sebagai `FAILED`.
- Timeout: outbox menjadi `UNCERTAIN` karena pesan mungkin sudah terkirim.
- Penerima tanpa nomor aktif atau preferensi mati: outbox menjadi `SKIPPED`.

Kegagalan satu penerima tidak menghentikan penerima lain dalam antrean.

## Pemeriksaan konfigurasi

```powershell
.\.venv\Scripts\python.exe manage.py whatsapp_check
```

Command ini tidak mengirim pesan. Untuk uji nyata ke nomor operator:

```powershell
.\.venv\Scripts\python.exe manage.py whatsapp_send_test --phone 08xxxxxxxxxx
```

Jangan menguji dengan nomor pengguna tanpa persetujuan.
