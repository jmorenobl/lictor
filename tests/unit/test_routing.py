import pytest

from lictor.domain.model import VerifyResult
from lictor.domain.routing import (
    END,
    route_build_driver,
    route_commit,
    route_implement,
    route_load_spec,
    route_pause,
    route_red_check,
    route_verify,
)


def test_route_load_spec_exit_code() -> None:
    assert route_load_spec({"exit_code": 10, "seal_ok": True}) == END


def test_route_load_spec_seal_ok() -> None:
    assert route_load_spec({"exit_code": None, "seal_ok": True}) == "red_check"


def test_route_load_spec_seal_not_ok() -> None:
    assert route_load_spec({"exit_code": None, "seal_ok": False}) == "build_driver"


def test_route_red_check_exit_code() -> None:
    assert route_red_check({"exit_code": 10, "last_verify": None}) == END


def test_route_red_check_last_verify_failed_verifyresult() -> None:
    last_verify = VerifyResult(ok=False)
    assert route_red_check({"exit_code": None, "last_verify": last_verify}) == "build_driver"


def test_route_red_check_last_verify_failed_dict() -> None:
    assert route_red_check({"exit_code": None, "last_verify": {"ok": False}}) == "build_driver"


def test_route_red_check_passed() -> None:
    last_verify = VerifyResult(ok=True)
    assert route_red_check({"exit_code": None, "last_verify": last_verify}) == "pick_next"
    assert route_red_check({"exit_code": None, "last_verify": None}) == "pick_next"


def test_route_implement_exit_code() -> None:
    assert route_implement({"exit_code": 10}) == END
    assert route_implement({"exit_code": 10, "agent_signal": "ok"}) == END


def test_route_implement_rate_limited() -> None:
    assert route_implement({"agent_signal": "rate_limited"}) == "pause"


def test_route_implement_spec_conflict() -> None:
    assert route_implement({"agent_signal": "spec_conflict"}) == "quarantine"


def test_route_implement_ok_or_error() -> None:
    assert route_implement({"agent_signal": "ok"}) == "verify"
    assert route_implement({"agent_signal": "error"}) == "verify"
    assert route_implement({"agent_signal": "timeout"}) == "verify"
    assert route_implement({"agent_signal": None}) == "verify"


def test_route_verify_success() -> None:
    last_verify = VerifyResult(ok=True)
    assert route_verify({"last_verify": last_verify, "attempt": 1}, max_attempts=3) == "commit"


def test_route_verify_retry_when_under_max_attempts() -> None:
    last_verify = VerifyResult(ok=False)
    assert (
        route_verify({"last_verify": last_verify, "attempt": 1, "max_attempts": 3}) == "implement"
    )
    assert route_verify({"last_verify": last_verify, "attempt": 2}, max_attempts=3) == "implement"


def test_route_verify_quarantine_when_attempts_exhausted() -> None:
    last_verify = VerifyResult(ok=False)
    assert (
        route_verify({"last_verify": last_verify, "attempt": 3, "max_attempts": 3}) == "quarantine"
    )
    assert route_verify({"last_verify": last_verify, "attempt": 4}, max_attempts=3) == "quarantine"


def test_route_verify_with_config_limits() -> None:
    class DummyLimits:
        max_attempts = 4

    class DummyConfig:
        limits = DummyLimits()

    last_verify = VerifyResult(ok=False)
    assert (
        route_verify({"last_verify": last_verify, "attempt": 3, "config": DummyConfig()})
        == "implement"
    )
    assert (
        route_verify({"last_verify": last_verify, "attempt": 4, "config": DummyConfig()})
        == "quarantine"
    )


def test_route_verify_default_fallback_limit() -> None:
    last_verify = VerifyResult(ok=False)
    # Default limit is 3
    assert route_verify({"last_verify": last_verify, "attempt": 2}) == "implement"
    assert route_verify({"last_verify": last_verify, "attempt": 3}) == "quarantine"


def test_route_verify_with_other_objects() -> None:
    # Not None and no .ok attribute
    assert (
        route_verify({"last_verify": "invalid_verify_obj", "attempt": 1}, max_attempts=3)
        == "implement"
    )
    assert route_verify({"last_verify": None, "attempt": 1}, max_attempts=3) == "implement"


def test_route_pause_returns_resume_to() -> None:
    assert route_pause({"resume_to": "implement"}) == "implement"
    assert route_pause({"resume_to": "build_driver"}) == "build_driver"
    assert route_pause({}) == "implement"


def test_route_implement_unavailable_agent_pauses() -> None:
    assert route_implement({"agent_signal": "unavailable"}) == "pause"


def test_route_implement_rejected_retries_like_a_failed_verification() -> None:
    state = {"agent_signal": "rejected", "attempt": 1}
    assert route_implement(state, max_attempts=3) == "implement"


def test_route_implement_rejected_quarantines_when_attempts_run_out() -> None:
    state = {"agent_signal": "rejected", "attempt": 3}
    assert route_implement(state, max_attempts=3) == "quarantine"


def test_route_implement_rejected_obeys_the_given_limit_over_the_default() -> None:
    state = {"agent_signal": "rejected", "attempt": 2}
    assert route_implement(state, max_attempts=2) == "quarantine"


def test_route_verify_obeys_the_given_limit_over_the_default() -> None:
    last_verify = VerifyResult(ok=False)
    assert route_verify({"last_verify": last_verify, "attempt": 2}, max_attempts=2) == "quarantine"


def test_route_verify_obeys_the_limit_in_the_state_over_the_default() -> None:
    last_verify = VerifyResult(ok=False)
    state = {"last_verify": last_verify, "attempt": 2, "max_attempts": 2}
    assert route_verify(state) == "quarantine"


def test_route_verify_without_attempts_counts_from_zero() -> None:
    assert route_verify({"last_verify": VerifyResult(ok=False)}, max_attempts=1) == "implement"


def test_route_red_check_blocked_waits_for_the_user() -> None:
    state = {"agent_signal": "blocked", "last_verify": {"ok": False}}
    assert route_red_check(state) == "pause"


def test_route_commit_blocked_waits_for_the_user() -> None:
    assert route_commit({"agent_signal": "blocked"}) == "pause"


def test_route_commit_goes_on_otherwise() -> None:
    assert route_commit({"agent_signal": None}) == "pick_next"


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"exit_code": 10, "agent_signal": "rejected"}, END),
        ({"agent_signal": "rate_limited"}, "pause"),
        ({"agent_signal": "unavailable", "agent_failures": 2}, "pause"),
        ({"agent_signal": "rejected"}, "build_driver"),
        ({"agent_signal": "error", "agent_failures": 1}, "build_driver"),
        ({"agent_signal": "error", "agent_failures": 0}, "red_check"),
        ({"agent_signal": "error"}, "red_check"),
        ({"agent_signal": "ok", "agent_failures": 0}, "red_check"),
        ({}, "red_check"),
    ],
)
def test_route_build_driver_retries_a_call_that_kept_its_attempt(
    state: dict[str, object], expected: str
) -> None:
    assert route_build_driver(state) == expected


def test_route_verify_ends_when_the_verification_stopped_the_run() -> None:
    state = {"exit_code": 10, "last_verify": {"ok": False}, "attempt": 2}

    assert route_verify(state, max_attempts=3) == END
