# smartlib-ai-node

Adapter pemanggilan provider AI (Node.js, tanpa dependensi, Node >= 18).

## Peran

Satu tugas saja: meneruskan prompt yang sudah dibangun Django ke
provider kompatibel OpenAI, lalu mengembalikan teks jawaban.

- Django tetap otoritatif untuk autentikasi, klasifikasi, retrieval,
  konteks (`data_saya`), system prompt, dan fallback.
- Layanan ini tidak mengakses MySQL, tidak menyimpan data, dan tidak
  mengenal aturan bisnis SmartLib selain payload yang diterima.
- Perilaku payload dan pemetaan error paritas dengan
  `apps/ai/providers.py::OpenAICompatibleProvider`.

## Menjalankan

```bash
AI_NODE_SHARED_SECRET=<isi-secret> \
AI_PROVIDER_BASE_URL=<url-provider> \
AI_API_KEY=<kunci> \
AI_MODEL=<model> \
node ai-node/server.js
```

Lalu di Django (`.env`):

```text
AI_NODE_URL=http://127.0.0.1:3101
AI_NODE_SHARED_SECRET=<secret-yang-sama>
```

Tanpa kedua variabel itu, Django memakai jalur langsung seperti semula.

## API

- `GET /healthz` tanpa auth, untuk pemeriksaan operasional.
- `POST /api/complete` dengan header `X-AI-Node-Secret`, body
  `{systemPrompt, userText, context}`, balikan `{text}` atau
  `{error: timeout|rate_limited|unavailable|malformed|...}`.

## Tes

```bash
node --test ai-node/server.test.js
```
