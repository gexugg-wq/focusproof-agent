class MonadRpcUnavailable(RuntimeError):
    """Sanitized retryable RPC failure safe to expose as a finding."""

    _ALLOWED_CODES = frozenset(
        {
            "deadline_exhausted",
            "malformed_response",
            "response_too_large",
            "rpc_error",
            "rpc_http_error",
            "transport_timeout",
            "transport_unavailable",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if code in self._ALLOWED_CODES else "transport_unavailable"
        super().__init__(self.code)
