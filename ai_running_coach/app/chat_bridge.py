"""Optional token-authenticated LAN dispatcher for a desktop MCP proxy."""

from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

from .chat_runtime import ChatFailure, REMOTE_ACTIONS

REQUEST_LIMIT = 128 * 1024
RESPONSE_LIMIT = 520 * 1024


class ChatBridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 4

    def __init__(self, address, runtime):
        self.runtime = runtime
        self.requests = deque()
        self.rate_lock = threading.Lock()
        self.dispatch_lock = threading.Lock()
        super().__init__(address, ChatBridgeHandler)

    def available(self):
        now = time.monotonic()
        with self.rate_lock:
            while self.requests and self.requests[0] < now - 60:
                self.requests.popleft()
            if len(self.requests) >= 30:
                return False
            self.requests.append(now)
            return True


class ChatBridgeHandler(BaseHTTPRequestHandler):
    server_version = 'Coach chat bridge'
    sys_version = ''

    def setup(self):
        self.request.settimeout(12)
        super().setup()

    def log_message(self, *_args):
        """No paths, credentials, request content or private data in HTTP logs."""

    def respond(self, status, value):
        content = json.dumps(value, allow_nan=False).encode('utf-8')
        if len(content) > RESPONSE_LIMIT:
            status, content = 500, b'{"ok":false,"code":"unavailable"}'
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'")
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(content)
        self.close_connection = True

    def do_GET(self):
        self.respond(404, {'ok': False, 'code': 'not_found'})

    do_OPTIONS = do_GET

    def do_POST(self):
        try:
            self._post()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception:
            self.respond(500, {'ok': False, 'code': 'unavailable'})

    def _post(self):
        if self.path != '/coach-chat' or self.headers.get('Origin'):
            self.respond(404, {'ok': False, 'code': 'not_found'})
            return
        headers = self.headers.get_all('Authorization', [])
        if len(headers) != 1 or not self.server.runtime.chat_authorized(headers[0]):
            self.respond(401, {'ok': False, 'code': 'unauthorized'})
            return
        if not self.server.available():
            self.respond(429, {'ok': False, 'code': 'rate_limited'})
            return
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get('Transfer-Encoding') or len(lengths) != 1 or not lengths[0].isdigit() or len(lengths[0]) > 7:
            self.respond(400, {'ok': False, 'code': 'invalid_input'})
            return
        length = int(lengths[0])
        if length > REQUEST_LIMIT:
            self.respond(413, {'ok': False, 'code': 'invalid_input'})
            return
        if self.headers.get_content_type() != 'application/json':
            self.respond(415, {'ok': False, 'code': 'invalid_input'})
            return
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw)
            if len(raw) != length or not isinstance(value, dict) or set(value) != {'action', 'payload'}:
                raise ValueError('Invalid request')
            action, payload = value['action'], value['payload']
            if not isinstance(action, str) or action not in REMOTE_ACTIONS or not isinstance(payload, dict):
                raise ValueError('Unsupported action')
        except (ValueError, RecursionError):
            self.respond(400, {'ok': False, 'code': 'invalid_input'})
            return
        if not self.server.dispatch_lock.acquire(blocking=False):
            self.respond(409, {'ok': False, 'code': 'busy'})
            return
        try:
            result = self.server.runtime.chat_dispatch(action, payload, remote=True)
            self.respond(200, {'ok': True, 'value': result})
        except ChatFailure as error:
            self.respond(409, {'ok': False, 'code': error.code, 'message': str(error)})
        except (RuntimeError, ValueError):
            self.respond(409, {'ok': False, 'code': 'busy'})
        finally:
            self.server.dispatch_lock.release()
