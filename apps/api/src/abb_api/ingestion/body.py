"""Reading and decoding request bodies defensively (spec §71.2, §71.4).

The size limit applies to the compressed bytes as received *and* to the decompressed bytes, and
both are enforced while streaming, so neither a lying Content-Length nor a zip bomb can make the
server buffer more than the limit.
"""

import zlib

from starlette.requests import Request

from abb_api.core.errors import AppError, ErrorCategory

_CHUNK = 64 * 1024


def too_large(limit: int, what: str) -> AppError:
    return AppError(
        "PAYLOAD_TOO_LARGE",
        f"{what} exceeds the {limit} byte limit.",
        category=ErrorCategory.VALIDATION,
        status_code=413,
        details={"limit_bytes": limit},
    )


def batch_invalid(message: str, **details: object) -> AppError:
    return AppError(
        "BATCH_INVALID",
        message,
        category=ErrorCategory.VALIDATION,
        status_code=400,
        details=dict(details),
    )


def require_json_content_type(request: Request) -> None:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise AppError(
            "UNSUPPORTED_MEDIA_TYPE",
            "Content-Type must be application/json.",
            category=ErrorCategory.VALIDATION,
            status_code=415,
        )


async def read_body(request: Request, limit: int) -> bytes:
    """The raw request body, never more than `limit` bytes."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise too_large(limit, "Request body")  # rejected before reading a byte
    received = bytearray()
    async for chunk in request.stream():
        received.extend(chunk)
        if len(received) > limit:
            raise too_large(limit, "Request body")
    return bytes(received)


def decode_body(raw: bytes, content_encoding: str | None, limit: int) -> bytes:
    """Apply Content-Encoding (gzip or none), capping the decompressed size."""
    encoding = (content_encoding or "identity").strip().lower()
    if encoding in ("", "identity"):
        return raw
    if encoding != "gzip":
        raise AppError(
            "UNSUPPORTED_ENCODING",
            "Content-Encoding must be gzip or identity.",
            category=ErrorCategory.VALIDATION,
            status_code=415,
        )
    decompressor = zlib.decompressobj(wbits=zlib.MAX_WBITS | 16)
    output = bytearray()
    try:
        remaining = raw
        while remaining and not decompressor.eof:
            piece = decompressor.decompress(remaining[:_CHUNK], limit + 1 - len(output))
            output.extend(piece)
            if len(output) > limit:
                raise too_large(limit, "Decompressed body")
            remaining = decompressor.unconsumed_tail + remaining[_CHUNK:]
        if not decompressor.eof:
            raise batch_invalid("The gzip stream is truncated.")
        if decompressor.unused_data:
            raise batch_invalid("The gzip stream has trailing data.")
    except zlib.error:
        raise batch_invalid("The body is not valid gzip.") from None
    return bytes(output)
