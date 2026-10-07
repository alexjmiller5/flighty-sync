from job.main import required_scopes
from job.config import Settings


def test_scopes_exclude_operator_authority():
    scopes = required_scopes(Settings())
    assert len(scopes) == 4
    assert "full" not in scopes and "admin" not in scopes
