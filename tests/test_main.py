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


def test_move_hub_carries_the_credential_and_baseline_to_the_new_url(monkeypatch, tmp_path):
    import json
    import job.main as cli
    from job.credentials import account

    old, new = "https://old.test", "https://new.test"
    (tmp_path / "config.json").write_text(json.dumps({"hub_url": old}))
    (tmp_path / "verification.json").write_text(
        json.dumps({"hub_url": old, "source": "x", "source_rows": 3})
    )
    tokens = {account(old): "device-token"}
    sessions = []

    class Hub:
        def __init__(self, url, token):
            sessions.append((url, token))

        def close(self):
            pass

    monkeypatch.setattr(cli, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        cli,
        "load_settings",
        lambda: Settings.model_validate_json((tmp_path / "config.json").read_text()),
    )
    monkeypatch.setattr(cli, "Hub", Hub)
    monkeypatch.setattr(cli, "check_session", lambda hub, settings: None)
    monkeypatch.setattr(cli, "read_token", lambda key: tokens[key])
    monkeypatch.setattr(cli, "store_token", lambda key, token: tokens.__setitem__(key, token))
    monkeypatch.setattr(cli, "delete_token", lambda key: tokens.pop(key))
    assert cli.main(["move-hub", "--hub-url", new]) == 0
    assert sessions == [(new, "device-token")]
    assert tokens == {account(new): "device-token"}
    assert json.loads((tmp_path / "config.json").read_text())["hub_url"] == new
    assert json.loads((tmp_path / "verification.json").read_text())["hub_url"] == new
