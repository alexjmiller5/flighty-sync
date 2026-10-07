import pytest
from job.main import check_session, required_scopes
from job.config import Settings
from job.sync import SyncError


def test_refuse_broad_or_incomplete_credentials():
    settings = Settings()

    class Session:
        def __init__(self, scopes):
            self.scopes = scopes

        def session(self):
            return {
                "scopes": self.scopes,
                "capabilities": {"conditional_patch": "revision-v1", "files": "opaque-key-v1"},
            }

    for scopes in [
        ["full"],
        ["admin"],
        list(required_scopes(settings)) + ["tables:read:people"],
        [],
    ]:
        with pytest.raises(SyncError):
            check_session(Session(scopes), settings)
    check_session(Session(list(required_scopes(settings))), settings)


@pytest.mark.parametrize("command", ["run", "inspect"])
def test_only_run_starts_flighty_before_reading(monkeypatch, tmp_path, command):
    import subprocess
    import job.main as cli

    calls = []
    monkeypatch.setattr(cli, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "load_settings", Settings)
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))

    def read(_):
        calls.append("read")
        raise SyncError("synthetic source stop")

    monkeypatch.setattr(cli, "read_flights", read)
    assert cli.main([command]) == 1
    expected = ["read"]
    if command == "run":
        expected.insert(
            0,
            (
                ["/usr/bin/open", "-g", "-a", "Flighty"],
                {
                    "check": True,
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                },
            ),
        )
    assert calls == expected
