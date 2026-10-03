"""Konstanta & helper ketersediaan koleksi fisik.

Nilai status berasal dari data (BR-13): eksemplar dengan status
``TERSEDIA`` dapat dipinjam. Modul ini sengaja terpisah dari model
agar tidak ada perubahan schema/ERD; dipakai katalog dan (nanti)
sirkulasi dari satu sumber yang sama.
"""

STATUS_TERSEDIA = "TERSEDIA"


def count_available_copies(book):
    """Jumlah eksemplar TERSEDIA untuk satu buku (tanpa query tambahan
    bila ``available_copies`` sudah di-annotate)."""
    annotated = getattr(book, "available_copies", None)
    if annotated is not None:
        return annotated
    return book.copies.filter(status=STATUS_TERSEDIA).count()


def is_copy_available(copy):
    return copy.status == STATUS_TERSEDIA
