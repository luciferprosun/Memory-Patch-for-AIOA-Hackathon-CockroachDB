"""Sanitized Step 12 HAT registry failures."""

class HatRegistryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
