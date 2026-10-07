import json
import subprocess

import pytest

from job.main import main
from job.credentials import store_token
from test_source import source


def test_dry_run_requires_no_credential_and_writes_no_source(tmp_path, monkeypatch, capsys):
    path = source(tmp_path)
    state = tmp_path / "state"
    monkeypatch.setenv("JOB_STATE_DIR", str(state))
    assert main(["configure", "--source", str(path), "--hub-url", "https://example.test"]) == 0
    before = path.read_bytes()
    assert main(["inspect"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["source_rows"] == 1
    assert path.read_bytes() == before


def test_run_requires_export_baseline_before_credential_access(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(args, 0))
    path = source(tmp_path)
    monkeypatch.setenv("JOB_STATE_DIR", str(tmp_path / "state"))
    main(["configure", "--source", str(path), "--hub-url", "https://example.test"])
    assert main(["run"]) == 1
    assert "verify-export" in capsys.readouterr().err


def test_store_token_keeps_secret_out_of_argv_and_rejects_injection(monkeypatch):
    calls = []

    def fake(args, **kw):
        calls.append((args, kw))
        return subprocess.CompletedProcess(args, 0, stdout="fixture-token\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake)
    store_token("a" * 64, "fixture-token")
    assert all("fixture-token" not in " ".join(args) for args, kw in calls)
    assert any("fixture-token" in kw.get("input", "") for args, kw in calls)
    with pytest.raises(ValueError):
        store_token("a" * 64, "bad\ncommand")


def test_verify_export_is_identity_based_and_mismatch_fails(tmp_path, monkeypatch, capsys):
    path = source(tmp_path)
    state = tmp_path / "state"
    monkeypatch.setenv("JOB_STATE_DIR", str(state))
    main(["configure", "--source", str(path), "--hub-url", "https://example.test"])
    export = tmp_path / "flights.csv"
    export.write_text("Flight Flighty ID\nwrong\n")
    assert main(["verify-export", str(export)]) == 1
    assert not (state / "verification.json").exists()
    export.write_text("Flight Flighty ID\nflight\n")
    assert main(["verify-export", str(export)]) == 0
    assert (state / "verification.json").exists()
