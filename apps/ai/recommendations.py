"""Engine rekomendasi koleksi deterministik (FR-78 – FR-81, BR-70 – BR-73).

INFORMATION ONLY: tidak membuat peminjaman, akses, atau persetujuan.
Sumber: koleksi aktif, kategori, penulis, dan riwayat peminjaman milik
sendiri yang diizinkan. Tanpa history cukup -> fallback umum yang
diberi label jujur (tanpa personalisasi palsu). Alasan (FR-80)
berbasis data: hanya klaim yang didukung skor.
"""

from collections import Counter

from django.db.models import Count, Q

from apps.catalog.availability import STATUS_TERSEDIA
from apps.catalog.models import Book, Ebook
from apps.circulation.models import EbookLoan, Loan

HISTORY_LOAN_STATUSES = ("ACTIVE", "COMPLETED", "APPROVED")
EBOOK_HISTORY_STATUSES = ("APPROVED",)


def _history_signals(user):
    """Kategori & penulis dari riwayat milik sendiri (diizinkan)."""
    categories = Counter()
    authors = Counter()
    titles_held = set()
    items = (
        Loan.objects.filter(user=user, status__in=HISTORY_LOAN_STATUSES)
        .prefetch_related("items__book_copy__book__bookauthor_set__author",
                          "items__book_copy__book__category")
    )
    for loan in items:
        for item in loan.items.all():
            book = item.book_copy.book
            titles_held.add(("book", book.pk))
            categories[book.category.name] += 1
            for rel in book.bookauthor_set.all():
                authors[rel.author.name] += 1
    for eloan in EbookLoan.objects.filter(
        user=user, status__in=EBOOK_HISTORY_STATUSES
    ).select_related("ebook__category").prefetch_related(
        "ebook__ebookauthor_set__author"
    ):
        titles_held.add(("ebook", eloan.ebook.pk))
        categories[eloan.ebook.category.name] += 1
        for rel in eloan.ebook.ebookauthor_set.all():
            authors[rel.author.name] += 1
    # Judul yang sedang diminta/aktif jangan direkomendasikan ulang.
    pending = Loan.objects.filter(
        user=user, status__in=("PENDING_APPROVAL", "APPROVED")
    ).prefetch_related("items__book_copy__book")
    for loan in pending:
        for item in loan.items.all():
            titles_held.add(("book", item.book_copy.book.pk))
    pending_ebooks = EbookLoan.objects.filter(
        user=user, status="PENDING_APPROVAL"
    ).select_related("ebook")
    for eloan in pending_ebooks:
        titles_held.add(("ebook", eloan.ebook.pk))
    return categories, authors, titles_held


def _candidate_books(exclude, limit=50):
    books = (
        Book.objects.filter(is_active=True)
        .select_related("category")
        .prefetch_related("bookauthor_set__author", "copies")
        .annotate(
            available=Count(
                "copies", filter=Q(copies__status=STATUS_TERSEDIA)
            )
        )
        .filter(available__gt=0)
        .order_by("title")[:limit]
    )
    result = []
    for book in books:
        if ("book", book.pk) in exclude:
            continue
        result.append(
            {
                "kind": "book",
                "id": book.pk,
                "title": book.title,
                "category": book.category.name,
                "authors": [rel.author.name for rel in book.bookauthor_set.all()],
                "available": book.available,
            }
        )
    return result


def _candidate_ebooks(exclude, limit=50):
    ebooks = (
        Ebook.objects.filter(is_active=True)
        .select_related("category")
        .prefetch_related("ebookauthor_set__author")
        .order_by("title")[:limit]
    )
    result = []
    for ebook in ebooks:
        if ("ebook", ebook.pk) in exclude:
            continue
        result.append(
            {
                "kind": "ebook",
                "id": ebook.pk,
                "title": ebook.title,
                "category": ebook.category.name,
                "authors": [rel.author.name for rel in ebook.ebookauthor_set.all()],
                "available": None,
            }
        )
    return result


def recommend_for_user(user, limit=6):
    """Kembalikan {items, basis, history_count}.

    basis "personal" bila ada sinyal riwayat, "general" bila tidak.
    Tiap item membawa "reason" berbasis data.
    """
    categories, authors, held = _history_signals(user)
    history_count = sum(categories.values())
    candidates = _candidate_books(held) + _candidate_ebooks(held)

    if not candidates:
        return {"items": [], "basis": "general", "history_count": 0}
    if not categories and not authors:
        ranked = sorted(
            candidates,
            key=lambda c: (-(c["available"] or 0), c["title"].lower()),
        )
        for item in ranked[:limit]:
            item["reason"] = "Koleksi tersedia yang dapat langsung dipinjam."
        return {"items": ranked[:limit], "basis": "general", "history_count": 0}

    scored = []
    for item in candidates:
        score = 0
        reasons = []
        cat_hits = categories.get(item["category"], 0)
        if cat_hits:
            score += 2 * cat_hits
            reasons.append(
                f"kategori {item['category']} sama dengan "
                f"{cat_hits} pinjaman Anda sebelumnya"
            )
        author_hits = [
            (name, authors[name])
            for name in item["authors"]
            if name in authors
        ]
        if author_hits:
            top_name, top_count = max(author_hits, key=lambda pair: pair[1])
            score += 3 * top_count
            reasons.append(
                f"penulis {top_name} pernah Anda pinjam "
                f"({top_count}x)"
            )
        if score > 0:
            item["reason"] = "Direkomendasikan karena " + " dan ".join(reasons) + "."
            scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["title"].lower()))
    items = [item for _, item in scored[:limit]]
    if not items:
        # Riwayat ada tetapi tak ada kandidat sejalur: fallback jujur.
        fallback = sorted(
            candidates,
            key=lambda c: (-(c["available"] or 0), c["title"].lower()),
        )[:limit]
        for item in fallback:
            item["reason"] = (
                "Belum ada koleksi sejalur riwayat Anda; "
                "ini koleksi tersedia lainnya."
            )
        return {"items": fallback, "basis": "general", "history_count": history_count}
    return {"items": items, "basis": "personal", "history_count": history_count}


def narrate(provider, user_display, recommendation):
    """Satu panggilan provider untuk merangkai teks (opsional).

    Gagal/kosong -> None; pemanggil memakai teks deterministik.
    """
    if provider is None or not recommendation["items"]:
        return None
    listing = "\n".join(
        f"- {item['title']} ({item['category']}): {item['reason']}"
        for item in recommendation["items"]
    )
    try:
        return provider.complete(
            "Anda asisten perpustakaan. Tulis ringkasan rekomendasi dalam "
            "1-2 paragraf Bahasa Indonesia yang ramah, mengalir, dan mudah "
            "dipahami. Sebutkan bahwa rekomendasi mempertimbangkan riwayat "
            "peminjaman bila basisnya personal. Jangan menambah judul atau "
            "penulis di luar daftar. Jangan menampilkan JSON, nama field, "
            "skor, nilai true/false, alasan mentah, karakter escape, atau "
            "proses analisis internal. Jangan menjanjikan peminjaman atau "
            "akses.",
            f"Pengguna: {user_display}\nDaftar rekomendasi:\n{listing}",
            {
                "jumlah_rekomendasi": len(recommendation["items"]),
                "basis": recommendation.get("basis"),
                "jumlah_riwayat": recommendation.get("history_count", 0),
            },
        )
    except Exception:
        return None
