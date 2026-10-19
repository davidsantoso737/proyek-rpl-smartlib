"""Presentasi halaman rekomendasi (FR-78 - FR-81).

Dua tugas, keduanya murni tampilan:

* ``format_narration`` mengubah teks bebas dari provider AI menjadi blok
  terstruktur yang aman dirender template. Keluarannya hanya teks biasa
  (tanpa HTML) sehingga escaping bawaan Django tetap berlaku dan penanda
  Markdown ringan seperti ``**tebal**`` tidak lagi tampil sebagai karakter
  mentah di antarmuka.
* ``attach_covers`` melampirkan ``cover_url`` dari katalog agar setiap
  rekomendasi menampilkan sampulnya sendiri.

Prompt, model, algoritma, urutan, dan isi rekomendasi tidak disentuh:
modul ini hanya membaca dan menata tampilan.
"""

import re

from apps.catalog.models import Book, Ebook

# --- Penanda blok (baris) ---
_BULLET_RE = re.compile(r"^\s{0,3}(?:[-*+\u2022]|\d{1,2}[.)])\s+(?P<text>.+)$")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(?P<text>.+?)\s*#*\s*$")
_HR_RE = re.compile(r"^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$")
_QUOTE_RE = re.compile(r"^\s{0,3}>\s?(?P<text>.*)$")

# --- Penanda inline (dicoba berurutan dari kiri) ---
_INLINE_RE = re.compile(
    r"!\[[^\]]*\]\([^)]*\)"                          # gambar -> dibuang
    r"|\[(?P<link>[^\]]+)\]\([^)]*\)"                # tautan -> label saja
    r"|`(?P<code>[^`]+)`"                            # kode -> teks biasa
    r"|\*\*(?P<bold_a>.+?)\*\*"                      # **tebal**
    r"|(?<!\w)__(?P<bold_b>.+?)__(?!\w)"             # __tebal__
    r"|(?<!\*)\*(?!\s)(?P<em_a>[^*\n]+?)(?<!\s)\*(?!\*)"  # *miring*
    r"|(?<!\w)_(?P<em_b>[^_\n]+?)_(?!\w)"            # _miring_
)


def _push(runs, text, strong):
    """Tambahkan potongan teks, gabungkan dengan potongan sebelahnya."""
    if not text:
        return
    if runs and runs[-1]["strong"] == strong:
        runs[-1]["text"] += text
    else:
        runs.append({"text": text, "strong": strong})


def _has_content(runs):
    return any(run["text"].strip() for run in runs)


def _parse_inline(text):
    """Ubah satu baris menjadi daftar potongan {text, strong}.

    Hanya teks biasa yang dikembalikan; tautan disisakan labelnya dan
    gambar dibuang karena respons AI tidak boleh menentukan gambar.
    """
    runs = []
    pos = 0
    for match in _INLINE_RE.finditer(text):
        if match.start() > pos:
            _push(runs, text[pos:match.start()], False)
        if match.group("link") is not None:
            _push(runs, match.group("link"), False)
        elif match.group("code") is not None:
            _push(runs, match.group("code"), False)
        elif match.group("bold_a") is not None:
            _push(runs, match.group("bold_a"), True)
        elif match.group("bold_b") is not None:
            _push(runs, match.group("bold_b"), True)
        elif match.group("em_a") is not None:
            _push(runs, match.group("em_a"), False)
        elif match.group("em_b") is not None:
            _push(runs, match.group("em_b"), False)
        # blok gambar: sengaja tidak menghasilkan teks apa pun
        pos = match.end()
    if pos < len(text):
        _push(runs, text[pos:], False)
    return runs


def _parse_blocks(text):
    """Pecah teks menjadi blok paragraf/butir/judul/kutipan.

    Baris yang berurutan digabung jadi satu paragraf dengan baris baru
    yang dipertahankan (tanpa mengubah isi teks). Baris kosong memisahkan
    bagian, sesuai struktur yang memang ada pada respons.
    """
    blocks = []
    paragraph = []
    bullets = []

    def flush_paragraph():
        if not paragraph:
            return
        runs = []
        for index, line in enumerate(paragraph):
            if index:
                _push(runs, "\n", False)
            for run in _parse_inline(line):
                _push(runs, run["text"], run["strong"])
        paragraph.clear()
        if _has_content(runs):
            blocks.append({"type": "paragraph", "runs": runs})

    def flush_bullets():
        if not bullets:
            return
        items = []
        for line in bullets:
            runs = _parse_inline(line)
            if _has_content(runs):
                items.append(runs)
        bullets.clear()
        if items:
            blocks.append({"type": "list", "items": items})

    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            flush_bullets()
            continue
        if _HR_RE.match(line):
            flush_paragraph()
            flush_bullets()
            continue
        heading = _HEADING_RE.match(line)
        quote = _QUOTE_RE.match(line)
        if heading or quote:
            flush_paragraph()
            flush_bullets()
            match = heading or quote
            runs = _parse_inline(match.group("text").strip())
            if _has_content(runs):
                kind = "heading" if heading else "quote"
                blocks.append({"type": kind, "runs": runs})
            continue
        match = _BULLET_RE.match(line)
        if match:
            flush_paragraph()
            bullets.append(match.group("text").strip())
            continue
        flush_bullets()
        paragraph.append(line)

    flush_paragraph()
    flush_bullets()
    return blocks


def format_narration(text):
    """Ubah teks AI menjadi blok aman untuk template.

    Mengembalikan daftar blok: ``{"type": "paragraph" | "heading" |
    "quote", "runs": [...]}`` atau ``{"type": "list", "items": [[run, ...]]}``.
    Teks kosong atau ``None`` -> daftar kosong. Bila pemecahan gagal,
    teks tetap dikembalikan apa adanya sebagai satu paragraf.
    """
    if text is None or not str(text).strip():
        return []
    plain = str(text)
    try:
        return _parse_blocks(plain)
    except Exception:
        # Fallback aman: jangan hilangkan teks hanya karena penataan gagal.
        return [{"type": "paragraph", "runs": [{"text": plain, "strong": False}]}]


def attach_covers(items):
    """Lampirkan ``cover_url`` katalog ke setiap item rekomendasi.

    Membaca saja: judul, urutan, dan alasan tidak disentuh. Kunci lookup
    memakai ``(kind, id)`` supaya id Book dan Ebook yang beririsan tidak
    saling tertukar.
    """
    book_ids = [i.get("id") for i in items if i.get("kind") == "book"]
    ebook_ids = [i.get("id") for i in items if i.get("kind") == "ebook"]
    covers = {}
    if book_ids:
        covers.update(
            (("book", row["id"]), row["cover_url"])
            for row in Book.objects.filter(pk__in=book_ids).values(
                "id", "cover_url"
            )
        )
    if ebook_ids:
        covers.update(
            (("ebook", row["id"]), row["cover_url"])
            for row in Ebook.objects.filter(pk__in=ebook_ids).values(
                "id", "cover_url"
            )
        )
    for item in items:
        item["cover_url"] = covers.get((item.get("kind"), item.get("id")))
    return items
