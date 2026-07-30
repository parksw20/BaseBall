"""BaseBall static server + /save/<name> capture endpoint (verification).

Usage: python serve.py [port]   (default 8131)

file://로도 게임은 돌아가지만, 서버로 열면 선수 사진·구단 로고(KBO_DB_Builder/output/)가
정상 로드되고 캔버스 캡처(/save)로 검증 스크린샷을 남길 수 있다.
"""
import base64
import http.server
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8131


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):
        if not self.path.startswith("/save/"):
            self.send_error(404)
            return
        name = Path(self.path[len("/save/"):]).name  # sanitize
        length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(length).decode("ascii")
        if "," in data:
            data = data.split(",", 1)[1]  # strip data:image/png;base64,
        out = ROOT / "captures"
        out.mkdir(exist_ok=True)
        (out / name).write_bytes(base64.b64decode(data))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")


if __name__ == "__main__":
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler) as srv:
        print(f"serving {ROOT} at http://localhost:{PORT}")
        srv.serve_forever()
