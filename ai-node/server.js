'use strict';

/* SmartLib AI provider-call adapter (Node.js).
 *
 * Owns exactly one job: forwarding a Django-built prompt to the
 * OpenAI-compatible provider over HTTPS and returning the answer text.
 * Django stays authoritative for auth, classification, retrieval, context,
 * system prompt, and fallback. This service has no database access, no
 * business logic, and no knowledge of SmartLib rules beyond the payload
 * it receives. Payload semantics mirror
 * apps/ai/providers.py::OpenAICompatibleProvider.
 */

const http = require('node:http');
const crypto = require('node:crypto');

const SECRET_HEADER = 'x-ai-node-secret';

function readConfig(env) {
  return {
    port: Number.parseInt(env.AI_NODE_PORT || '3101', 10),
    sharedSecret: env.AI_NODE_SHARED_SECRET || '',
    providerBaseUrl: (env.AI_PROVIDER_BASE_URL || '').replace(/\/+$/, ''),
    providerApiKey: env.AI_API_KEY || '',
    providerModel: env.AI_MODEL || '',
    timeoutSeconds: Number.parseFloat(env.AI_NODE_TIMEOUT_SECONDS || '15'),
    maxBodyBytes: Number.parseInt(env.AI_NODE_MAX_BODY_BYTES || '262144', 10),
  };
}

function sendJson(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(payload),
  });
  res.end(payload);
}

function secretsEqual(received, expected) {
  if (!received || !expected) {
    return false;
  }
  const a = Buffer.from(String(received), 'utf8');
  const b = Buffer.from(String(expected), 'utf8');
  if (a.length !== b.length) {
    return false;
  }
  return crypto.timingSafeEqual(a, b);
}

function readBody(req, maxBytes) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > maxBytes) {
        reject(Object.assign(new Error('body too large'), { status: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

function extractText(providerBody) {
  const text = providerBody?.choices?.[0]?.message?.content;
  if (typeof text !== 'string') {
    return null;
  }
  const trimmed = text.trim();
  return trimmed === '' ? null : trimmed;
}

async function callProvider(config, { systemPrompt, userText, context }) {
  const payload = {
    model: config.providerModel,
    messages: [
      { role: 'system', content: systemPrompt },
      {
        role: 'user',
        content:
          'Konteks sistem (faktual, jangan dikarang di luar ini):\n' +
          `${JSON.stringify(context)}\n\nPertanyaan pengguna:\n${userText}`,
      },
    ],
    temperature: 0.2,
  };
  let upstream;
  try {
    upstream = await fetch(`${config.providerBaseUrl}/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${config.providerApiKey}`,
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(config.timeoutSeconds * 1000),
    });
  } catch (err) {
    if (err && err.name === 'TimeoutError') {
      return { status: 504, body: { error: 'timeout' } };
    }
    return { status: 502, body: { error: 'unavailable' } };
  }
  if (upstream.status === 429) {
    return { status: 429, body: { error: 'rate_limited' } };
  }
  if (!upstream.ok) {
    return { status: 502, body: { error: 'unavailable' } };
  }
  let data;
  try {
    data = await upstream.json();
  } catch {
    return { status: 502, body: { error: 'malformed' } };
  }
  const text = extractText(data);
  if (text === null) {
    return { status: 502, body: { error: 'malformed' } };
  }
  return { status: 200, body: { text } };
}

function createServer(config) {
  return http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url || '/', 'http://localhost');
      if (req.method === 'GET' && url.pathname === '/healthz') {
        sendJson(res, 200, { status: 'ok' });
        return;
      }
      if (url.pathname !== '/api/complete') {
        sendJson(res, 404, { error: 'not_found' });
        return;
      }
      if (req.method !== 'POST') {
        sendJson(res, 405, { error: 'method_not_allowed' });
        return;
      }
      if (!secretsEqual(req.headers[SECRET_HEADER], config.sharedSecret)) {
        sendJson(res, 401, { error: 'unauthorized' });
        return;
      }
      if (
        !config.providerBaseUrl ||
        !config.providerApiKey ||
        !config.providerModel
      ) {
        sendJson(res, 502, { error: 'unavailable' });
        return;
      }
      let raw;
      try {
        raw = await readBody(req, config.maxBodyBytes);
      } catch (err) {
        sendJson(res, err.status || 400, { error: 'bad_request' });
        return;
      }
      let parsed;
      try {
        parsed = JSON.parse(raw);
      } catch {
        sendJson(res, 400, { error: 'bad_request' });
        return;
      }
      const { systemPrompt, userText, context } = parsed || {};
      if (
        typeof systemPrompt !== 'string' ||
        typeof userText !== 'string' ||
        typeof context !== 'object' ||
        context === null
      ) {
        sendJson(res, 400, { error: 'bad_request' });
        return;
      }
      const result = await callProvider(config, {
        systemPrompt,
        userText,
        context,
      });
      sendJson(res, result.status, result.body);
    } catch {
      sendJson(res, 502, { error: 'unavailable' });
    }
  });
}

if (require.main === module) {
  const config = readConfig(process.env);
  if (!config.sharedSecret) {
    console.error('AI_NODE_SHARED_SECRET is required.');
    process.exit(1);
  }
  createServer(config).listen(config.port, '127.0.0.1', () => {
    console.log(`smartlib-ai-node listening on 127.0.0.1:${config.port}`);
  });
}

module.exports = { createServer, readConfig, extractText };
