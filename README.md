# SmartLib - Sistem Informasi Perpustakaan Berbasis AI

SmartLib merupakan aplikasi web manajemen perpustakaan sekolah yang dirancang untuk mendigitalisasi dan mengintegrasikan proses operasional perpustakaan, mulai dari pengelolaan koleksi buku, layanan peminjaman dan pengembalian, hingga pengelolaan anggota dan pelaporan.

Aplikasi ini dilengkapi dengan integrasi Artificial Intelligence (AI) sebagai asisten cerdas untuk membantu pengguna memperoleh informasi dan rekomendasi berdasarkan konteks serta data perpustakaan.

---

## Identitas Mahasiswa

| Keterangan | Informasi |
|---|---|
| Nama | David Santoso |
| NIM | 41257097 |
| Kelompok | Kelompok 2 |
| Tema Topik Kelompok | AI-Based Digital Learning Resource Ecosystem|
| Nama Aplikasi | SmartLib |
| Mata Kuliah | Rekayasa Perangkat Lunak |
| Program Studi | S1 Teknik Informatika |
| Institusi | STMIK IKMI Cirebon |

---

## Deskripsi Aplikasi

SmartLib dikembangkan sebagai solusi digital untuk mengatasi permasalahan pengelolaan perpustakaan sekolah yang masih dilakukan secara konvensional, seperti pencatatan transaksi peminjaman, pengelolaan inventaris buku, pemantauan ketersediaan koleksi, serta penyusunan laporan perpustakaan.

Aplikasi ini mengintegrasikan seluruh proses tersebut ke dalam satu sistem berbasis web dengan tiga aktor utama:

- **Siswa:** Mengakses katalog koleksi, membaca bahan digital, melakukan peminjaman, melihat riwayat transaksi, dan memanfaatkan asisten AI.
- **Guru:** Mengakses layanan perpustakaan sesuai hak akses, memanfaatkan koleksi dan bahan ajar, serta fitur pendukung pembelajaran.
- **Pustakawan:** Mengelola data koleksi, inventaris, anggota, transaksi peminjaman dan pengembalian, denda, serta laporan operasional perpustakaan.

SmartLib dirancang menggunakan pendekatan modular agar setiap bagian aplikasi memiliki tanggung jawab yang terstruktur dan mudah dikembangkan.

## Fitur Utama

### 1. Manajemen Pengguna dan Hak Akses
- Autentikasi dan pengelolaan akun pengguna.
- Pengaturan hak akses berdasarkan peran siswa, guru, dan pustakawan.
- Dashboard sesuai peran pengguna.

### 2. Manajemen Katalog dan Koleksi
- Pengelolaan informasi buku dan koleksi perpustakaan.
- Pencarian dan penyaringan koleksi.
- Pengelompokan kategori koleksi.
- Pengelolaan koleksi digital dan bahan ajar.

### 3. Sirkulasi Peminjaman dan Pengembalian
- Pengajuan dan pengelolaan peminjaman.
- Proses persetujuan peminjaman oleh pustakawan.
- Pencatatan pengembalian buku.
- Pengelolaan status transaksi dan riwayat peminjaman.
- Perhitungan serta pengelolaan denda keterlambatan.

### 4. Manajemen Inventaris
- Pemantauan jumlah eksemplar koleksi.
- Pengelolaan kondisi buku.
- Pencatatan buku tersedia, dipinjam, rusak, dan hilang.

### 5. Layanan Koleksi Digital
- Akses bahan bacaan digital sesuai hak akses.
- Pembatasan akses file digital berdasarkan status peminjaman.
- Dukungan pembacaan dokumen digital.

### 6. Notifikasi WhatsApp
- Integrasi notifikasi melalui WhatsApp menggunakan Fonnte API.
- Pengiriman informasi terkait aktivitas perpustakaan.
- Pemrosesan notifikasi melalui mekanisme antrean.
- Pencatatan status pengiriman dan penanganan kegagalan.

### 7. Laporan Perpustakaan
- Penyajian informasi operasional perpustakaan.
- Rekapitulasi aktivitas peminjaman dan pengembalian.
- Informasi koleksi dan inventaris.
- Penyediaan laporan untuk kebutuhan administrasi.

## Fitur Artificial Intelligence (AI)

SmartLib mengintegrasikan AI sebagai bagian dari layanan utama aplikasi, bukan sekadar fitur tambahan.

### 1. AI Assistant

Asisten AI interaktif yang dapat digunakan untuk membantu pengguna memperoleh informasi dan panduan terkait layanan perpustakaan.

Kemampuan utamanya meliputi:
- Menjawab pertanyaan umum dan percakapan kontekstual.
- Memberikan panduan penggunaan aplikasi.
- Membantu memahami prosedur peminjaman dan pengembalian.
- Menjawab pertanyaan berdasarkan informasi aktual dalam database perpustakaan.
- Memberikan informasi mengenai koleksi, ketersediaan buku, transaksi, dan denda sesuai hak akses pengguna.

### 2. AI Recommendation

Fitur rekomendasi berbasis AI yang membantu pengguna menemukan koleksi bacaan yang relevan berdasarkan informasi dan konteks permintaan pengguna.

### 3. AI Provider Configuration

Aplikasi menyediakan pengaturan provider AI untuk mengelola konfigurasi layanan AI, termasuk pemilihan provider, model, dan API Key.

### 4. Node.js AI Adapter

SmartLib menggunakan layanan Node.js sebagai adapter opsional untuk menangani komunikasi dengan penyedia layanan AI. Django tetap menjadi backend utama dan sumber kebenaran data aplikasi.

---

## Teknologi yang Digunakan

