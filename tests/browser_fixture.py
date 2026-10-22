"""A local web app for automation tests; no user accounts or external services."""

import threading
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!doctype html><meta charset="utf-8"><title>Toolkit browser fixture</title>
<label>Name <input id="name"></label>
<label>Password <input type="password" value="fixture-private-password"></label>
<label>Code <input autocomplete="one-time-code" value="fixture-private-code"></label>
<label>Choice <select id="choice"><option value="alpha">Alpha</option><option value="beta">Beta</option></select></label>
<label>Enabled <input type="checkbox" id="enabled"></label>
<button id="save">Save</button><output id="result">Waiting</output><div id="shadow"></div>
<div style="height:900px">Scroll past this space</div><iframe name="embedded" src="/frame"></iframe>
<script>
document.querySelector('#save').onclick = () => {
    const value = document.querySelector('#name').value + ':' + document.querySelector('#choice').value
        + ':' + document.querySelector('#enabled').checked;
    document.querySelector('#result').textContent = value;
    localStorage.setItem('saved', value);
};
const shadow = document.querySelector('#shadow').attachShadow({mode:'open'});
const button = document.createElement('button');
button.textContent = 'Shadow action';
button.onclick = () => document.title = 'Shadow clicked';
shadow.append(button);
</script>"""

FRAME = """<!doctype html><title>Frame</title>
<button id="frame" onclick="document.querySelector('output').textContent = 'Frame clicked'">Frame action</button>
<output>Frame waiting</output>"""


@contextmanager
def fixture() -> Generator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            data = (FRAME if self.path == "/frame" else PAGE).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Security-Policy", "require-trusted-types-for 'script'")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
