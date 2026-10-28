"""Prevent hardware access; provide a recording VISA substitute when requested."""

from types import SimpleNamespace

import pyvisa
import pytest


@pytest.fixture(autouse=True)
def no_hardware(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("A test attempted to create a real VISA ResourceManager.")

    monkeypatch.setattr(pyvisa, "ResourceManager", refuse)


@pytest.fixture
def fake_visa(monkeypatch, no_hardware):
    calls = []
    failures = {}
    responses = {
        "*IDN?": "ITECH Ltd,IT8812C,PRIVATE-SERIAL,1.2",
        "INP:STAT?": "1",
        "MEAS:VOLT?": "12.0", "MEAS:CURR?": "0.5", "MEAS:POW?": "6.0",
    }

    def event(name):
        calls.append(name)
        if name in failures:
            raise failures[name]

    class Session:
        open_status = pyvisa.constants.StatusCode.success

        def __setattr__(self, name, value):
            event((name, value))
            super().__setattr__(name, value)

        def query(self, query):
            event(query)
            value = responses[query]
            if isinstance(value, Exception):
                raise value
            return value

        def get_visa_attribute(self, attribute):
            event("runtime_metadata")
            return "FAKE VISA"

        def close(self):
            event("session_close")

    session = Session()

    class Manager:
        visalib = SimpleNamespace(library_path="FAKE-VISA.dll")

        def open_resource(self, resource, *, open_timeout, resource_pyclass):
            event(("open", resource, open_timeout))
            event("open_resource")
            return session

        def close(self):
            event("manager_close")

    def factory(backend):
        event(("backend", backend))
        event("manager")
        return Manager()

    monkeypatch.setattr(pyvisa, "ResourceManager", factory)
    return SimpleNamespace(calls=calls, failures=failures, responses=responses, session=session)
