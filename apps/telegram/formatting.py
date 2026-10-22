import datetime
import html
from dataclasses import dataclass

from django.utils import timezone

BOOK_TITLE_PREFIX = "📚 <b>SMARTLIB"
REMINDER_TITLE_PREFIX = "⏰ <b>SMARTLIB"
FORMATTED_TITLE_PREFIXES = (BOOK_TITLE_PREFIX, REMINDER_TITLE_PREFIX)

MAX_TITLES = 3
MAX_REMINDER_LINES = 10
MAX_BOOK_TITLE_LENGTH = 160
MAX_REASON_LENGTH = 500

MONTH_NAMES = (
    "Januari",
    "Februari",
    "Maret",
    "April",
    "Mei",
    "Juni",
    "Juli",
    "Agustus",
    "September",
    "Oktober",
    "November",
    "Desember",
)

FOOTER = "<i>SmartLib · Perpustakaan Digital</i>"

REMINDER_HEADINGS = {
    "H3": "Jatuh tempo H-3",
    "H1": "Jatuh tempo besok",
    "OVERDUE": "Peminjaman terlambat",
}

REMINDER_INTROS = {
    "H3": "buku berikut akan jatuh tempo dalam 3 hari.",
    "H1": "buku berikut akan jatuh tempo besok.",
    "OVERDUE": "buku berikut sudah melewati jatuh tempo.",
}

REMINDER_ACTIONS = {
    "H3": "Kembalikan buku tepat waktu untuk menghindari denda.",
    "H1": "Kembalikan buku besok untuk menghindari denda.",
    "OVERDUE": "Kembalikan buku segera. Denda mengikuti aturan perpustakaan.",
}


@dataclass(frozen=True)
class TelegramMessage:
    title: str
    message: str


def is_formatted_title(title):
    value = (title or "").strip()
    return value.startswith(FORMATTED_TITLE_PREFIXES) and "</b>" in value


def format_date(value, *, include_time=False):
    if value is None:
        return "-"
    if isinstance(value, datetime.datetime):
        if timezone.is_naive(value):
            value = timezone.make_aware(value)
        value = timezone.localtime(value)
    text = f"{value.day} {MONTH_NAMES[value.month - 1]} {value.year}"
    if include_time and isinstance(value, datetime.datetime):
        text = f"{text} pukul {value:%H:%M}"
    return text


def format_rupiah(amount):
    return f"Rp{int(amount or 0):,}".replace(",", ".")


def _escape(value):
    return html.escape("" if value is None else str(value), quote=True)


def _truncate(value, limit):
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _title(prefix, heading):
    return f"{prefix} <b>SMARTLIB · {_escape(heading)}</b>"


def _user_name(user):
    name = _truncate(getattr(user, "full_name", "") or "", 80)
    return _escape(name) if name else "Pemustaka"


def _book_title(item):
    book = getattr(getattr(item, "book_copy", None), "book", None)
    return _truncate(
        getattr(book, "title", "") or "Tanpa judul",
        MAX_BOOK_TITLE_LENGTH,
    )


def _copy_code(item):
    copy = getattr(item, "book_copy", None)
    return _truncate(getattr(copy, "copy_code", "") or "-", 40)


def _book_reference(item):
    return (
        f"<b>{_escape(_book_title(item))}</b> "
        f"(<code>{_escape(_copy_code(item))}</code>)"
    )


def _item_line(item):
    return f"• {_book_reference(item)}"


def _item_lines(items, limit=MAX_TITLES):
    items = list(items or [])
    lines = [_item_line(item) for item in items[:limit]]
    remaining = len(items) - limit
    if remaining > 0:
        lines.append(f"• dan {remaining} buku lainnya")
    return lines


def _join(*sections):
    return "\n\n".join(section for section in sections if section)


