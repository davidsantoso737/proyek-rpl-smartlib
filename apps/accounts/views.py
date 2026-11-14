import datetime
import logging

from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.whatsapp.context import build_profile_context

from .authorization import active_login_required, role_required
from .excel_import import (
    ExcelImportError,
    build_template_workbook,
    parse_import_workbook,
    template_filename,
)
from .forms import (
    INPUT_CLASS,
    MANAGEABLE_ROLES,
    LoginForm,
    ManagedUserCreateForm,
    ManagedUserUpdateForm,
    ProfileForm,
)
from .import_validation import STATUS_VALID, build_preview
from .import_creation import create_imported_accounts

User = get_user_model()

logger = logging.getLogger(__name__)

USER_PAGE_SIZE = 15


def _safe_next(request):
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}
    ):
        return next_url
    return None


LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 300


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _login_throttle_key(request):
    username = (request.POST.get("username") or "").strip().lower()
    return f"login-attempts:{_client_ip(request)}:{username}"


def login_view(request):
    """Login + identifikasi role (FR-01, FR-02, BR-02).

    Brute-force mitigation: maksimal 5 kegagalan per username+IP
    dalam 5 menit; kunci tanpa membocorkan validitas username
    (pesan generik yang sama).
    """
    if request.user.is_authenticated and request.user.is_active:
        if getattr(request.user, "must_change_password", False):
            return redirect("accounts:password_change")
        return redirect("accounts:dashboard")
    if request.method == "POST":
        form = LoginForm(request, data=request.POST)
        throttle_key = _login_throttle_key(request)
        if cache.get(throttle_key, 0) >= LOGIN_MAX_ATTEMPTS:
            form.add_error(
                None,
                "Terlalu banyak percobaan masuk. "
                "Coba lagi beberapa menit kemudian.",
            )
        elif form.is_valid():
            cache.delete(throttle_key)
            user = form.get_user()
            login(request, user)
            # P1 — first-login enforcement: akun provisioning wajib
            # mengganti password sebelum akses normal (abaikan ?next=).
            if getattr(user, "must_change_password", False):
                return redirect("accounts:password_change")
            return redirect(_safe_next(request) or "accounts:dashboard")
        else:
            attempts = cache.get(throttle_key, 0) + 1
            cache.set(throttle_key, attempts, LOGIN_LOCKOUT_SECONDS)
            logger.warning(
                "Login gagal untuk %s dari %s (%s/%s percobaan).",
                request.POST.get("username", "?"),
                _client_ip(request),
                attempts,
                LOGIN_MAX_ATTEMPTS,
            )
    else:
        form = LoginForm(request)
    return render(
        request,
        "accounts/login.html",
        {"form": form, "next": request.GET.get("next", "")},
    )


@require_POST
def logout_view(request):
    """Logout hanya via POST (FR-01)."""
    logout(request)
    return redirect("/accounts/login/?logged_out=1")


@active_login_required
def dashboard_view(request):
    """Dispatcher dashboard sesuai role (FR-02, BR-03)."""
    role = request.user.role
    if role == User.Role.SISWA:
        return redirect("accounts:dashboard_siswa")
    if role == User.Role.GURU:
        return redirect("accounts:dashboard_guru")
    if role == User.Role.PUSTAKAWAN:
        return redirect("accounts:dashboard_pustakawan")
    raise PermissionDenied("Role akun Anda tidak dikenal.")


