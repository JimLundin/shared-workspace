import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { createServer as createHttpServer } from 'node:http';
import { createServer as createHttpsServer } from 'node:https';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';

const run = promisify(execFile);
export const syntheticCredential = 'synthetic-rendered-meeting-proof-never-valid-at-provider';
export const desiredOutcome = 'Prepare cited notes, actions and an unsent follow-up.';
export const transcriptQuote = 'Mira will share the review brief next Friday.';

function generatedResponse(input) {
  assert.equal(input.store, false);
  const captured = JSON.parse(input.input[0].content[0].text);
  assert.equal(captured.sources.length, 3);
  assert.equal(captured.outcome, desiredOutcome);
  assert.equal(new Set(captured.sources.map(item => item.source.resource_id)).size, 3);
  for (const item of captured.sources) {
    for (const field of ['workspace_id', 'resource_id', 'revision_id']) {
      assert.equal(typeof item.source[field], 'string');
      assert(item.source[field].length > 0);
    }
  }
  const source = captured.sources.find(item => item.text.includes(transcriptQuote));
  assert(source);
  const citation = { source: source.source, quote: transcriptQuote };
  const basis = { type: 'transcript', citations: [citation] };
  const material = {
    notes: { title: 'Prepared meeting notes', body: transcriptQuote, citations: [citation] },
    actions: [{ text: 'Share the review brief', owner: { type: 'assigned', name: 'Mira', basis }, deadline: { type: 'stated', text: 'next Friday', basis }, basis }],
    follow_up: { title: 'Unsent review follow-up', body: 'For review: ' + transcriptQuote, citations: [citation] },
  };
  return {
    status: 'completed', error: null, incomplete_details: null,
    output: [{ type: 'message', role: 'assistant', status: 'completed', content: [{ type: 'output_text', text: JSON.stringify(material) }] }],
  };
}

/** Local TLS endpoint; captures worker requests that bypass Playwright routes and never forwards them. */
export async function controlledMeetingProvider(origin) {
  const app = new URL(origin);
  assert.equal(app.protocol, 'http:');
  assert.equal(app.hostname, '127.0.0.1');
  assert.equal(app.origin, origin);
  const certificates = await mkdtemp(join(tmpdir(), 'workspace-provider-cert-'));
  const stats = {
    type: 'isolated-https-provider-fixture', requests: 0, accepted_requests: 0,
    rejected_requests: 0, forwarded: 0, preflights: 0, unexpected_requests: 0,
    connections: 0, tls_errors: 0, captured_source_count: 0,
    synthetic_credential: false, fixture_certificates_removed: false,
  };
  const sockets = new Set();
  const track = socket => { sockets.add(socket); socket.once('close', () => sockets.delete(socket)); };
  let secure, proxy;
  async function close() {
    for (const socket of sockets) socket.destroy();
    if (proxy?.listening) await new Promise((resolve, reject) => proxy.close(error => error ? reject(error) : resolve()));
    await rm(certificates, { recursive: true, force: true });
    stats.fixture_certificates_removed = true;
  }
  try {
    await run('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes',
      '-keyout', join(certificates, 'key.pem'), '-out', join(certificates, 'cert.pem'),
      '-days', '1', '-subj', '/CN=api.openai.com', '-addext', 'subjectAltName=DNS:api.openai.com'],
    { timeout: 15000, maxBuffer: 65536 });
    secure = createHttpsServer({ key: await readFile(join(certificates, 'key.pem')), cert: await readFile(join(certificates, 'cert.pem')) }, async (request, response) => {
      response.setHeader('Access-Control-Allow-Origin', origin);
      response.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
      response.setHeader('Access-Control-Allow-Headers', 'authorization, content-type');
      response.setHeader('Content-Type', 'application/json');
      if (request.method === 'OPTIONS' && request.url === '/v1/responses') {
        stats.preflights++;
        request.resume();
        response.writeHead(204).end();
        return;
      }
      stats.requests++;
      let stage = 'endpoint';
      try {
        assert.equal(request.method, 'POST');
        assert.equal(request.url, '/v1/responses');
        stage = 'synthetic-credential';
        assert.equal(request.headers.authorization, 'Bearer ' + syntheticCredential);
        stage = 'bounded-body';
        const chunks = [];
        let size = 0;
        for await (const chunk of request) {
          size += chunk.length;
          assert(size <= 1024 * 1024);
          chunks.push(chunk);
        }
        stage = 'captured-input';
        const body = generatedResponse(JSON.parse(Buffer.concat(chunks).toString('utf8')));
        stats.captured_source_count = 3;
        stats.synthetic_credential = true;
        stats.accepted_requests++;
        response.end(JSON.stringify(body));
      } catch {
        stats.rejected_requests++;
        // Only a fixed stage is retained: assertions can contain keys or request bodies.
        stats.fixture_error = stage;
        response.writeHead(400).end(JSON.stringify({ error: 'Controlled provider fixture rejected the request.' }));
      }
    });
    secure.requestTimeout = 10000;
    secure.headersTimeout = 10000;
    secure.on('connection', track);
    secure.on('secureConnection', track);
    secure.on('tlsClientError', () => { stats.tls_errors++; });
    proxy = createHttpServer((request, response) => {
      stats.unexpected_requests++;
      request.resume();
      response.writeHead(502).end();
    });
    proxy.on('connection', track);
    proxy.on('connect', (request, socket, head) => {
      if (request.url !== 'api.openai.com:443') {
        stats.unexpected_requests++;
        socket.end('HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n');
        return;
      }
      stats.connections++;
      socket.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      if (head.length) socket.unshift(head);
      secure.emit('connection', socket);
    });
    await new Promise((resolve, reject) => {
      proxy.once('error', reject);
      proxy.listen(0, '127.0.0.1', resolve);
    });
    return { stats, server: 'http://127.0.0.1:' + proxy.address().port, close };
  } catch (error) {
    await close();
    throw error;
  }
}
