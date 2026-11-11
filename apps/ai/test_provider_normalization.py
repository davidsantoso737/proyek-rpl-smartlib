"""Normalisasi respons provider lintas bentuk API.

Fokus: jawaban final diambil dari field yang benar, reasoning/tool call tidak
tercampur, dan keluaran non-percakapan (parser/JSON internal) ditolak agar
tidak pernah sampai ke chatbot atau halaman rekomendasi.
"""

import json
from unittest import mock

from django.test import SimpleTestCase

from apps.ai.providers import (
    AIProviderError,
    NodeProvider,
    OpenAICompatibleProvider,
    looks_like_natural_text,
    normalize_answer_text,
    normalize_response_text,
)


class ResponseExtractionTests(SimpleTestCase):
    def test_openai_content_string(self):
        data = {"choices": [{"message": {"content": "  Halo, saya siap.  "}}]}
        self.assertEqual(normalize_response_text(data), "Halo, saya siap.")

    def test_content_berupa_daftar_part(self):
        data = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "Halo "},
                            {"type": "text", "text": "dunia."},
                        ]
                    }
                }
            ]
        }
        self.assertEqual(normalize_response_text(data), "Halo dunia.")

    def test_gemini_candidates_parts_dan_thought_dilewati(self):
        data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"thought": True, "text": "Saya sedang berpikir..."},
                            {"text": "Jawaban untuk Anda."},
                        ]
                    }
                }
            ]
        }
        self.assertEqual(
            normalize_response_text(data), "Jawaban untuk Anda."
        )
        thought = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"thought": True, "text": "analisis internal"},
                            {"text": "Jawaban final."},
                        ]
                    }
                }
            ]
        }
        self.assertEqual(normalize_response_text(thought), "Jawaban final.")

    def test_reasoning_tidak_dipakai_jika_content_kosong(self):
        data = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning_content": "Saya menganalisis pertanyaan.",
                    }
                }
            ]
        }
        with self.assertRaises(AIProviderError) as ctx:
            normalize_response_text(data)
        self.assertEqual(ctx.exception.kind, AIProviderError.MALFORMED)

    def test_string_json_ganda_dibuka(self):
        inner = json.dumps({"answer": "Halo dari pembungkus."})
        data = {"choices": [{"message": {"content": json.dumps(inner)}}]}
        self.assertEqual(
            normalize_response_text(data), "Halo dari pembungkus."
        )

    def test_objek_json_pembungkus_jawaban_dibuka(self):
        data = {
            "choices": [
                {
                    "message": {
                        "content": '{"answer": "Rekomendasi untuk Anda."}'
                    }
                }
            ]
        }
        self.assertEqual(
            normalize_response_text(data), "Rekomendasi untuk Anda."
        )

    def test_json_internal_ditolak(self):
        data = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"is_listing": false, "personal_intent": '
                            '"profil", "data_saya": {"nama": "Budi"}}'
                        )
                    }
                }
            ]
        }
        with self.assertRaises(AIProviderError) as ctx:
            normalize_response_text(data)
        self.assertEqual(ctx.exception.kind, AIProviderError.MALFORMED)

    def test_keluaran_parser_simbol_ditolak(self):
        garbage = "\\_ 2 **_ < \\_ & & & & & & } } } } } } } }"
        with self.assertRaises(AIProviderError):
            normalize_answer_text(garbage)

    def test_boolean_tunggal_ditolak(self):
        for value in ("true", "false", "null", "None"):
            with self.subTest(value=value):
                with self.assertRaises(AIProviderError):
                    normalize_answer_text(value)

    def test_teks_sah_dengan_tanda_baca_tetap_utuh(self):
        text = "Status (PENDING_APPROVAL) tetap ditampilkan apa adanya."
        self.assertEqual(normalize_answer_text(text), text)

    def test_looks_like_natural_text(self):
        self.assertTrue(looks_like_natural_text("Halo, ada yang bisa dibantu?"))
        self.assertTrue(looks_like_natural_text("Ya."))
        self.assertTrue(looks_like_natural_text("OK"))
        self.assertFalse(looks_like_natural_text("........"))
        self.assertFalse(looks_like_natural_text("& & & & & & &"))

    def test_blok_thought_dibuang_final_dipertahankan(self):
        data = {
            "choices": [
                {
                    "message": {
                        "content": (
                            "<thought>\n"
                            "* User asks: siapa saya.\n"
                            "* data_saya.profil: {\"nama\": \"Sugeng\"}.\n"
                            "</thought>\n\n"
                            "Halo Sugeng! Kamu siswa kelas XII-A jurusan IPA."
                        )
                    }
                }
            ]
        }
        answer = normalize_response_text(data)
        self.assertEqual(
            answer,
            "Halo Sugeng! Kamu siswa kelas XII-A jurusan IPA.",
        )
        self.assertNotIn("data_saya", answer)
        self.assertNotIn("<thought>", answer)

    def test_tag_think_case_insensitive_dan_markdown_utuh(self):
        data = {
            "choices": [
                {
                    "message": {
                        "content": (
                            "<THINK>analisis internal</THINK>\n"
                            "**Halo Sugeng!**\n\n"
                            "- Kelas: XII-A\n"
                            "- Jurusan: IPA"
                        )
                    }
                }
            ]
        }
        self.assertEqual(
            normalize_response_text(data),
            "**Halo Sugeng!**\n\n- Kelas: XII-A\n- Jurusan: IPA",
        )

    def test_beberapa_blok_thought_dibuang(self):
        data = {
            "choices": [
                {
                    "message": {
                        "content": (
                            "<thought>satu</thought>"
                            "Jawaban final."
                            "<thought>dua</thought>"
                        )
                    }
                }
            ]
        }
        self.assertEqual(normalize_response_text(data), "Jawaban final.")

    def test_pembuka_tanpa_penutup_membuang_sisa_reasoning(self):
        data = {
            "choices": [
                {
                    "message": {
                        "content": (
                            "Halo Sugeng! Kamu siswa XII-A.\n"
                            "<thought>\n* Constraint check: jangan tampilkan JSON.\n"
                        )
                    }
                }
            ]
        }
        self.assertEqual(
            normalize_response_text(data),
            "Halo Sugeng! Kamu siswa XII-A.",
        )

    def test_penutup_tanpa_pembuka_membuang_awalan_reasoning(self):
        data = {
            "choices": [
                {
                    "message": {
                        "content": (
                            "Analisis internal: pakai data_saya.profil.\n"
                            "</thought>\n"
                            "Halo Sugeng! Kamu siswa XII-A."
                        )
                    }
                }
            ]
        }
        self.assertEqual(
            normalize_response_text(data),
            "Halo Sugeng! Kamu siswa XII-A.",
        )

    def test_hanya_reasoning_tanpa_final_ditolak(self):
        data = {
            "choices": [
                {
                    "message": {
                        "content": (
                            "<thought>Hanya proses berpikir tanpa jawaban."
                            "</thought>"
                        )
                    }
                }
            ]
        }
        with self.assertRaises(AIProviderError) as ctx:
            normalize_response_text(data)
        self.assertEqual(ctx.exception.kind, AIProviderError.MALFORMED)

    def test_tag_non_reasoning_tidak_dihapus(self):
        text = "Gunakan <b>tebal</b> pada Markdown bila perlu."
        self.assertEqual(normalize_answer_text(text), text)

    def test_gabungan_chunk_streaming_dinormalisasi_setelah_lengkap(self):
        # Backend saat ini tidak memakai streaming; bila chunk digabung utuh
        # sebelum normalisasi, blok reasoning tetap dibuang.
        chunks = ["<tho", "ught>analisis", " internal</thou", "ght>\nHalo!"]
        self.assertEqual(normalize_answer_text("".join(chunks)), "Halo!")


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


