# Panduan Provisioning Akun Siswa & Guru (SmartLib SMA)

Dokumen operasional untuk pustakawan, developer, dan operator.
Mencakup pembuatan akun manual, import Excel, aturan identifier,
password awal, first-login, duplikat, error import, dan
activate/deactivate. Akun PUSTAKAWAN tidak dibahas di sini karena
perilakunya tidak berubah.

## 1. Cara membuat akun siswa manual

1. Login sebagai pustakawan.
2. Buka **Kelola Pengguna**, klik **+ Tambah Siswa**.
3. Isi NIS, nama lengkap, dan data profil (email, telepon, kelas).
4. Klik **Buat akun**. Tidak ada isian username/password: keduanya
   otomatis sama dengan NIS.
5. Sampaikan NIS kepada siswa sebagai username sekaligus password
   awal. Siswa wajib mengganti password saat login pertama.

Catatan halaman Profil milik sendiri: kolom Kelas dan Jurusan
hanya tampil untuk role Siswa. Role Guru dan Pustakawan hanya
dapat mengubah nama lengkap, email, dan telepon.

## 2. Cara membuat akun guru manual

1. Login sebagai pustakawan.
2. Buka **Kelola Pengguna**, klik **+ Tambah Guru**.
3. Isi NIP, nama lengkap, dan data profil (email, telepon, jurusan).
4. Klik **Buat akun**. Username dan password awal otomatis sama
   dengan NIP.
5. Sampaikan NIP kepada guru sebagai username sekaligus password
   awal. Guru wajib mengganti password saat login pertama.

## 3. Cara menyiapkan Excel siswa

1. Di halaman **Kelola Pengguna** klik **Import Siswa**, lalu unduh
   template bila belum ada.
2. Format hanya `.xlsx` (maksimal 5 MB). Satu sheet aktif dibaca;
   baris pertama adalah header.
3. Header yang dikenali: `NIS` (wajib), `Nama Lengkap`, `Email`,
   `No Telepon`, `Kelas`, `Jurusan`. Varian umum seperti `HP`,
   `class`, atau `department` juga terbaca; kolom lain diabaikan.
4. Isi satu baris per siswa. Contoh baris: `006112`,
   `Andi Saputra`, `andi@sekolah.id`, `08123456789`, `XII-A`, `IPA`.
5. Simpan sel NIS sebagai teks bila memakai leading zero.

## 4. Cara menyiapkan Excel guru

Sama seperti siswa, dengan header identifier `NIP` (wajib).
Contoh baris: `007889`, `Ibu Guru`, `guru@sekolah.id`,
`0819000001`, (kelas opsional), `Matematika`. Import guru
dilakukan dari halaman **Import Guru** yang terpisah; jenis
import menentukan role, bukan isi file.

## 5. Aturan NIS/NIP

- NIS/NIP selalu diperlakukan sebagai string, bukan angka.
- Leading zero dipertahankan: `006112` tetap `006112`, tidak
  pernah menjadi `6112`.
- Spasi di ujung dipangkas; spasi ganda di nama diciutkan.
- Nama tidak diubah kapitalisasinya oleh sistem.
- Email dipangkas dan diubah ke huruf kecil.
- Sel kosong pada kolom opsional dibaca sebagai kosong.
- Jika Excel menyimpan identifier sebagai angka, sistem membaca
  angka tersebut apa adanya dan tidak mengarang kembali nol yang
  sudah hilang di file. Format tampilan zero-pad Excel
  (mis. `000000`) dihormati sesuai metadata file.

## 6. Aturan password awal

- Password awal siswa = NIS. Password awal guru = NIP.
- Password selalu disimpan sebagai hash Django, tidak pernah
  sebagai teks biasa di database, log, pesan error, atau halaman
  hasil import.
- File Excel tidak memiliki kolom password dan tidak membutuhkannya.
- Username tidak dapat diubah, termasuk oleh pemilik akun.

## 7. First-login password change

1. Pengguna baru login dengan NIS/NIP sebagai username dan password.
2. Sistem langsung mengarahkan ke halaman ganti password sebelum
   dashboard atau fitur lain dapat dibuka. Upaya membuka URL
   lain dialihkan ke halaman yang sama.
3. Setelah password baru disimpan, penanda wajib-ganti menjadi
   nonaktif dan pengguna masuk ke dashboard sesuai role.
4. Password lama tidak berlaku lagi. Sesi login tidak terputus
   saat mengganti password sendiri.

## 8. Duplicate handling

- Identifier yang sama dua kali dalam satu file: kedua baris
  ditolak sebagai error, dengan pesan yang menyebut identifier
  dan nomor barisnya.
- Identifier yang sudah dipakai akun di database: baris ditandai
  conflict dan tidak dibuat. Akun existing tidak pernah
  ditimpa atau diubah otomatis.
- Proteksi terakhir ada di database (unique username, NIS, NIP).
  Konflik yang muncul antara preview dan konfirmasi dilewati
  dengan alasan; kegagalan fatal membatalkan seluruh batch.

## 9. Error import

Alur import: Upload XLSX, Parse, Normalize, Validate, Preview,
Confirmation, Atomic Creation, Result. Tidak ada akun yang dibuat
sebelum konfirmasi, dan konfirmasi hanya aktif bila tidak ada
baris Invalid atau Conflict.

| Pesan | Artinya | Tindakan |
|---|---|---|
| Format file tidak didukung | Bukan `.xlsx` | Simpan ulang sebagai `.xlsx` |
| Berkas tidak dapat dibaca | File rusak/bukan Excel | Periksa file, unduh template |
| Sheet/header kosong | Tidak ada data | Isi minimal header + 1 baris |
| Kolom NIS/NIP tidak ditemukan | Header identifier hilang | Samakan header dengan template |
| NIS/NIP wajib diisi | Sel identifier kosong | Isi identifier baris tersebut |
| Duplikat dalam file | Identifier ganda | Hapus/satukan baris ganda |
| Sudah dipakai akun ... | Conflict database | Keluarkan baris dari file |
| Email tidak valid | Format email salah | Perbaiki sel email |
| Nama lengkap wajib diisi | Sel nama kosong | Isi nama baris tersebut |
| Ukuran file melebihi ... | File di atas 5 MB | Pecah menjadi beberapa file |

Halaman preview menampilkan ringkasan Total, Valid, Invalid,
dan Conflict beserta tabel per baris (nomor baris, identifier,
nama, email, kelas, department, status, pesan). Halaman hasil
menampilkan Total data, Berhasil dibuat, dan daftar baris yang
dilewati beserta alasannya.

## 10. Activate/deactivate account

1. Di **Kelola Pengguna**, gunakan tombol **Nonaktifkan** atau
   **Aktifkan** pada baris akun. Aksi ini via POST dengan
   konfirmasi dan CSRF.
2. Akun nonaktif tidak dapat login dan sesi lamanya ditolak di
   semua fitur. Password dan data akun tidak berubah.
3. Akun hasil import diperlakukan sama dengan akun lainnya.
4. Reset password (tombol **Reset**) mengembalikan password ke
   NIS/NIP awal dan mengaktifkan kembali kewajiban ganti
   password, tanpa mengubah role, username, identifier, atau
   status aktif.