def _reader_home_context(request):
    """Presentation-only context for the Siswa/Guru library home.

    Read-only assembly from existing sources: the AI recommendation
    service, Category, and the active Book/Ebook rows with fields the
    catalog already selects. No model, URL, permission, or business
    logic change; a failure here degrades to empty sections.
    """
    context = {
        "reader_home": True,
        "home_recommendation": {"items": [], "basis": "general"},
        "home_categories": [],
        "active_category_id": "",
        "category_books": [],
        "category_has_more": False,
    }
    try:
        from apps.ai.recommendations import recommend_for_user
        from apps.catalog.models import Book, Category, Ebook

        recommendation = recommend_for_user(request.user, limit=4)
        book_ids = [
            item["id"] for item in recommendation["items"]
            if item["kind"] == "book"
        ]
        ebook_ids = [
            item["id"] for item in recommendation["items"]
            if item["kind"] == "ebook"
        ]
        covers = {}
        if book_ids:
            covers.update(
                (row["id"], row["cover_url"])
                for row in Book.objects.filter(pk__in=book_ids).values(
                    "id", "cover_url"
                )
            )
        if ebook_ids:
            covers.update(
                (row["id"], row["cover_url"])
                for row in Ebook.objects.filter(pk__in=ebook_ids).values(
                    "id", "cover_url"
                )
            )
        for item in recommendation["items"]:
            item["cover_url"] = covers.get(item["id"])
        context["home_recommendation"] = recommendation

        categories = list(Category.objects.order_by("name"))
        context["home_categories"] = categories

        category_param = (request.GET.get("kategori") or "").strip()
        active_id = (
            int(category_param) if category_param.isdigit() else None
        )
        if active_id is not None and not any(
            cat.pk == active_id for cat in categories
        ):
            active_id = None
        context["active_category_id"] = (
            str(active_id) if active_id is not None else ""
        )

        held = {
            (item["kind"], item["id"])
            for item in recommendation["items"]
        }
        books_qs = (
            Book.objects.filter(is_active=True)
            .select_related("category")
            .prefetch_related("bookauthor_set__author")
            .order_by("title")
        )
        ebooks_qs = (
            Ebook.objects.filter(is_active=True)
            .select_related("category")
            .prefetch_related("ebookauthor_set__author")
            .order_by("title")
        )
        if active_id is not None:
            books_qs = books_qs.filter(category_id=active_id)
            ebooks_qs = ebooks_qs.filter(category_id=active_id)
        preview = []
        for book in books_qs[:8]:
            if ("book", book.pk) in held:
                continue
            preview.append(
                {
                    "kind": "book",
                    "id": book.pk,
                    "title": book.title,
                    "authors": [
                        rel.author.name
                        for rel in book.bookauthor_set.all()
                    ],
                    "category": book.category.name,
                    "cover_url": book.cover_url,
                }
            )
        for ebook in ebooks_qs[:8]:
            if ("ebook", ebook.pk) in held:
                continue
            preview.append(
                {
                    "kind": "ebook",
                    "id": ebook.pk,
                    "title": ebook.title,
                    "authors": [
                        rel.author.name
                        for rel in ebook.ebookauthor_set.all()
                    ],
                    "category": ebook.category.name,
                    "cover_url": ebook.cover_url,
                }
            )
        preview.sort(key=lambda row: row["title"].lower())
        context["category_books"] = preview[:8]
        context["category_has_more"] = (
            not context["category_books"]
            and (books_qs.exists() or ebooks_qs.exists())
        )
    except Exception:
        logger.exception("Beranda reader: gagal menyusun konteks.")
    return context


@role_required(User.Role.SISWA)
def siswa_dashboard_view(request):
    context = {
        "dashboard_title": "Beranda",
        "dashboard_intro": (
            "Temukan bacaan berikutnya dari koleksi perpustakaan."
        ),
    }
    context.update(_reader_home_context(request))
    return render(request, "accounts/dashboard.html", context)


@role_required(User.Role.GURU)
def guru_dashboard_view(request):
    context = {
        "dashboard_title": "Beranda",
        "dashboard_intro": (
            "Temukan bacaan berikutnya dari koleksi perpustakaan."
        ),
    }
    context.update(_reader_home_context(request))
    return render(request, "accounts/dashboard.html", context)


MONTH_LABELS = [
    "Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
    "Jul", "Agu", "Sep", "Okt", "Nov", "Des",
]


def _six_month_keys():
    """(tahun, bulan) enam bulan terakhir menurut tanggal lokal."""
    today = timezone.localdate()
    year, month = today.year, today.month
    keys = []
    for _ in range(6):
        keys.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(keys))