class ProviderAdapterTests(SimpleTestCase):
    def test_openai_complete_menormalisasi_konten(self):
        provider = OpenAICompatibleProvider("http://x", "k", "m", 5)
        with mock.patch(
            "apps.ai.providers.urllib.request.urlopen",
            return_value=FakeResponse(
                {"choices": [{"message": {"content": "  Jawaban wajar.  "}}]}
            ),
        ):
            self.assertEqual(
                provider.complete("sys", "halo", {}), "Jawaban wajar."
            )

    def test_openai_complete_menolak_keluaran_parser(self):
        provider = OpenAICompatibleProvider("http://x", "k", "m", 5)
        with mock.patch(
            "apps.ai.providers.urllib.request.urlopen",
            return_value=FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "\\_ & & & } } } } } } } }"
                            }
                        }
                    ]
                }
            ),
        ):
            with self.assertRaises(AIProviderError) as ctx:
                provider.complete("sys", "halo", {})
        self.assertEqual(ctx.exception.kind, AIProviderError.MALFORMED)

    def test_node_complete_menormalisasi_teks(self):
        provider = NodeProvider("http://127.0.0.1:3101", "secret", 5)
        with mock.patch(
            "apps.ai.providers.urllib.request.urlopen",
            return_value=FakeResponse({"text": "  Jawaban node.  "}),
        ):
            self.assertEqual(
                provider.complete("sys", "halo", {}), "Jawaban node."
            )

    def test_node_complete_menolak_keluaran_internal(self):
        provider = NodeProvider("http://127.0.0.1:3101", "secret", 5)
        with mock.patch(
            "apps.ai.providers.urllib.request.urlopen",
            return_value=FakeResponse({"text": '{"is_listing": false}'}),
        ):
            with self.assertRaises(AIProviderError) as ctx:
                provider.complete("sys", "halo", {})
        self.assertEqual(ctx.exception.kind, AIProviderError.MALFORMED)
