'use strict';

/* Parity and failure tests for the Node provider-call adapter.
 * A stub upstream mimics the OpenAI-compatible API; no real provider,
 * no Django, and no database are involved.
 */

const assert = require('node:assert/strict');
const http = require('node:http');
const { test, before, after } = require('node:test');
const { createServer, readConfig, extractText } = require('./server');

const SECRET = 'test-shared-secret';
let upstream;
let upstreamMode = 'ok';
let nodeServer;
let nodePort;

function startUpstream() {
  return new Promise((resolve) => {
    upstream = http.createServer((req, res) => {
      let body = '';
      req.on('data', (chunk) => {
        body += chunk;
      });
      req.on('end', () => {
        if (upstreamMode === 'ok') {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(
            JSON.stringify({
              choices: [{ message: { content: '  Jawaban stub.  ' } }],
            }),
          );
        } else if (upstreamMode === 'rate_limited') {
          res.writeHead(429, { 'Content-Type': 'application/json' });
          res.end('{}');
        } else if (upstreamMode === 'broken') {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end('{"choices": []}');
        } else if (upstreamMode === 'error') {
          res.writeHead(500, { 'Content-Type': 'application/json' });
          res.end('{}');
        } else if (upstreamMode === 'slow') {
          setTimeout(() => {
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(
              JSON.stringify({
                choices: [{ message: { content: 'late' } }],
              }),
            );
          }, 3000);
        } else if (upstreamMode === 'echo') {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(
            JSON.stringify({
              choices: [{ message: { content: body } }],
            }),
          );
        }
      });
    });
    upstream.listen(0, '127.0.0.1', resolve);
  });
}

function configFor(upstreamPort, timeoutSeconds = '5') {
  return {
    ...readConfig({ ...process.env, AI_NODE_SHARED_SECRET: SECRET }),
    providerBaseUrl: `http://127.0.0.1:${upstreamPort}`,
    providerApiKey: 'stub-key',
    providerModel: 'stub-model',
    timeoutSeconds: Number.parseFloat(timeoutSeconds),
  };
}

async function complete(payload, secret = SECRET) {
  const res = await fetch(`http://127.0.0.1:${nodePort}/api/complete`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-ai-node-secret': secret,
    },
    body: JSON.stringify(payload),
  });
  return { status: res.status, body: await res.json() };
}

const question = {
  systemPrompt: 'system',
  userText: 'halo',
  context: { koleksi_fisik: [] },
};

before(async () => {
  await startUpstream();
  const upstreamPort = upstream.address().port;
  nodeServer = createServer(configFor(upstreamPort, '1'));
  await new Promise((resolve) => {
    nodeServer.listen(0, '127.0.0.1', resolve);
  });
  nodePort = nodeServer.address().port;
});

after(async () => {
  nodeServer.close();
  upstream.close();
});

test('health check needs no auth', async () => {
  const res = await fetch(`http://127.0.0.1:${nodePort}/healthz`);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { status: 'ok' });
});

test('missing or wrong secret is rejected', async () => {
  const bad = await complete(question, 'wrong');
  assert.equal(bad.status, 401);
  const res = await fetch(`http://127.0.0.1:${nodePort}/api/complete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(question),
  });
  assert.equal(res.status, 401);
});

test('success returns trimmed provider text', async () => {
  upstreamMode = 'ok';
  const res = await complete(question);
  assert.equal(res.status, 200);
  assert.equal(res.body.text, 'Jawaban stub.');
});

test('payload mirrors the Python adapter semantics', async () => {
  upstreamMode = 'echo';
  const res = await complete(question);
  assert.equal(res.status, 200);
  const sent = JSON.parse(res.body.text);
  assert.equal(sent.model, 'stub-model');
  assert.equal(sent.temperature, 0.2);
  assert.equal(sent.messages[0].role, 'system');
  assert.match(sent.messages[1].content, /Pertanyaan pengguna:\nhalo/);
});

test('rate limit maps to 429', async () => {
  upstreamMode = 'rate_limited';
  const res = await complete(question);
  assert.equal(res.status, 429);
  assert.equal(res.body.error, 'rate_limited');
});

test('upstream 500 maps to 502 unavailable', async () => {
  upstreamMode = 'error';
  const res = await complete(question);
  assert.equal(res.status, 502);
  assert.equal(res.body.error, 'unavailable');
});

test('malformed provider body maps to 502 malformed', async () => {
  upstreamMode = 'broken';
  const res = await complete(question);
  assert.equal(res.status, 502);
  assert.equal(res.body.error, 'malformed');
});

test('slow upstream maps to 504 timeout', async () => {
  upstreamMode = 'slow';
  const res = await complete(question);
  assert.equal(res.status, 504);
  assert.equal(res.body.error, 'timeout');
});

test('invalid request body is rejected', async () => {
  const res = await complete({ userText: 'halo' });
  assert.equal(res.status, 400);
});

test('extractText parity edge cases', () => {
  assert.equal(extractText({ choices: [] }), null);
  assert.equal(extractText({}), null);
  assert.equal(extractText({ choices: [{ message: {} }] }), null);
  assert.equal(
    extractText({ choices: [{ message: { content: '  x  ' } }] }),
    'x',
  );
});
