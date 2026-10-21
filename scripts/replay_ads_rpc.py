"""Serve saved real Ads Transparency RPC responses on localhost, for local runs while Google blocks this IP.

Usage: python scripts/replay_ads_rpc.py [port]
Then run the Ads Actor with ADS_RPC_BASE_URL=http://127.0.0.1:<port>/ . Only the Transparency Center's RPC host is
replayed; ad previews and images are still fetched live from Google. The one change to the saved data: the page token
is removed from the creatives page, so the replay ends after that page instead of serving the same page again.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

FIX = Path(__file__).resolve().parent.parent / 'google-ads-transparency-scraper' / 'tests' / 'fixtures'
CREATIVES = json.loads((FIX / 'search_creatives_domain_nike_com.json').read_text(encoding='utf-8'))
CREATIVES.pop('2', None)
SUGGESTIONS = (FIX / 'search_suggestions_nike.json').read_bytes()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        if 'SearchCreatives' in self.path:
            body = json.dumps(CREATIVES).encode()
        elif 'SearchSuggestions' in self.path:
            body = SUGGESTIONS
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f'Replaying saved Ads RPC responses on http://127.0.0.1:{port}/', flush=True)
    HTTPServer(('127.0.0.1', port), Handler).serve_forever()
