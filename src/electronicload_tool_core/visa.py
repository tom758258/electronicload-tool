"""Explicit USB System VISA sessions with bounded I/O and no state-changing cleanup."""

import re
from contextlib import contextmanager

from .errors import LoadError, communication_error

TIMEOUT_MS = 5000
TERMINATION = "\n"


def validate_connection(resource: str | None, backend: str, *, required: bool) -> None:
    if backend != "system":
        raise LoadError("unsupported_backend", "Only --backend system (IVI) is permitted.")
    if resource is None:
        if required:
            raise LoadError("missing_resource", "LIVE requests require an explicit --resource.")
        return
    match = re.fullmatch(
        r"USB[0-9]*::(0x[0-9a-f]{1,4}|[0-9]{1,5})::(0x[0-9a-f]{1,4}|[0-9]{1,5})"
        r"::[A-Za-z0-9_.-]+(?:::[0-9]+)?::INSTR",
        resource,
        re.IGNORECASE,
    )
    if not match or any(
        int(value, 16 if value.lower().startswith("0x") else 10) > 65535
        for value in match.groups()
    ):
        raise LoadError("invalid_resource", "An explicit USB INSTR VISA resource is required.")


@contextmanager
def open_session(resource: str):
    import pyvisa

    class USBSession(pyvisa.resources.USBInstrument):
        def open(self, access_mode=pyvisa.constants.AccessModes.no_lock, open_timeout=TIMEOUT_MS):
            # Resource.open() retries viClear on success_device_not_present.
            # Retain the handle for cleanup, but never run that recovery loop.
            self.session, self.open_status = self._resource_manager.open_bare_resource(
                self._resource_name, access_mode, open_timeout
            )

        def close(self):
            try:
                handle = self.session
            except pyvisa.errors.InvalidSession:
                return
            try:
                # This session registers no events; close only its native handle.
                self.visalib.close(handle)
            finally:
                # Manager.close and destruction must not retry a failed close.
                self.session = None

    manager = session = None
    primary = None
    try:
        try:
            manager = pyvisa.ResourceManager("@ivi")
        except Exception as exc:
            raise communication_error("visa_manager_failed", "Cannot load System VISA.", exc) from exc
        try:
            session = manager.open_resource(
                resource, open_timeout=TIMEOUT_MS, resource_pyclass=USBSession
            )
        except Exception as exc:
            raise communication_error("visa_open_failed", "Cannot open the USB session.", exc) from exc
        if session.open_status != pyvisa.constants.StatusCode.success:
            raise LoadError(
                "visa_open_failed", "VISA did not report a ready session; no recovery was attempted.",
                3, visa_status=int(session.open_status),
            )
        try:
            session.timeout = TIMEOUT_MS
            session.read_termination = TERMINATION
            session.write_termination = TERMINATION
        except Exception as exc:
            raise communication_error(
                "visa_configure_failed", "Cannot configure the VISA session.", exc
            ) from exc
        # VISA metadata is evidence for operator review, not automatic vendor approval.
        try:
            manufacturer = session.get_visa_attribute(
                pyvisa.constants.ResourceAttribute.resource_manufacturer_name
            )
        except Exception:
            manufacturer = None
        runtime = {"library": str(manager.visalib.library_path), "manufacturer": manufacturer}
        yield session, runtime
    except BaseException as exc:
        primary = exc
        raise
    finally:
        failures = []
        for name, handle in (("session", session), ("resource_manager", manager)):
            if handle is not None:
                try:
                    handle.close()
                except Exception as exc:
                    failures.append(communication_error(
                        "cleanup_failed", f"Could not close {name}.", exc
                    ).as_dict())
                    if name == "resource_manager":
                        # Report the failure; destruction must not retry viClose.
                        handle.session = None
                        handle.visalib.resource_manager = None
        if failures:
            if isinstance(primary, LoadError):
                primary.details["cleanup_errors"] = failures
                primary.exit_code = 3
            else:
                raise LoadError(
                    "cleanup_failed", "VISA cleanup failed.", 3,
                    cleanup_errors=failures,
                    **({"primary_error": type(primary).__name__} if primary else {}),
                ) from primary
