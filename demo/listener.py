"""Prints anything POSTed to :9999. If it prints the canary, the injection worked."""
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode(errors="replace")
        print(f"\n[LEAK RECEIVED] {body!r}\n", flush=True)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


print("listening on 127.0.0.1:9999 (silence means nothing leaked)")
HTTPServer(("127.0.0.1", 9999), Handler).serve_forever()
