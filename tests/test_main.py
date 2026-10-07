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
