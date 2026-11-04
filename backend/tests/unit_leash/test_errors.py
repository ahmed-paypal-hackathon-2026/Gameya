"""LeashError carries a code, message, details and an HTTP status."""

import pytest
from app.leash.service import LeashError
from app.schemas.contracts import LeashErrorCode

EXPECTED = {
    LeashErrorCode.ORGANISER_ONLY: 401,
    LeashErrorCode.LEASH_DECISION_NOT_FOUND: 404,
    LeashErrorCode.LEASH_ALREADY_RESOLVED: 409,
    LeashErrorCode.LEASH_STALE_DECISION: 409,
    LeashErrorCode.LEASH_POLICY_INVALID: 422,
    LeashErrorCode.LEASH_DISABLED: 503,
}


@pytest.mark.parametrize(("code", "status"), list(EXPECTED.items()))
def test_http_status_mapping(code: LeashErrorCode, status: int) -> None:
    assert LeashError(code, "msg").http_status == status


def test_every_code_is_mapped() -> None:
    assert set(EXPECTED) == set(LeashErrorCode)


def test_fields_and_default_details() -> None:
    err = LeashError(LeashErrorCode.LEASH_ALREADY_RESOLVED, "done", {"decision_id": "lsd_1"})
    assert err.code is LeashErrorCode.LEASH_ALREADY_RESOLVED
    assert err.message == "done" and err.details == {"decision_id": "lsd_1"}
    assert str(err) == "done"
    assert LeashError(LeashErrorCode.LEASH_DISABLED, "off").details == {}


def test_package_exports_resolve_and_leash_error() -> None:
    from app import leash

    assert {"resolve", "LeashError"} <= set(leash.__all__)
    assert leash.LeashError is LeashError
