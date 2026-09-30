"""Bound HTTP request bodies before the multipart parser can spool unlimited data."""

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class UploadLimitMiddleware:
    """Limit both Content-Length and chunked bodies, allowing multipart overhead."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/api/audio":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = self.max_bytes + 1
        if length > self.max_bytes or length < 0:
            response = JSONResponse(
                {
                    "code": "audio_too_large",
                    "message": "La carga supera el tamaño máximo permitido.",
                },
                status_code=413,
            )
            await response(scope, receive, send)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "code": "audio_too_large",
                            "message": "La carga supera el tamaño máximo permitido.",
                        },
                    )
            return message

        await self.app(scope, limited_receive, send)