def _librarian_dashboard_stats():
    """Agregat read-only untuk dashboard pustakawan.

    Definisi mengikuti tabel yang sudah ada: judul buku = baris Book,
    eksemplar = baris BookCopy per status, e-book = baris Ebook,
    peminjaman aktif = Loan berstatus ACTIVE. Metrik koleksi diambil dari
    ``apps.catalog.statistics`` agar AI Assistant memakai angka yang sama.
    Tren memakai tanggal pengajuan (Loan.requested_at) dan pengembalian
    (LoanItem.returned_at) agar konsisten dengan Laporan
    Peminjaman/Pengembalian.
    """
    from apps.catalog.statistics import (
        COPY_STATUS_ORDER,
        collection_statistics,
    )
    from apps.circulation.models import EbookLoan, Loan, LoanItem
    from apps.circulation.services import EBOOK_APPROVED

    now = timezone.now()
    stats = {
        "total_books": 0,
        "total_copies": 0,
        "copies_available": 0,
        "total_ebooks": 0,
        "active_loans": 0,
        "active_ebooks": 0,
        "copy_status_total": 0,
        "copy_status_rows": [],
        "trend_months": [],
        "trend_max": 0,
        "trend_loan_total": 0,
        "trend_return_total": 0,
    }

    collection = collection_statistics(detail_limit=1)
    stats["total_books"] = collection["titles_total"]
    stats["total_ebooks"] = collection["ebooks_total"]
    stats["active_loans"] = collection["active_loans"]
    stats["active_ebooks"] = EbookLoan.objects.filter(
        status=EBOOK_APPROVED,
        started_at__lte=now,
        expires_at__gt=now,
    ).count()

    copy_counts = collection["copy_status_counts"]
    total_copies = collection["copies_total"]
    stats["total_copies"] = total_copies
    stats["copies_available"] = collection["copies_available"]
    stats["copy_status_total"] = total_copies
    tones = ("emerald", "blue", "amber", "rose")
    for (code, label), tone in zip(COPY_STATUS_ORDER, tones):
        value = copy_counts.get(code, 0)
        pct = round(value * 100 / total_copies) if total_copies else 0
        if value and not pct:
            pct = 1
        stats["copy_status_rows"].append(
            {
                "label": label,
                "value": value,
                "pct": pct,
                "tone": tone,
            }
        )

    keys = _six_month_keys()
    tz = timezone.get_current_timezone()
    loan_map = {}
    return_map = {}
    for year, month in keys:
        start = datetime.datetime(year, month, 1, tzinfo=tz)
        if month == 12:
            next_year, next_month = year + 1, 1
        else:
            next_year, next_month = year, month + 1
        end = datetime.datetime(next_year, next_month, 1, tzinfo=tz)
        loan_map[(year, month)] = Loan.objects.filter(
            requested_at__gte=start,
            requested_at__lt=end,
        ).count()
        return_map[(year, month)] = LoanItem.objects.filter(
            returned_at__gte=start,
            returned_at__lt=end,
        ).count()
    months = []
    for year, month in keys:
        months.append(
            {
                "label": MONTH_LABELS[month - 1],
                "loans": loan_map.get((year, month), 0),
                "returns": return_map.get((year, month), 0),
            }
        )
    peak = max(
        [row["loans"] for row in months]
        + [row["returns"] for row in months]
        + [0]
    )
    for row in months:
        row["loans_pct"] = round(row["loans"] * 100 / peak) if peak else 0
        row["returns_pct"] = round(row["returns"] * 100 / peak) if peak else 0
        if row["loans"] and not row["loans_pct"]:
            row["loans_pct"] = 1
        if row["returns"] and not row["returns_pct"]:
            row["returns_pct"] = 1
    stats["trend_months"] = months
    stats["trend_max"] = peak
    stats["trend_loan_total"] = sum(row["loans"] for row in months)
    stats["trend_return_total"] = sum(row["returns"] for row in months)
    return stats


