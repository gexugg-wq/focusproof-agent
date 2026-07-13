class OpenHandsIntegrationError(RuntimeError):
    """Raised when the OpenHands SDK cannot be imported or adapted."""


class UnsafeOpenHandsToolError(OpenHandsIntegrationError):
    """Raised when a disabled OpenHands tool is requested."""


class OpenHandsCapabilityMissingError(OpenHandsIntegrationError):
    """Raised when a required OpenHands capability is unavailable."""
