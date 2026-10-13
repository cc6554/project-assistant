"""Launch-bound API authentication; no HTTP endpoint exposes the secret."""
import hmac
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

API_TOKEN = os.environ.get("JOB_RADAR_API_TOKEN") or secrets.token_urlsafe(48)
INSTANCE_ID = os.environ.get("JOB_RADAR_INSTANCE_ID") or secrets.token_hex(16)
ORIGINS = {"http://127.0.0.1:17689", "http://localhost:17689", "http://localhost:1420", "http://127.0.0.1:1420"}
HOSTS = {"127.0.0.1:17689", "localhost:17689", "127.0.0.1", "localhost"}


class LocalAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.headers.get("host", "").lower() not in HOSTS:
            return JSONResponse({"detail": "不受信任的 Host"}, status_code=403)
        origin = request.headers.get("origin")
        if origin is not None and origin not in ORIGINS:
            return JSONResponse({"detail": "不受信任的来源"}, status_code=403)
        if request.url.path.startswith("/api") and request.method != "OPTIONS":
            supplied = request.headers.get("x-job-radar-token", "")
            if not hmac.compare_digest(supplied.encode(), API_TOKEN.encode()):
                return JSONResponse({"detail": "请从桌面应用或后端启动链接进入"}, status_code=401)
        response = await call_next(request)
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response
