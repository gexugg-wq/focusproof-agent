class MonadRpcUnavailable(RuntimeError):
    """Sanitized retryable RPC failure safe to expose as a finding."""
