'use strict';

// The canvas keeps the same snapshots, display events and command contract in
// both hosts. Only this file knows about QWebChannel or WebView2 messaging.
window.ToolkitCanvasBridge = (() => {
  const protocol = 'toolkit.canvas';
  const version = 1;
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  let attached = false;

  function connect({changed, display, connected}) {
    if (attached) throw new Error('The canvas host is already attached.');
    attached = true;
    const webview = window.chrome?.webview;
    if (webview && typeof webview.postMessage === 'function' && typeof webview.addEventListener === 'function') {
      const receive = event => {
        const message = event.data;
        if (!object(message) || message.protocol !== protocol || message.version !== version || !object(message.value)) return;
        const value = message.value;
        if (message.type === 'snapshot' && object(value.space) && typeof value.space.id === 'string'
            && Array.isArray(value.items) && Array.isArray(value.placements) && Array.isArray(value.spaces)) {
          changed(JSON.stringify(value));
        } else if (message.type === 'display' && typeof value.panel === 'string' && value.panel.length <= 40) {
          display(JSON.stringify(value));
        }
      };
      webview.addEventListener('message', receive);
      window.addEventListener('pagehide', () => webview.removeEventListener('message', receive), {once:true});
      connected(Object.freeze({
        command(text) {
          if (typeof text !== 'string' || text.length > 300000) return;
          let value;
          try { value = JSON.parse(text); } catch { return; }
          if (!object(value) || typeof value.kind !== 'string' || !value.kind || value.kind.length > 40) return;
          webview.postMessage({protocol, version, type:'command', value});
        },
        ready(surface) {
          if (surface === 'canvas' || surface === 'bar') webview.postMessage({protocol, version, type:'ready', surface});
        }
      }));
      return;
    }
    if (!window.qt?.webChannelTransport) return;
    const attachQt = () => {
      new window.QWebChannel(window.qt.webChannelTransport, channel => {
        const canvas = channel.objects.canvas;
        canvas.changed.connect(changed);
        canvas.display.connect(display);
        connected(canvas);
      });
    };
    if (window.QWebChannel) { attachQt(); return; }
    const script = document.createElement('script');
    script.src = 'qrc:///qtwebchannel/qwebchannel.js';
    script.onload = attachQt;
    script.onerror = () => window.dispatchEvent(new Event('canvas-host-error'));
    document.head.append(script);
  }
  return Object.freeze({connect});
})();