| Komponen | Teknologi |
|---|---|
| Backend | Python, Django 6.1.1 |
| Database | MySQL 8.4.3 |
| ORM | Django ORM |
| Database Driver | PyMySQL |
| Frontend | Django Templates, HTML, JavaScript |
| CSS Framework | Tailwind CSS 4 |
| AI Adapter | Node.js |
| AI Integration | API Provider kompatibel OpenAI |
| WhatsApp Integration | Fonnte API |
| Automated Testing | Django Test Framework, Coverage.py |
| Version Control | Git dan GitHub |
| Development Environment | Laragon, Visual Studio Code |

---

## Persyaratan Sistem

Sebelum menjalankan aplikasi, pastikan perangkat telah memiliki:

- Python 3.14 atau versi yang kompatibel.
- Node.js versi 18 atau lebih baru.
- MySQL 8.4 atau versi yang kompatibel.
- Git.
- Web browser modern.
- npm (termasuk dalam instalasi Node.js).

---

## Instalasi dan Konfigurasi

### 1. Clone Repository

```bash
git clone https://github.com/davidsantoso737/proyek-rpl-smartlib.git
cd proyek-rpl-smartlib
```

### 2. Buat Virtual Environment Python

```bash
python -m venv .venv
```

Aktifkan virtual environment:

**Windows PowerShell:**

```powershell
.\.venv\Scripts\Activate.ps1
```

### 3. Instal Dependensi Python

```bash
pip install -r requirements.txt
```

### 4. Konfigurasi Environment

Buat file `.env` pada direktori utama proyek.

Isi konfigurasi berikut dan sesuaikan dengan lingkungan lokal:

```env
SECRET_KEY=your-django-secret-key
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost

DB_NAME=digital_library
DB_USER=root
DB_PASSWORD=
DB_HOST=127.0.0.1
DB_PORT=3306

AI_PROVIDER_BASE_URL=
AI_API_KEY=
AI_MODEL=
AI_TIMEOUT_SECONDS=

FONNTE_API_TOKEN=
FONNTE_API_URL=https://api.fonnte.com/send
FONNTE_COUNTRY_CODE=62
WHATSAPP_NOTIFICATIONS_ENABLED=False
```

**Catatan:**
- Jangan mengunggah file `.env` atau API Key asli ke repository publik.
- Sesuaikan konfigurasi database dengan lingkungan MySQL yang digunakan.
- Konfigurasi AI dan WhatsApp bersifat opsional, tetapi diperlukan apabila ingin mengaktifkan integrasi layanan eksternal.
- Provider dan model AI juga dapat dikelola melalui pengaturan aplikasi yang tersedia.

### 5. Buat Database MySQL

Buat database dengan nama `digital_library` melalui MySQL atau phpMyAdmin.

```sql
CREATE DATABASE digital_library;
```

Pastikan konfigurasi database pada file `.env` sesuai dengan database yang telah dibuat.

### 6. Jalankan Migrasi Database

```bash
python manage.py migrate
```

### 7. Buat Akun Administrator

```bash
python manage.py createsuperuser
```

Ikuti instruksi terminal untuk membuat akun administrator.

### 8. Instal Dependensi Frontend

Pada direktori utama proyek:

```bash
npm ci
```

Kemudian jalankan proses build Tailwind CSS:

```bash
npm run build
```

Untuk mode pengembangan dengan pemantauan perubahan CSS:

```bash
npm run dev
```

### 9. Konfigurasi Node.js AI Adapter (Opsional)

Masuk ke direktori `ai-node`:

```bash
cd ai-node
npm ci
```

Jalankan layanan AI adapter:

```bash
npm start
```

Kembali ke direktori utama proyek untuk menjalankan Django.

### 10. Jalankan Aplikasi

```bash
python manage.py runserver
```

Buka browser dan akses:

```text
http://127.0.0.1:8000/
```

---

## Pengujian Otomatis

SmartLib menggunakan Django Test Framework untuk menguji fungsionalitas aplikasi secara otomatis.

Jalankan pengujian menggunakan perintah:

```bash
python manage.py test --noinput --verbosity 2
```

Untuk pengujian menggunakan konfigurasi khusus testing:

```bash
python manage.py test --settings=config.settings_test --noinput --verbosity 2
```

Pengujian mencakup berbagai bagian aplikasi, termasuk autentikasi, katalog, sirkulasi, inventaris, notifikasi, AI, dan laporan.

Pengujian juga dirancang agar layanan eksternal seperti API AI dan WhatsApp tidak mengganggu proses pengujian otomatis.

---

## Video Presentasi Proyek

Berikut merupakan tautan video presentasi sebagai bagian dari dokumentasi dan persyaratan pengumpulan proyek Rekayasa Perangkat Lunak.

| Tahapan | Tautan Video |
|---|---|
| Video Final - Live Demo Aplikasi | [Tonton Video Final SmartLib](https://youtu.be/LTpiJyH6YnM?si=ps5ljMOIitf6wywg) |

---

## Repository

Source code aplikasi SmartLib dapat diakses melalui repository GitHub berikut:

**GitHub Repository:**  
https://github.com/davidsantoso737/proyek-rpl-smartlib

---

## Penutup

SmartLib merupakan implementasi proyek akhir mata kuliah Rekayasa Perangkat Lunak yang menggabungkan pengembangan aplikasi web, pengelolaan basis data, integrasi Artificial Intelligence, pengujian otomatis, serta praktik pengembangan perangkat lunak yang terstruktur.

Pengembangan aplikasi ini diharapkan dapat memberikan solusi digital yang membantu meningkatkan efisiensi dan kualitas pengelolaan perpustakaan sekolah.

---

**Developed by David Santoso**  
**Rekayasa Perangkat Lunak - Semester 3**  
**S1 Teknik Informatika | STMIK IKMI Cirebon**