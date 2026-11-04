"""Request limits and structured logs without bodies, tokens or query strings."""
import json
import logging
import os
import time
import uuid
from collections import OrderedDict, deque
from threading import Lock

from starlette.responses import JSONResponse

logger = logging.getLogger('campus.operations')


class RequestLimits:
    def __init__(self):
        self.clients = OrderedDict()
        self.lock = Lock()

    def check(self, peer, bucket, limit):
        now = time.monotonic()
        with self.lock:
            key = (peer, bucket)
            events = self.clients.setdefault(key, deque())
            self.clients.move_to_end(key)
            while events and events[0] <= now-60:
                events.popleft()
            if len(events) >= limit:
                return max(1, int(61-(now-events[0])))
            events.append(now)
            while len(self.clients) > 4096:
                self.clients.popitem(last=False)
        return 0


limits = RequestLimits()


async def middleware(request, call_next):
    request_id = uuid.uuid4().hex
    start = time.monotonic()
    path = request.url.path
    bucket = ('chat' if path == '/api/chat' else 'auth' if path in ('/api/auth/login', '/api/auth/password')
              else 'admin' if path.startswith('/api/admin') or path in ('/api/auth/session', '/api/auth/logout') else None)
    if bucket and os.getenv('RATE_LIMIT_ENABLED', '1') != '0':
        default = {'chat': '30', 'auth': '10', 'admin': '120'}[bucket]
        setting = {'chat': 'CHAT_REQUESTS_PER_MINUTE', 'auth': 'AUTH_REQUESTS_PER_MINUTE', 'admin': 'ADMIN_REQUESTS_PER_MINUTE'}[bucket]
        maximum = max(1, int(os.getenv(setting, default)))
        retry = limits.check(request.client.host if request.client else 'unknown', bucket, maximum)
        if retry:
            return JSONResponse({'detail': 'Too many requests. Please wait and try again.'}, status_code=429,
                                headers={'Retry-After': str(retry), 'X-Request-ID': request_id})
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.error(json.dumps({'event': 'request_failed', 'request_id': request_id, 'path': path,
                                 'error_type': type(exc).__name__}))
        response = JSONResponse({'detail': 'The service could not complete this request.', 'request_id': request_id}, status_code=500)
    response.headers['X-Request-ID'] = request_id
    response.headers['X-Content-Type-Options'] = 'nosniff'
    if path.startswith(('/api/admin', '/api/auth')):
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['Pragma'] = 'no-cache'
        from backend import auth
        if getattr(request.state, 'clear_auth', False):
            auth.clear_cookies(response)
        elif getattr(request.state, 'auth_tokens', None):
            auth.save_cookies(response, request.state.auth_tokens)
    if response.status_code >= 500:
        logger.error(json.dumps({'event': 'service_error', 'request_id': request_id, 'path': path,
                                 'status': response.status_code, 'duration_ms': round((time.monotonic()-start)*1000)}))
    return response
