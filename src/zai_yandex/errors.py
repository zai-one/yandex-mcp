from __future__ import annotations

import json
from typing import Any

from fastmcp.exceptions import ToolError

from zai_yandex.transport import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderHttpError,
    ProviderNotFoundError,
    ProviderRateLimited,
    ProviderRequestError,
    ProviderRequestTooLarge,
    ProviderResponseError,
    ProviderResponseTooLarge,
    ProviderTimeoutError,
    ProviderTransientHttpError,
    ProviderTransportError,
)


class SafeToolError(ToolError):
    def __init__(self, envelope: dict[str, Any]) -> None:
        self.envelope = envelope
        self.error_code = str(envelope["code"])
        super().__init__(json.dumps(envelope, sort_keys=True, separators=(",", ":")))


class BoundedValidationError(ValueError):
    """Validation failure with a reviewed, non-provider reason code."""

    def __init__(self, reason_code: str) -> None:
        if not reason_code.replace("_", "").isalnum() or len(reason_code) > 80:
            raise ValueError("invalid validation reason code")
        self.reason_code = reason_code
        super().__init__(reason_code)


def safe_provider_error(provider: str, exc: Exception) -> SafeToolError:
    code = "provider_error"
    retryable = False
    retry_after: int | None = None
    if isinstance(exc, ProviderRateLimited):
        code = "provider_rate_limited"
        retryable = True
        retry_after = exc.retry_after_seconds
    elif isinstance(exc, ProviderAuthenticationError):
        code = "provider_authentication_failed"
    elif isinstance(exc, ProviderNotFoundError):
        code = "provider_resource_not_found"
    elif isinstance(exc, (ProviderRequestTooLarge, ProviderResponseTooLarge)):
        code = "provider_payload_too_large"
    elif isinstance(exc, ProviderRequestError):
        code = "provider_request_invalid"
    elif isinstance(exc, ProviderTimeoutError):
        code = "provider_timeout"
        retryable = True
    elif isinstance(exc, ProviderTransportError):
        code = "provider_transport_failed"
        retryable = True
    elif isinstance(exc, ProviderTransientHttpError):
        code = "provider_temporarily_unavailable"
        retryable = True
    elif isinstance(exc, ProviderResponseError):
        code = "provider_response_invalid"
    elif isinstance(exc, ProviderHttpError):
        code = "provider_http_error"
    elif isinstance(exc, ValueError):
        code = "validation_failed"
    elif isinstance(exc, PermissionError):
        code = "provider_disabled"
    elif not isinstance(exc, ProviderError):
        code = "operation_failed"
    envelope: dict[str, Any] = {
        "code": code,
        "provider": provider,
        "retryable": retryable,
    }
    if retry_after is not None:
        envelope["retry_after_seconds"] = retry_after
    if isinstance(exc, BoundedValidationError):
        envelope["reason_code"] = exc.reason_code
    return SafeToolError(envelope)
