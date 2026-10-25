import init, { BrowserPeer } from './pkg/browser_iroh_probe.js';

const wasm = await init();
const element = (id) => document.getElementById(id);
let peer;
let remote;
window.probe = {
  memory_bytes() { return wasm.memory.buffer.byteLength; },
  async start() {
    if (peer) throw new Error('Close the existing peer first');
    peer = await BrowserPeer.start();
    const address = await peer.address();
    element('address').value = address;
    element('status').textContent = `Online: ${peer.id()}`;
    element('start').disabled = true;
    element('close').disabled = false;
    element('select').disabled = false;
    return { id: peer.id(), address };
  },
  select(address) {
    const decoded = JSON.parse(address);
    peer.select_peer(decoded.id);
    remote = address;
    element('send').disabled = false;
  },
  async send(text) {
    const receipt = JSON.parse(await peer.send(remote, text));
    element('status').textContent = `Verified ${receipt.bytes} bytes from ${receipt.peer_id}`;
    return receipt;
  },
  events() { return JSON.parse(peer.take_events()); },
  async close() {
    if (!peer) return;
    await peer.close();
    peer.free();
    peer = undefined;
    remote = undefined;
    element('status').textContent = 'Closed';
    element('start').disabled = false;
    for (const id of ['close', 'select', 'send']) element(id).disabled = true;
  },
};
for (const [id, action] of [
  ['start', () => window.probe.start()],
  ['close', () => window.probe.close()],
  ['select', () => window.probe.select(element('remote').value)],
  ['send', () => window.probe.send(element('message').value)],
]) {
  element(id).addEventListener('click', async () => {
    try { await action(); }
    catch (error) { element('status').textContent = error.message; }
  });
}
