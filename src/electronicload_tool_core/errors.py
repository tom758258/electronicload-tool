"""Errors shared by Core and its command-line adapter."""


class LoadError(Exception):
    def __init__(self, code: str, message: str, exit_code: int = 2, **details):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.details = details

    def as_dict(self) -> dict:
        result = {"code": self.code, "message": str(self)}
        if self.details:
            result["details"] = self.details
        return result


def communication_error(code: str, message: str, cause: Exception) -> LoadError:
    # Native exception text can contain a resource or instrument serial number.
    details = {"exception_type": type(cause).__name__}
    status = getattr(cause, "error_code", None)
    if isinstance(status, int):
        details["visa_status"] = int(status)
    return LoadError(code, message, 3, **details)
