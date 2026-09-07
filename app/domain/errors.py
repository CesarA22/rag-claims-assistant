from typing import Any


class InsurCoError(Exception):
    """Typed failure. `code` is the problem+json discriminator and the stored error_code.

    `context` is log-only: model name, prompt, upstream status. Handlers must
    never serialise it into the response body.
    """

    code: str = "internal_error"

    def __init__(self, message: str = "", *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message or self.code)
        self.context: dict[str, Any] = context or {}


class ProviderTimeout(InsurCoError):
    code = "provider_timeout"


class ProviderUnavailable(InsurCoError):
    code = "provider_unavailable"


class RateLimited(InsurCoError):
    code = "rate_limited"


class InvalidRequest(InsurCoError):
    code = "invalid_request"


class ModelContract(InsurCoError):
    """The provider returned a body that is not the schema it was given.

    Not a retryable transport failure and not a refusal: the model broke the
    structured-output contract. Surfaced as its own code so the turn fails
    honestly, rather than shipping a refusal that blames citation validation for
    something citation validation never saw.
    """

    code = "model_contract"


class ProviderDegraded(InsurCoError):
    """Attempts exhausted on a retryable failure. ask.py may still render excerpts."""

    code = "provider_degraded"

    def __init__(
        self,
        message: str = "",
        *,
        underlying_code: str = "provider_unavailable",
        attempts: int = 0,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, context=context)
        self.underlying_code = underlying_code
        self.attempts = attempts


class CircuitOpen(InsurCoError):
    """Breaker refused the call. No provider contact, no spend."""

    code = "circuit_open"
