"""Explicit System VISA discovery; only USB INSTR receives read-only IDN probes."""

from .errors import LoadError, communication_error
from .identity import MODEL_ID, parse_identity
from .visa import open_session, validate_connection


def _listed_resources() -> tuple[str, ...]:
    import pyvisa

    try:
        manager = pyvisa.ResourceManager("@ivi")
    except Exception as exc:
        raise communication_error("visa_manager_failed", "Cannot load System VISA.", exc) from exc
    primary = None
    try:
        try:
            # The default PyVISA filter can omit non-INSTR resource types.
            return tuple(manager.list_resources("?*"))
        except Exception as exc:
            error = communication_error("visa_list_failed", "Cannot enumerate VISA resources.", exc)
            primary = error
            raise error from exc
    finally:
        try:
            manager.close()
        except Exception as exc:
            cleanup = communication_error("cleanup_failed", "Cannot close VISA resource manager.", exc)
            if primary is not None:
                primary.details["cleanup_errors"] = [cleanup.as_dict()]
            else:
                raise cleanup from exc


def _generic_idn(raw: str) -> bool:
    return len(parts := [field.strip() for field in raw.split(",")]) == 4 and all(parts)


def discover_resources(*, live_only: bool = False) -> dict:
    """List all VISA resources, or IDN-verify only explicitly eligible USB INSTR."""
    resources = _listed_resources()
    if not live_only:
        rows = [{"resource": resource} for resource in resources]
    else:
        rows = []
        for resource in resources:
            try:
                validate_connection(resource, "system", required=True)
            except LoadError:
                continue
            try:
                with open_session(resource) as (session, _runtime):
                    raw = session.query("*IDN?").strip()
                    if not _generic_idn(raw):
                        continue
                    try:
                        parse_identity(raw)
                        model_id = MODEL_ID
                    except LoadError:
                        model_id = None
                    row = {"resource": resource, "idn": raw, "model_id": model_id}
                # Append only after the VISA session and manager have closed successfully.
                rows.append(row)
            except LoadError:
                # A failed probe is not a live result; keep probing other USB devices.
                continue
            except Exception:
                # Native VISA exceptions can contain resource details; never expose them.
                continue
    return {"backend": "system", "live_only": live_only, "count": len(rows), "resources": rows}
