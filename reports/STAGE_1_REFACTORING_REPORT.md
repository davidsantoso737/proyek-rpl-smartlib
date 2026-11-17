# Stage 1 Refactoring Report

Tanggal: 2026-11-17
Branch: `test/refactoring-code`
HEAD saat audit: `51900fd`

## 1. Objective

Meningkatkan keterbacaan, tanggung jawab fungsi, dan arah dependensi tanpa mengubah perilaku aplikasi. Perubahan dibatasi pada ekstraksi helper, pemusatan duplikasi, dan perbaikan import.

## 2. Baseline

- Existing test: 662 test, seluruhnya lulus sebelum refactoring.
- `manage.py check`: tidak ada issue.
- Working tree awal: hanya `config/settings_test.py` yang belum di-track.
- Tidak ada migration, perubahan schema, URL, permission, atau response contract.

## 3. Changes

### 3.1 Pagination helper bersama

File baru: `apps/core/pagination.py`.

`paginate_queryset()` memusatkan:

- pembuatan `Paginator`;
- pengambilan page dari `request.GET`;
- penghapusan parameter `page` dari querystring.

Dipakai oleh:

- `apps/catalog/views.py`: katalog, detail buku, daftar buku kelola, daftar e-book kelola.
- `apps/accounts/views.py`: daftar pengguna.

Tidak ada perubahan ukuran halaman, nomor halaman, querystring, atau `result_count`.

### 3.2 Dashboard pustakawan

File: `apps/accounts/views.py`.

Ekstraksi:

- `_percentage()` untuk perhitungan persentase dengan minimum 1 persen untuk nilai nonzero;
- `_copy_status_rows()` untuk membentuk baris status eksemplar;
- `_month_bounds()` untuk batas awal dan akhir bulan;
- `_loan_trend()` untuk agregasi tren enam bulan.

Nilai total, label, tone, persentase, dan urutan bulan dipertahankan.

### 3.3 Konflik nomor WhatsApp

File: `apps/whatsapp/connection.py`.

Ekstraksi:

- `_other_owner_connection()` untuk koneksi milik pengguna lain;
- `_same_user_connection()` untuk koneksi pengguna yang sama berdasarkan status.

Status hasil `phone_in_use`, `already_pending`, dan `awaiting_verification` tidak berubah.

### 3.4 Notifikasi jatuh tempo dan terlambat

File: `apps/notifications/services.py`.

Ekstraksi:

- `_loan_titles()` untuk ringkasan judul;
- `_create_loan_notification()` untuk pengecekan idempotensi dan pembuatan notifikasi.

Teks notifikasi, tipe, reference, dan jumlah notifikasi yang dibuat tidak berubah.

### 3.5 Arah dependensi laporan

File: `apps/reports/services.py`.

`STATUS_TERSEDIA` sekarang diimpor langsung dari `apps.catalog.availability`, bukan melalui `apps.circulation.services`. Nilai konstanta tetap sama.

## 4. Files Changed

Existing files:

- `apps/accounts/views.py`
- `apps/catalog/views.py`
- `apps/notifications/services.py`
- `apps/reports/services.py`
- `apps/whatsapp/connection.py`

New file:

- `apps/core/pagination.py`

Statistik diff untuk file existing: 166 insertions, 166 deletions. File helper baru: 12 baris.

## 5. Verification

### 5.1 Static checks

- `manage.py check`: no issues.
- `git diff --check`: tidak ada whitespace error.

### 5.2 Targeted tests

| Scope | Tests | Result | Time |
| --- | ---: | --- | ---: |
| `apps.catalog`, `apps.accounts` | 185 | OK | 8.511s |
| `apps.accounts` | 145 | OK | 5.914s |
| `apps.whatsapp` | 117 | OK | 2.655s |
| `apps.notifications`, `apps.reports` | 25 | OK | 1.844s |

### 5.3 Full regression

- 662 tests dijalankan.
- 662 lulus.
- 0 failure, 0 error, 0 skipped.
- Waktu: 29.251 detik.
- Test database dihapus otomatis.
- Database development tidak disentuh.

## 6. Behavior Preservation

- Tidak ada perubahan model atau migration.
- Tidak ada perubahan URL, view permission, atau format response.
- Tidak ada perubahan teks antarmuka.
- Tidak ada test yang diubah atau dihapus.
- Tidak ada dependency baru untuk aplikasi.
- Tidak ada commit, push, atau merge.

## 7. Deferred Items

Item berikut sengaja belum diubah karena risiko perubahan perilaku atau cakupan yang terlalu besar:

- Duplikasi format ringkasan koleksi di `apps/ai/services.py`.
- Predikat window e-book aktif dan kandidat `EbookLoan.is_active_at`.
- Pemisahan `_reader_home_context()` di `apps/accounts/views.py`.
- Pemisahan fungsi panjang di `apps/ai/services.py`, `apps/circulation/services.py`, dan `apps/whatsapp/connection.py`.
- BOM UTF-8 pada `apps/core/test_pending.py`.

## 8. Conclusion

Stage 1 selesai dengan refactoring terbatas pada helper dan duplikasi low-risk. Seluruh 662 test tetap lulus. Perubahan siap dilanjutkan ke Stage 2 tanpa commit.
