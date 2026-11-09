"""NetworkManager contract tests, using an in-memory backend; no Wi-Fi changes."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("setup_wifi", Path(__file__).resolve().parents[2] / "scripts/go2/setup_wifi.py")
wifi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wifi)


class FakeNM:
    def __init__(self, existing=False, changed=False, wrong_type=False):
        self.calls = []
        self.existing, self.changed, self.wrong_type = existing, changed, wrong_type
        self.active_reads = 0

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ("-g", "GENERAL.TYPE"):
            return "wifi"
        if args[:2] == ("-g", "connection.uuid"):
            return "home-uuid" if args[-1] == "CU_tD9c" else ("farm-uuid" if self.existing else None)
        if args[:2] == ("-g", "connection.type"):
            return "vpn" if self.wrong_type else "802-11-wireless"
        if args[:2] == ("-g", "802-11-wireless.ssid"):
            return "13pro"
        if "--active" in args:
            self.active_reads += 1
            return "other-uuid:wlan0" if self.changed and self.active_reads > 1 else "home-uuid:wlan0"
        if args[:2] == ("connection", "add"):
            assert args[-2:] == ("connection.autoconnect", "no")
            self.existing = True
        return ""


def mutations(nm):
    return [call for call in nm.calls if call[:1] == ("connection",)]


def test_save_profiles_without_switching_or_duplicate_on_repeat():
    nm = FakeNM()
    wifi.configure("example-test-password", run=nm)
    wifi.configure("example-test-password", run=nm)
    writes = mutations(nm)
    assert sum(call[:2] == ("connection", "add") for call in writes) == 1
    assert not any(part in ("up", "down", "disconnect", "restart", "delete") for call in nm.calls for part in call)
    assert writes[-1] == ("connection", "modify", "uuid", "farm-uuid", "connection.autoconnect", "yes")
    credential_index = next(i for i, call in enumerate(writes) if "802-11-wireless-security.psk" in call)
    assert credential_index < writes.index(writes[-1])
    assert any("100" in call and "home-uuid" in call for call in writes)
    assert any("50" in call and "farm-uuid" in call for call in writes)


def test_changed_active_connection_leaves_new_profile_disabled():
    nm = FakeNM(changed=True)
    with pytest.raises(RuntimeError, match="发生变化"):
        wifi.configure("example-test-password", run=nm)
    assert not any("connection.autoconnect" in call and "yes" in call for call in mutations(nm))


def test_unrelated_existing_profile_is_preserved():
    nm = FakeNM(existing=True, wrong_type=True)
    with pytest.raises(RuntimeError, match="用途不同"):
        wifi.configure("example-test-password", run=nm)
    assert not mutations(nm)


@pytest.mark.parametrize("password", ["", "short", "z" * 64])
def test_invalid_password_causes_no_network_writes(password):
    nm = FakeNM()
    with pytest.raises(ValueError):
        wifi.configure(password, run=nm)
    assert not mutations(nm)
