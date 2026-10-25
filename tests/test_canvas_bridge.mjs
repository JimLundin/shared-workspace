import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {test} from 'node:test';

const source = await readFile(new URL('../assistant/canvas_assets/bridge.js', import.meta.url), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));

function setup(kind) {
  const sent = [], snapshots = [], displays = [], events = new Map(), removed = [], scripts = [], dispatched = [];
  let native;
  const window = {
    addEventListener(name, callback) { events.set(name, callback); },
    dispatchEvent(event) { dispatched.push(event.type); },
  };
  if (kind === 'webview2') window.chrome = {webview:{
    postMessage(message) { sent.push(plain(message)); },
    addEventListener(name, callback) { events.set('host-' + name, callback); },
    removeEventListener(name, callback) { removed.push([name, callback === events.get('host-' + name)]); },
  }};
  const installQt = () => {
    window.QWebChannel = function(transport, callback) {
      assert.equal(transport, window.qt.webChannelTransport);
      callback({objects:{canvas:{
        changed:{connect(fn) { events.set('qt-changed', fn); }},
        display:{connect(fn) { events.set('qt-display', fn); }},
        command(value) { sent.push(value); }, ready(value) { sent.push(value); },
      }}});
    };
  };
  if (kind === 'qt' || kind === 'qt-lazy') window.qt = {webChannelTransport:{}};
  if (kind === 'qt') installQt();
  const document = {
    createElement(tag) { assert.equal(tag, 'script'); return {}; },
    head:{append(script) { scripts.push(script); }},
  };
  vm.runInContext(source, vm.createContext({window, Event, document}));
  const handlers = {
    changed:value=>snapshots.push(JSON.parse(value)),
    display:value=>displays.push(JSON.parse(value)),
    connected:value=>{ native=value; },
  };
  window.ToolkitCanvasBridge.connect(handlers);
  return {window, sent, snapshots, displays, events, removed, scripts, dispatched, installQt, handlers,
    get native() { return native; }};
}

test('WebView2 subscribes before ready and preserves command payloads', () => {
  const host=setup('webview2');
  assert.ok(host.events.get('host-message'));
  assert.equal(host.scripts.length,0);
  host.native.ready('canvas');
  host.native.command(JSON.stringify({kind:'edit',id:'note-1',text:'Ångström\n日本語 🧩'}));
  assert.deepEqual(host.sent, [
    {protocol:'toolkit.canvas',version:1,type:'ready',surface:'canvas'},
    {protocol:'toolkit.canvas',version:1,type:'command',value:{kind:'edit',id:'note-1',text:'Ångström\n日本語 🧩'}},
  ]);
});

test('WebView2 delivers snapshots and display events, rejecting other messages', () => {
  const host=setup('webview2'), receive=data=>host.events.get('host-message')({data});
  const value={space:{id:'main'},spaces:[],items:[],placements:[]};
  receive({protocol:'toolkit.canvas',version:1,type:'snapshot',value});
  receive({protocol:'toolkit.canvas',version:1,type:'display',value:{panel:'focus',id:'note-1'}});
  for(const data of [null, [], 'unparsed', {}, {protocol:'other',version:1,type:'snapshot',value},
    {protocol:'toolkit.canvas',version:2,type:'snapshot',value},
    {protocol:'toolkit.canvas',version:1,type:'snapshot',value:{space:{id:'main'}}},
    {protocol:'toolkit.canvas',version:1,type:'display',value:{panel:[]}},
    {protocol:'toolkit.canvas',version:1,type:'command',value:{kind:'delete'}}]) receive(data);
  assert.deepEqual(host.snapshots,[value]);
  assert.deepEqual(host.displays,[{panel:'focus',id:'note-1'}]);
});

test('Outbound boundary rejects malformed or oversized commands and unknown surfaces', () => {
  const host=setup('webview2');
  for(const value of [null, 7, '', '{', '[]', '{}', '{"kind":1}', JSON.stringify({kind:'edit',text:'a'.repeat(300000)})]) host.native.command(value);
  host.native.ready('remote-app');
  assert.deepEqual(host.sent,[]);
});

test('Qt uses the existing QWebChannel contract unchanged', () => {
  const host=setup('qt');
  assert.equal(host.scripts.length,0);
  host.native.ready('bar');
  host.native.command('{"kind":"launcher"}');
  host.events.get('qt-changed')('{"space":{"id":"main"}}');
  host.events.get('qt-display')('{"panel":"fit"}');
  assert.deepEqual(host.sent,['bar','{"kind":"launcher"}']);
  assert.deepEqual(host.snapshots,[{space:{id:'main'}}]);
  assert.deepEqual(host.displays,[{panel:'fit'}]);
});

test('Qt loads its own channel script before attaching the canvas', () => {
  const host=setup('qt-lazy');
  assert.equal(host.native,undefined);
  assert.deepEqual(host.sent,[]);
  assert.equal(host.scripts.length,1);
  assert.equal(host.scripts[0].src,'qrc:///qtwebchannel/qwebchannel.js');
  host.installQt();
  host.scripts[0].onload();
  assert.ok(host.events.get('qt-changed'));
  assert.ok(host.events.get('qt-display'));
  host.native.ready('canvas');
  assert.deepEqual(host.sent,['canvas']);
});

test('A failed Qt script load reports failure without claiming host readiness', () => {
  const host=setup('qt-lazy');
  host.scripts[0].onerror();
  assert.equal(host.native,undefined);
  assert.deepEqual(host.sent,[]);
  assert.deepEqual(host.dispatched,['canvas-host-error']);
});

test('Standalone canvas remains usable without pretending to have a native host', () => {
  const host=setup('none');
  assert.equal(host.native,undefined);
  assert.deepEqual(host.sent,[]);
  assert.equal(host.scripts.length,0);
});

test('Duplicate attachment is rejected and page exit detaches WebView2 listener', () => {
  const host=setup('webview2');
  assert.throws(()=>host.window.ToolkitCanvasBridge.connect(host.handlers),/already attached/);
  host.events.get('pagehide')();
  assert.deepEqual(host.removed,[['message',true]]);
});