def format_loan_approved(loan, items=None):
    items = list(items or [])
    parts = [
        f"Halo <b>{_user_name(loan.user)}</b>, pengajuan peminjaman Anda "
        "sudah disetujui."
    ]
    summary = [
        "<b>Ringkasan</b>",
        f"• Nomor pengajuan: <code>#{_escape(loan.pk)}</code>",
    ]
    if items:
        summary.append(f"• Jumlah buku: {len(items)}")
        summary.extend(_item_lines(items))
    summary.append("• Status: <b>Disetujui</b>")
    parts.append("\n".join(summary))

    action = [
        "<b>Langkah berikutnya</b>",
        "Tunjukkan QR Pickup saat mengambil buku di perpustakaan.",
    ]
    if getattr(loan, "pickup_exp_at", None):
        action.append(
            "QR berlaku sampai "
            f"{format_date(loan.pickup_exp_at, include_time=True)}."
        )
    parts.append("\n".join(action))
    parts.append(FOOTER)
    return TelegramMessage(
        _title("📚", "Pengajuan disetujui"),
        _join(*parts),
    )


def format_loan_rejected(loan, reason):
    parts = [
        f"Halo <b>{_user_name(loan.user)}</b>, pengajuan peminjaman Anda "
        "belum dapat disetujui."
    ]
    summary = [
        "<b>Ringkasan</b>",
        f"• Nomor pengajuan: <code>#{_escape(loan.pk)}</code>",
        "• Status: <b>Ditolak</b>",
    ]
    parts.append("\n".join(summary))
    reason_text = _truncate(reason, MAX_REASON_LENGTH) or "Tidak disebutkan."
    parts.append(f"<b>Alasan</b>\n{_escape(reason_text)}")
    parts.append(FOOTER)
    return TelegramMessage(
        _title("📚", "Pengajuan ditolak"),
        _join(*parts),
    )


def format_loan_active(loan, items=None):
    items = list(items or [])
    parts = [
        f"Halo <b>{_user_name(loan.user)}</b>, peminjaman Anda sudah aktif."
    ]
    summary = [
        "<b>Ringkasan</b>",
        f"• Nomor pengajuan: <code>#{_escape(loan.pk)}</code>",
    ]
    if getattr(loan, "due_at", None):
        summary.append(
            "• Jatuh tempo: "
            f"<b>{format_date(loan.due_at, include_time=True)}</b>"
        )
    if items:
        summary.append(f"• Jumlah buku: {len(items)}")
        summary.extend(_item_lines(items))
    summary.append("• Status: <b>Aktif</b>")
    parts.append("\n".join(summary))
    parts.append(
        "<b>Yang perlu dilakukan</b>\n"
        "Kembalikan buku sebelum tanggal jatuh tempo."
    )
    parts.append(FOOTER)
    return TelegramMessage(
        _title("📚", "Peminjaman aktif"),
        _join(*parts),
    )


def format_loan_returned(loan, item, *, lost=False):
    parts = []
    summary = [
        "<b>Ringkasan</b>",
        f"• Nomor pengajuan: <code>#{_escape(loan.pk)}</code>",
        f"• Buku: {_book_reference(item)}",
    ]

    if lost:
        parts.append(
            f"Halo <b>{_user_name(loan.user)}</b>, laporan kehilangan "
            "eksemplar sudah dicatat."
        )
        summary.append("• Status: <b>Hilang</b>")
        replacement = (
            getattr(item, "replacement_status", "") or "BELUM_DIGANTI"
        )
        replacement_label = (
            "Belum diganti" if replacement == "BELUM_DIGANTI" else replacement
        )
        summary.append(
            f"• Penggantian: <b>{_escape(replacement_label)}</b>"
        )
        parts.append("\n".join(summary))
        parts.append(
            "<b>Yang perlu dilakukan</b>\n"
            "Hubungi pustakawan untuk proses penggantian eksemplar."
        )
        parts.append(FOOTER)
        return TelegramMessage(
            _title("📚", "Buku hilang tercatat"),
            _join(*parts),
        )

    parts.append(
        f"Halo <b>{_user_name(loan.user)}</b>, pengembalian buku sudah "
        "dicatat."
    )
    summary.append("• Status: <b>Dikembalikan</b>")
    if getattr(item, "returned_at", None):
        summary.append(
            "• Tanggal kembali: "
            f"{format_date(item.returned_at, include_time=True)}"
        )
    late_day = max(int(getattr(item, "late_day", 0) or 0), 0)
    if late_day > 0:
        summary.append(f"• Keterlambatan: {late_day} hari")
    fine_amount = getattr(item, "fine_amount", 0) or 0
    if fine_amount > 0:
        fine_status = getattr(item, "fine_status", "")
        fine_label = "Lunas" if fine_status == "LUNAS" else "Belum dibayar"
        summary.append(
            f"• Denda: <b>{format_rupiah(fine_amount)}</b> ({fine_label})"
        )
    parts.append("\n".join(summary))
    if fine_amount > 0:
        parts.append(
            "<b>Yang perlu dilakukan</b>\n"
            "Lunasi denda di perpustakaan."
        )
    parts.append(FOOTER)
    return TelegramMessage(
        _title("📚", "Buku dikembalikan"),
        _join(*parts),
    )


