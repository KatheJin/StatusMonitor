"""Disposable HTTP fixture. Only the verifier injects/reverses faults."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        healthy = self.path == '/ready' or Path('/fixture/mode').read_text().strip() == 'healthy'
        body = json.dumps({'healthy': healthy}).encode()
        self.send_response(200 if healthy else 503)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