@role_required(User.Role.PUSTAKAWAN)
def pustakawan_dashboard_view(request):
    context = {
        "dashboard_title": "Dashboard Pustakawan",
        "dashboard_intro": (
            "Pantau koleksi, sirkulasi, dan antrean kerja perpustakaan."
        ),
        "active_loans": 0,
        "total_books": 0,
        "total_copies": 0,
        "copies_available": 0,
        "total_ebooks": 0,
        "active_ebooks": 0,
        "copy_status_total": 0,
        "copy_status_rows": [],
        "trend_months": [],
        "trend_max": 0,
        "trend_loan_total": 0,
        "trend_return_total": 0,
    }
    try:
        context.update(_librarian_dashboard_stats())
    except Exception:
        logger.exception("Dashboard pustakawan: gagal menghitung statistik.")
    return render(request, "accounts/dashboard.html", context)


@active_login_required
def profile_view(request):
    """Lihat/ubah profil milik sendiri (FR-03, BR-03).

    Form selalu terikat ke ``request.user`` sehingga tidak ada parameter
    id pengguna: profil orang lain tidak dapat diakses maupun diubah.
    """
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profil berhasil diperbarui.")
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=request.user)
    context = {"form": form}
    context.update(build_profile_context(request))
    return render(request, "accounts/profile.html", context)


@active_login_required
def password_change_view(request):
    """Ubah password sendiri dengan verifikasi password lama (NFR-04).

    P1: keberhasilan juga menuntaskan first-login enforcement
    (must_change_password -> False). View ini dikecualikan dari
    enforcement agar tidak terjadi redirect loop.
    """
    if request.method == "POST":
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            form.save()
            user = form.user
            if getattr(user, "must_change_password", False):
                user.must_change_password = False
                user.save(update_fields=["must_change_password", "updated_at"])
            update_session_auth_hash(request, user)
            messages.success(request, "Password berhasil diubah.")
            return redirect("accounts:profile")
    else:
        form = PasswordChangeForm(request.user)
    for field in form.fields.values():
        field.widget.attrs.update({"class": INPUT_CLASS})
    return render(request, "accounts/password_change.html", {"form": form})


def _manageable_user_or_404(pk):
    """Hanya Siswa/Guru yang dapat dikelola via public UI (scope)."""
    return get_object_or_404(User, pk=pk, role__in=MANAGEABLE_ROLES)


@role_required(User.Role.PUSTAKAWAN)
def user_list_view(request):
    """Daftar akun Siswa/Guru (FR-04, BR-77). Hanya Pustakawan."""
    q = (request.GET.get("q") or "").strip()
    role_filter = request.GET.get("role") or ""
    users = User.objects.filter(role__in=MANAGEABLE_ROLES)
    if role_filter in MANAGEABLE_ROLES:
        users = users.filter(role=role_filter)
    else:
        role_filter = ""
    if q:
        # Username hasil provisioning = NIS/NIP, tetapi akun lama
        # dapat memiliki username berbeda; cari di ketiganya.
        users = users.filter(
            Q(username__icontains=q)
            | Q(full_name__icontains=q)
            | Q(nis__icontains=q)
            | Q(nip__icontains=q)
        )
    users = users.order_by("role", "username")
    paginator = Paginator(users, USER_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "accounts/user_list.html",
        {
            "page_obj": page_obj,
            "q": q,
            "role_filter": role_filter,
            "querystring": params.urlencode(),
            "result_count": paginator.count,
        },
    )


