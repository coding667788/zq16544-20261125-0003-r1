class FolioImpactError(Exception):
    """Base class for all domain and CLI errors."""


class InputValidationError(FolioImpactError):
    """Raised when a CASE document does not match the documented schema."""

    def __init__(self, message, path=None, details=None):
        super().__init__(message)
        self.path = path
        self.details = details or {}


class SearchLimitError(FolioImpactError):
    """Raised when an input exceeds an exhaustive-search limit."""
