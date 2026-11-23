"""Explicit USB and ASRL System VISA sessions."""

import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from types import SimpleNamespace

from pyvisa.constants import ControlFlow, Parity, StopBits

from .errors import LoadError, communication_error

TIMEOUT_MS = 5000
TERMINATION = "\n"
QUERY_DELAY_S = 0.150
ASRL_OPEN_TIMEOUT_MS = 1000
ASRL_IDN_TIMEOUT_MS = 1000
SERIAL_CHOICES = {
    "parity": {value.name: value for value in Parity},
    "stop_bits": {"1": StopBits.one, "1.5": StopBits.one_and_a_half, "2": StopBits.two},
    "flow_control": dict(ControlFlow.__members__),
    "read_termination": {"CR": "\r", "LF": "\n", "CRLF": "\r\n"},
    "write_termination": {"CR": "\r", "LF": "\n", "CRLF": "\r\n"},
}


@dataclass(frozen=True)
class SerialOptions:
    baud_rate: int | None = None
    data_bits: int | None = None
    parity: str | None = None
    stop_bits: str | None = None
    flow_control: str | None = None
    read_termination: str | None = None
    write_termination: str | None = None

    def overrides(self) -> dict:
        options = {name: value for name, value in asdict(self).items() if value is not None}
        if self.baud_rate is not None and (
            type(self.baud_rate) is not int or not 1 <= self.baud_rate <= 0xFFFFFFFF
        ):
            raise LoadError("argument_error", "Serial baud rate must be a positive 32-bit unsigned integer.")
        if self.data_bits is not None and (type(self.data_bits) is not int or not 5 <= self.data_bits <= 8):
            raise LoadError("argument_error", "Serial data bits must be an integer from 5 through 8.")
        for name, choices in SERIAL_CHOICES.items():
            if name in options:
                value = options[name]
                if not isinstance(value, str) or value not in choices:
                    raise LoadError("argument_error", f"Unsupported serial {name.replace('_', ' ')}.")
                options[name] = choices[value]
        return options


class TraceSessionLost(LoadError):
    """The command/reply stream is no longer trustworthy."""

    def __init__(self, operation, **details):
        super().__init__("trace_session_untrusted",
                         "TRACE lost reliable communication. Feed and Input states are unknown.",
                         3, operation=operation, feed="UNKNOWN", input_state="UNKNOWN", **details)


def mark_session_untrusted(session) -> None:
    session._trace_session_untrusted = True


def require_trusted_session(session) -> None:
    if getattr(session, "_trace_session_untrusted", False):
        raise TraceSessionLost("session")


def validate_connection(resource: str | None, backend: str, *, required: bool,
                        asrl: bool = False) -> None:
    if backend != "system":
        raise LoadError("unsupported_backend", "Only --backend system (IVI) is permitted.")
    if resource is None:
        if required:
            raise LoadError("missing_resource", "LIVE requests require an explicit --resource.")
        return
    if asrl:
        if not re.fullmatch(r"ASRL[0-9]+::INSTR", resource, re.IGNORECASE | re.ASCII):
            raise LoadError("invalid_resource", "An explicit ASRL INSTR VISA resource is required.")
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
            session.query_delay = QUERY_DELAY_S
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


def query(session, command: str) -> str:
    try:
        return session.query(command)
    except Exception as exc:
        error = communication_error("query_failed", f"Query {command} failed.", exc)
        error.details["query"] = command
        raise error from exc


@contextmanager
def open_asrl_session(resource: str, overrides: dict):
    import pyvisa

    class ASRLSession(pyvisa.resources.SerialInstrument):
        def set_io_timeout(self, timeout: int) -> None:
            try:
                self.timeout = timeout
            except Exception as exc:
                raise communication_error("visa_configure_failed", "Cannot set ASRL I/O timeout.", exc) from exc

    validate_connection(resource, "system", required=True, asrl=True)
    manager_handle = session_handle = None
    primary = None
    try:
        try:
            library = pyvisa.highlevel.open_visa_library("@ivi")
            # PyVISA's ResourceManager caches a session on the library. Own an
            # independent native manager handle so ASRL cleanup cannot close USB.
            manager_handle, _ = library.open_default_resource_manager()
        except Exception as exc:
            raise communication_error("visa_manager_failed", "Cannot load System VISA for ASRL.", exc) from exc
        try:
            manager = SimpleNamespace(visalib=library, session=manager_handle)
            session = ASRLSession(manager, resource)
            # Bypass Resource.open and its viClear recovery loop.
            session_handle, status = library.open(manager_handle, resource,
                                                  pyvisa.constants.AccessModes.no_lock,
                                                  ASRL_OPEN_TIMEOUT_MS)
            session.session = session_handle
        except Exception as exc:
            raise communication_error("visa_open_failed", "Cannot open the ASRL session.", exc) from exc
        if status != pyvisa.constants.StatusCode.success:
            raise LoadError("visa_open_failed", "VISA did not report a ready ASRL session.",
                            3, visa_status=int(status))
        try:
            for name, value in overrides.items():
                setattr(session, name, value)
        except Exception as exc:
            raise communication_error("visa_configure_failed", "Cannot configure the ASRL session.", exc) from exc
        session.set_io_timeout(ASRL_IDN_TIMEOUT_MS)
        try:
            manufacturer = session.get_visa_attribute(
                pyvisa.constants.ResourceAttribute.resource_manufacturer_name
            )
        except Exception:
            manufacturer = None
        yield session, {"library": str(library.library_path), "manufacturer": manufacturer}
    except BaseException as exc:
        primary = exc
        raise
    finally:
        failures = []
        if session_handle is not None:
            # Prevent PyVISA destruction from retrying a failed native close.
            session.session = None
        for name, handle in (("session", session_handle), ("resource_manager", manager_handle)):
            if handle is not None:
                try:
                    library.close(handle)
                except Exception as exc:
                    failures.append(communication_error(
                        "cleanup_failed", f"Could not close ASRL {name}.", exc
                    ).as_dict())
        if failures:
            if isinstance(primary, LoadError):
                primary.details["cleanup_errors"] = failures
                primary.exit_code = 3
            elif primary is None:
                raise LoadError("cleanup_failed", "ASRL VISA cleanup failed.", 3, cleanup_errors=failures)
            else:
                primary.__context__ = LoadError(
                    "cleanup_failed", "ASRL VISA cleanup failed.", 3, cleanup_errors=failures
                )