@role_required(User.Role.PUSTAKAWAN)
def user_create_view(request, role):
    """Tambah akun Siswa/Guru (FR-04, FR-07, BR-77). Hanya Pustakawan.

    P2 — manual provisioning: username/password awal otomatis =
    NIS (Siswa) atau NIP (Guru); ``must_change_password`` True.
    """
    if role not in MANAGEABLE_ROLES:
        raise PermissionDenied(
            "Role tersebut tidak dapat dibuat melalui halaman ini."
        )
    if request.method == "POST":
        form = ManagedUserCreateForm(request.POST, role=role)
        if form.is_valid():
            user = form.save()
            logger.info(
                "Pustakawan %s membuat akun %s (%s).",
                request.user.username,
                user.username,
                user.role,
            )
            identifier = user.nis or user.nip or user.username
            messages.success(
                request,
                f"Akun {user.username} ({user.get_role_display()}) "
                "berhasil dibuat. Username dan password awal: "
                f"{identifier}.",
            )
            return redirect("accounts:user_list")
    else:
        form = ManagedUserCreateForm(role=role)
    return render(
        request,
        "accounts/user_form.html",
        {
            "form": form,
            "form_title": (
                "Tambah Siswa" if role == User.Role.SISWA else "Tambah Guru"
            ),
            "submit_label": "Buat akun",
            "cancel_url": "accounts:user_list",
            "info_text": (
                "Username dan password awal otomatis sama dengan NIS. "
                "Siswa wajib mengganti password saat login pertama."
                if role == User.Role.SISWA
                else "Username dan password awal otomatis sama dengan NIP. "
                "Guru wajib mengganti password saat login pertama."
            ),
        },
    )


@role_required(User.Role.PUSTAKAWAN)
def user_update_view(request, pk):
    """Ubah data Siswa/Guru (FR-05, FR-07, BR-77). Hanya Pustakawan."""
    user = _manageable_user_or_404(pk)
    if request.method == "POST":
        form = ManagedUserUpdateForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            logger.info(
                "Pustakawan %s mengubah akun %s (%s).",
                request.user.username,
                user.username,
                user.role,
            )
            messages.success(
                request, f"Data {user.username} berhasil diperbarui."
            )
            return redirect("accounts:user_list")
    else:
        form = ManagedUserUpdateForm(instance=user)
    return render(
        request,
        "accounts/user_form.html",
        {
            "form": form,
            "form_title": f"Ubah {user.username}",
            "submit_label": "Simpan perubahan",
            "cancel_url": "accounts:user_list",
        },
    )


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def user_toggle_active_view(request, pk):
    """Aktif/nonaktifkan akun Siswa/Guru (FR-06, BR-78). Hanya Pustakawan."""
    user = _manageable_user_or_404(pk)
    user.is_active = not user.is_active
    user.save(update_fields=["is_active", "updated_at"])
    logger.info(
        "Pustakawan %s %s akun %s (%s).",
        request.user.username,
        "mengaktifkan" if user.is_active else "menonaktifkan",
        user.username,
        user.role,
    )
    messages.success(
        request,
        f"Akun {user.username} berhasil "
        f"{'diaktifkan' if user.is_active else 'dinonaktifkan'}.",
    )
    return redirect("accounts:user_list")


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def user_reset_password_view(request, pk):
    """Reset password Siswa/Guru ke identifier. Hanya Pustakawan.

    P6: password dikembalikan ke NIS (Siswa) / NIP (Guru), di-hash
    via set_password, dan ``must_change_password`` diset True
    sehingga first-login enforcement (P1) berlaku lagi. Role,
    username, identifier, dan status aktif tidak diubah. Tanpa
    nilai password di pesan maupun log.
    """
    user = _manageable_user_or_404(pk)
    if user.role == User.Role.SISWA:
        identifier = (user.nis or "").strip()
        label = "NIS"
    else:
        identifier = (user.nip or "").strip()
        label = "NIP"
    if not identifier:
        messages.error(
            request,
            f"Akun {user.username} tidak memiliki {label} "
            "sehingga password tidak dapat direset.",
        )
        return redirect("accounts:user_list")
    user.set_password(identifier)
    user.must_change_password = True
    user.save(update_fields=["password", "must_change_password", "updated_at"])
    logger.info(
        "Pustakawan %s mereset password akun %s (%s).",
        request.user.username,
        user.username,
        user.role,
    )
    messages.success(
        request,
        f"Password {user.username} direset ke {label} awal. "
        "Pengguna wajib mengganti password saat login berikutnya.",
    )
    return redirect("accounts:user_list")


IMPORT_SESSION_KEY = "account_import_preview"

IMPORT_XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument"
    ".spreadsheetml.sheet"
)


def _import_kind_or_403(kind):
    """Hanya SISWA/GURU yang dapat diimport via public UI (scope)."""
    if kind not in MANAGEABLE_ROLES:
        raise PermissionDenied(
            "Jenis import tersebut tidak tersedia."
        )
    return kind


