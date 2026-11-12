"""统一错误结构（设计文档 v2.0 第 5.3 章 / 契约冻结部分）。

所有非 2xx 响应体形状固定为::

    {"error": {"code": "...", "message": "...", "details": {...}, "trace_id": "..."}}

要点：
- ``code`` 是**机器可读的稳定枚举**，客户端据此分支；``message`` 面向人，可改。
- ``trace_id`` 与响应头 ``X-Trace-Id``、服务端日志一致，便于把用户截图对应到那条日志。
- ``details`` 只放结构化补充（字段错误、冲突对象、被拒原因），**不放堆栈**。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.trace import get_trace_id


class ApiError(Exception):
    """所有业务异常的基类。子类只需声明 ``status_code`` 与 ``code``。"""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "BAD_REQUEST"

    def __init__(
        self,
        message: str,
        *,
        details: Any | None = None,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class BadRequest(ApiError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "BAD_REQUEST"


class Unauthorized(ApiError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHORIZED"


class Forbidden(ApiError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "FORBIDDEN"


class NotFound(ApiError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"


class Conflict(ApiError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"


class IdempotencyKeyReused(Conflict):
    code = "IDEMPOTENCY_KEY_REUSED"


class PlanVersionConflict(Conflict):
    code = "PLAN_VERSION_CONFLICT"


class PreconditionFailed(ApiError):
    status_code = status.HTTP_412_PRECONDITION_FAILED
    code = "PRECONDITION_FAILED"


class ServiceUnavailable(ApiError):
    """外部依赖不可用（免钥源未配置/超时，ADR-0034）：诚实失败，不伪造数据。"""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "EXTERNAL_SOURCE_UNAVAILABLE"


class PlanNotFeasible(ApiError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "PLAN_NOT_FEASIBLE"


class Unprocessable(ApiError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "UNPROCESSABLE_ENTITY"


class UnsupportedMedia(ApiError):
    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    code = "UNSUPPORTED_MEDIA_TYPE"


def error_body(err: ApiError, trace_id: str) -> dict[str, Any]:
    return {
        "error": {
            "code": err.code,
            "message": err.message,
            "details": err.details,
            "trace_id": trace_id,
        }
    }


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:  # noqa: ARG001
        trace_id = get_trace_id()
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc, trace_id),
            headers={"X-Trace-Id": trace_id},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:  # noqa: ARG001
        trace_id = get_trace_id()
        # Pydantic 的 ctx 可能含异常对象，不可直接 JSON 序列化，这里只保留可序列化字段。
        details = [
            {"loc": list(err.get("loc", [])), "msg": err.get("msg"), "type": err.get("type")}
            for err in exc.errors()
        ]
        err = ApiError("请求参数校验失败", details=details, code="VALIDATION_ERROR")
        return JSONResponse(
            status_code=err.status_code,
            content=error_body(err, trace_id),
            headers={"X-Trace-Id": trace_id},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:  # noqa: ARG001
        trace_id = get_trace_id()
        code = {
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            409: "CONFLICT",
            415: "UNSUPPORTED_MEDIA_TYPE",
        }.get(exc.status_code, "HTTP_ERROR")
        err = ApiError(str(exc.detail), code=code, status_code=exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(err, trace_id),
            headers={"X-Trace-Id": trace_id},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:  # noqa: ARG001
        trace_id = get_trace_id()
        err = ApiError(
            "服务端内部错误", code="INTERNAL_ERROR", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        return JSONResponse(
            status_code=err.status_code,
            content=error_body(err, trace_id),
            headers={"X-Trace-Id": trace_id},
        )