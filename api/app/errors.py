from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    """Base class for errors an endpoint raises deliberately (bad input,
    not found, etc). Always rendered through the same {error: {...}} envelope.
    """

    def __init__(self, code: str, message: str, status_code: int, details: object = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class NotFoundError(ApiError):
    def __init__(self, message: str, details: object = None):
        super().__init__("not_found", message, status.HTTP_404_NOT_FOUND, details)


class BadRequestError(ApiError):
    def __init__(self, message: str, details: object = None):
        super().__init__("bad_request", message, status.HTTP_400_BAD_REQUEST, details)


class UpstreamError(ApiError):
    """Raised when an upstream service (e.g. database) is unreachable or returns an error"""
    def __init__(self, message: str, details: object = None):
        super().__init__("upstream_error", message, status.HTTP_502_BAD_GATEWAY, details)


def _envelope(code: str, message: str, details: object = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_envelope("validation_error", "Request failed validation", exc.errors()),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope("http_error", str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, _exc: Exception) -> JSONResponse:
        # Never leak a stack trace on the wire.
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope("internal_error", "An unexpected error occurred"),
        )