def _import_context(kind):
    return {
        "kind": kind,
        "kind_title": "Siswa" if kind == User.Role.SISWA else "Guru",
    }


@role_required(User.Role.PUSTAKAWAN)
def import_upload_view(request, kind):
    """Upload .xlsx -> parse -> normalisasi -> validasi -> preview.

    Konfirmasi + bulk creation terjadi di ``import_confirm_view``.
    File hanya dibaca (openpyxl, data_only), tidak dieksekusi;
    validasi ekstensi + isi dilakukan server-side.
    """
    _import_kind_or_403(kind)
    context = _import_context(kind)
    if request.method == "POST":
        uploaded = request.FILES.get("file")
        if uploaded is None or uploaded.size == 0:
            context["form_error"] = (
                "Pilih berkas .xlsx yang akan diimport."
            )
            return render(request, "accounts/import_upload.html", context)
        try:
            parsed = parse_import_workbook(uploaded, kind)
        except ExcelImportError as exc:
            context["form_error"] = str(exc)
            return render(request, "accounts/import_upload.html", context)
        preview = build_preview(parsed)
        request.session[IMPORT_SESSION_KEY] = {
            "kind": kind,
            "parsed": parsed,
        }
        context["preview"] = preview
        return render(request, "accounts/import_preview.html", context)
    return render(request, "accounts/import_upload.html", context)


@role_required(User.Role.PUSTAKAWAN)
def import_template_view(request, kind):
    """Unduh template .xlsx untuk jenis import (Hanya Pustakawan)."""
    _import_kind_or_403(kind)
    response = HttpResponse(
        build_template_workbook(kind),
        content_type=IMPORT_XLSX_CONTENT_TYPE,
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{template_filename(kind)}"'
    )
    return response


@role_required(User.Role.PUSTAKAWAN)
@require_POST
def import_confirm_view(request, kind):
    """Konfirmasi hasil preview dengan validasi ulang terhadap database.

    P5: baris VALID dibuat via bulk creation atomic (transaksi penuh;
    conflict tak terduga dilewati dengan alasan, error fatal
    me-rollback seluruh batch). Password awal = identifier (di-hash),
    tidak pernah ditampilkan per akun.
    """
    _import_kind_or_403(kind)
    stored = request.session.get(IMPORT_SESSION_KEY)
    if not stored or stored.get("kind") != kind:
        messages.error(
            request,
            "Data preview tidak ditemukan. Silakan upload ulang berkas.",
        )
        return redirect("accounts:import_upload", kind=kind)
    preview = build_preview(stored["parsed"])
    context = _import_context(kind)
    context["preview"] = preview
    if not preview["can_confirm"]:
        messages.error(
            request,
            "Masih ada baris error atau conflict. "
            "Perbaiki berkas lalu upload ulang; "
            "tidak ada akun yang dibuat.",
        )
        return render(request, "accounts/import_preview.html", context)
    valid_rows = [
        {"row_number": row["row_number"], "data": row["data"]}
        for row in preview["rows"]
        if row["status"] == STATUS_VALID
    ]
    try:
        result = create_imported_accounts(kind, valid_rows)
    except Exception:
        # Fatal: tidak ada yang ter-commit (rollback oleh atomic).
        # Tidak ada password di log/exception (hanya hitungan).
        logger.exception(
            "Import %s oleh %s gagal total (%s baris); rollback.",
            kind,
            request.user.username,
            len(valid_rows),
        )
        messages.error(
            request,
            "Terjadi kesalahan saat membuat akun. "
            "Tidak ada akun yang dibuat; silakan coba lagi.",
        )
        return render(request, "accounts/import_preview.html", context)
    request.session.pop(IMPORT_SESSION_KEY, None)
    logger.info(
        "Pustakawan %s import %s selesai: %s dibuat, %s dilewati.",
        request.user.username,
        kind,
        result["created_count"],
        result["skipped_count"],
    )
    context["result"] = result
    return render(request, "accounts/import_result.html", context)
