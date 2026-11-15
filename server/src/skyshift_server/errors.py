"""Errors the API turns into the contract's error JSON (contracts/api.md, Conventions):
HTTP 4xx/5xx with {"error": {"code": "bad_request" | "not_found" | "upstream" | "internal", "message"}}.
API keys (apikeys.py, v3.14) add "unauthorized" (401) and "rate_limited" (429).

The engine (search, cutouts, gaia, known, ...) may raise these:
- NotFoundError: nothing to show (e.g. no SPHEREx coverage at a position)  -> 404 not_found
- UpstreamError: IRSA, Gaia, S3 or the known-object index failed            -> 502 upstream
Network exceptions from httpx, requests, pyvo and astroquery are also mapped to upstream by the app.
"""

from __future__ import annotations


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


class NotFoundError(ApiError):
    def __init__(self, message: str = "not found") -> None:
        super().__init__(404, "not_found", message)


class UpstreamError(ApiError):
    def __init__(self, message: str = "an upstream service failed") -> None:
        super().__init__(502, "upstream", message)


def bad_request(message: str) -> ApiError:
    return ApiError(400, "bad_request", message)
