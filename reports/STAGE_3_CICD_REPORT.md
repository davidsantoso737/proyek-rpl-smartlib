# Stage 3 CI/CD Report

Tanggal: 2026-11-17
Workflow: `.github/workflows/ci.yml`
Status: configured dan divalidasi lokal, belum dijalankan di GitHub

## 1. Objective

Menambahkan pipeline GitHub Actions yang menjalankan pemeriksaan kualitas secara konsisten pada push dan pull request, tanpa menyimpan secret dan tanpa menambah deployment yang belum tersedia.

## 2. Workflow Design

Trigger:

- `push`
- `pull_request`

Konfigurasi:

- Runner: `ubuntu-latest`
- Timeout: 20 menit
- Permission: `contents: read`
- Concurrency per ref dengan `cancel-in-progress`
- Service MySQL 8.4
- Python 3.14
- pip cache berdasarkan `requirements.txt`

## 3. MySQL Service

Service database:

- Image: `mysql:8.4`
- Database awal: `smartlib_test_baseline`
- User: `root`
- Password hanya untuk service CI, bukan secret produksi
- Health check: `mysqladmin ping`
- Port: `3306`

`config.settings_test` memaksa `DB_NAME` ke database test terpisah. Django membuat dan menghapus `test_smartlib_test_baseline` selama test.

## 4. Pipeline Steps

1. Checkout repository.
2. Setup Python 3.14 dan cache pip.
3. Install `build-essential`, `default-libmysqlclient-dev`, dan `pkg-config`.
4. Install `requirements.txt` dan `coverage==7.17.3`.
5. Jalankan `python manage.py check`.
6. Jalankan `python manage.py makemigrations --check --dry-run`.
7. Jalankan test dengan coverage.
8. Jalankan `coverage report --fail-under=85`.
9. Upload artifact `coverage-json`.

## 5. Security

- Tidak ada secret produksi di workflow.
- AI, Fonnte, dan WhatsApp eksternal dinonaktifkan oleh test settings.
- Permission workflow dibatasi read-only.
- Tidak ada deployment step.
- Tidak ada kredensial database development.

## 6. Local Validation

Validasi yang dilakukan:

- YAML berhasil diparse.
- Key utama workflow terbaca: `name`, `on`, `permissions`, `concurrency`, `jobs`.
- Job `test` memiliki 8 step.
- `manage.py check`: no issues.
- `manage.py makemigrations --check --dry-run`: no changes detected.
- Full test suite: 662 lulus.
- Coverage: 89.48%.
- `coverage report --fail-under=85`: pass.

## 7. Remote Execution Status

Workflow belum dijalankan di GitHub karena perubahan belum di-commit atau di-push sesuai instruksi. Tidak ada run ID, status check, atau URL GitHub Actions yang dapat dilaporkan. Pipeline akan aktif setelah perubahan di-commit dan di-push ke branch yang memiliki workflow ini.

## 8. First Run Risks

- Ketersediaan Python 3.14 pada `actions/setup-python`.
- Build `mysqlclient` pada Python 3.14 di Ubuntu, meskipun runtime aplikasi memakai PyMySQL.
- Kesiapan service MySQL sebelum Django test dimulai.
- Perbedaan minor antara MySQL lokal 8.4.3 dan image `mysql:8.4` terbaru.

Health check service dan system dependencies sudah disiapkan untuk mengurangi risiko tersebut.

## 9. Conclusion

Stage 3 selesai pada level konfigurasi dan validasi lokal. Pipeline belum dieksekusi di GitHub, sehingga status remote harus diverifikasi setelah commit dan push.
