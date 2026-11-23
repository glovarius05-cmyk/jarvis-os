#!/usr/bin/env python3
"""Local OAuth relay daemon for Jarvis OS.
Redirects incoming localhost:7777 / 127.0.0.1:7777 OAuth callbacks to the remote VPS at 202.71.13.37:7777.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import sys
import threading
import time

VPS_HOST = "http://202.71.13.37:7777"


class OAuthRelayHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        target = f"{VPS_HOST}{self.path}"
        self.log_message("Forwarding OAuth GET %s -> %s", self.path, target)
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Перенаправление в Jarvis OS...</title>
  <meta http-equiv="refresh" content="0; url={target}">
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #09090b;
      color: #f4f4f5;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
      text-align: center;
    }}
    .card {{
      background: #18181b;
      border: 1px solid #27272a;
      border-radius: 12px;
      padding: 32px;
      max-width: 480px;
    }}
    h2 {{ color: #10b981; margin-top: 0; }}
    a {{ color: #f59e0b; text-decoration: none; font-weight: 500; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <div class="card">
    <h2>✅ Авторизация получена!</h2>
    <p>Перенаправление в Jarvis OS...</p>
    <p><a href="{target}">Нажмите здесь, если переход не произошел автоматически →</a></p>
  </div>
</body>
</html>"""
        self.wfile.write(html.encode("utf-8"))

    def do_POST(self):
        target = f"{VPS_HOST}{self.path}"
        self.log_message("Forwarding OAuth POST %s -> %s", self.path, target)
        self.send_response(307)
        self.send_header("Location", target)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def log_message(self, format, *args):
        sys.stderr.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] " + (format % args) + "\n")
        sys.stderr.flush()


def run_ipv4():
    try:
        server = HTTPServer(("0.0.0.0", 7777), OAuthRelayHandler)
        server.serve_forever()
    except Exception as exc:
        sys.stderr.write(f"IPv4 server error: {exc}\n")


def main():
    sys.stderr.write("Starting OAuth relay on port 7777 -> 202.71.13.37:7777\n")
    sys.stderr.flush()
    server = HTTPServer(("127.0.0.1", 7777), OAuthRelayHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