def format_ebook_approved(loan):
    parts = [
        f"Halo <b>{_user_name(loan.user)}</b>, akses e-book Anda sudah aktif."
    ]
    summary = [
        "<b>Ringkasan</b>",
        f"• Judul: <b>{_escape(_truncate(loan.ebook.title, 180))}</b>",
        "• Status: <b>Aktif</b>",
    ]
    if getattr(loan, "expires_at", None):
        summary.append(
            "• Berlaku sampai: "
            f"<b>{format_date(loan.expires_at, include_time=True)}</b>"
        )
    parts.append("\n".join(summary))
    parts.append(
        "<b>Langkah berikutnya</b>\n"
        "Buka halaman e-book di SmartLib untuk mulai membaca."
    )
    parts.append(FOOTER)
    return TelegramMessage(
        _title("📚", "E-book siap dibaca"),
        _join(*parts),
    )


def format_ebook_rejected(loan, reason):
    parts = [
        f"Halo <b>{_user_name(loan.user)}</b>, pengajuan e-book Anda belum "
        "dapat disetujui."
    ]
    summary = [
        "<b>Ringkasan</b>",
        f"• Judul: <b>{_escape(_truncate(loan.ebook.title, 180))}</b>",
        "• Status: <b>Ditolak</b>",
    ]
    parts.append("\n".join(summary))
    reason_text = _truncate(reason, MAX_REASON_LENGTH) or "Tidak disebutkan."
    parts.append(f"<b>Alasan</b>\n{_escape(reason_text)}")
    parts.append(FOOTER)
    return TelegramMessage(
        _title("📚", "E-book ditolak"),
        _join(*parts),
    )


def format_reminder(reminder_type, user, entries):
    entries = list(entries or [])
    parts = [
        f"Halo <b>{_user_name(user)}</b>, "
        f"{REMINDER_INTROS[reminder_type]}"
    ]
    lines = ["<b>Ringkasan</b>"]
    for item, due_date, days_left in entries[:MAX_REMINDER_LINES]:
        line = (
            f"• <b>{_escape(_book_title(item))}</b> "
            f"(<code>{_escape(_copy_code(item))}</code>) · "
            f"jatuh tempo {format_date(due_date)}"
        )
        if reminder_type == "OVERDUE":
            line = f"{line} · terlambat {abs(days_left)} hari"
        lines.append(line)
    remaining = len(entries) - MAX_REMINDER_LINES
    if remaining > 0:
        lines.append(f"• dan {remaining} buku lainnya")
    parts.append("\n".join(lines))
    parts.append(
        f"<b>Yang perlu dilakukan</b>\n{REMINDER_ACTIONS[reminder_type]}"
    )
    parts.append(FOOTER)
    return TelegramMessage(
        _title("⏰", REMINDER_HEADINGS[reminder_type]),
        _join(*parts),
    )
