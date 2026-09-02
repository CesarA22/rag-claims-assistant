class InsurCoError(Exception):
    """Typed failure. `code` is the problem+json discriminator and the stored error_code."""

    code: str = "internal_error"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)


class ProviderTimeout(InsurCoError):
    code = "provider_timeout"


class ProviderUnavailable(InsurCoError):
    code = "provider_unavailable"


class RateLimited(InsurCoError):
    code = "rate_limited"


class InvalidRequest(InsurCoError):
    code = "invalid_request"
