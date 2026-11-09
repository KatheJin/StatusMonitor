"""Fixed release server/router; no user-supplied upstreams."""
import json
import os
import urllib.request
import urllib.error
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        role = os.environ['DEMO_ROLE']
        if role == 'router':
            try:
                release = json.loads(Path('/control/current.json').read_text())['release']
                if release not in ('healthy', 'failing'):
                    raise ValueError('Invalid route')
                try:
                    response = urllib.request.urlopen('http://' + release + ':8080/health', timeout=2)
                except urllib.error.HTTPError as response_error:
                    response = response_error
                with response:
                    status, body = response.status, response.read(4096)
            except Exception:
                status, body = 503, b'{"release":"unknown"}'
        else:
            status = 200 if role == 'healthy' else 503
            body = json.dumps({'release': role}).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
