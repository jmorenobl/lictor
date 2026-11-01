"""Pure routing functions for the Lictor execution cycle in domain layer."""

from collections.abc import Mapping
from typing import Any, Final

END: Final[str] = "__end__"


def _is_verify_ok(verify_obj: Any) -> bool:
    if verify_obj is None:
        return False
    if hasattr(verify_obj, "ok"):
        return bool(verify_obj.ok)
    if isinstance(verify_obj, Mapping) and "ok" in verify_obj:
        return bool(verify_obj["ok"])
    return False


def route_load_spec(state: Mapping[str, Any]) -> str:
    """Route after load_spec node."""
    if state.get("exit_code") is not None:
        return END
    return "red_check" if state.get("seal_ok") else "build_driver"


def route_red_check(state: Mapping[str, Any], max_attempts: int | None = None) -> str:
    """Route after red_check node."""
    if state.get("exit_code") is not None:
        return END

    if state.get("agent_signal") == "blocked":
        return "pause"

    last_verify = state.get("last_verify")
    if last_verify is not None and not _is_verify_ok(last_verify):
        return "build_driver"

    return "pick_next"


def route_implement(state: Mapping[str, Any], max_attempts: int | None = None) -> str:
    """Route after implement node."""
    if state.get("exit_code") is not None:
        return END

    signal = state.get("agent_signal")
    if signal in ("rate_limited", "unavailable"):
        return "pause"
    if signal == "spec_conflict":
        return "quarantine"
    if signal == "rejected":
        return _retry_or_quarantine(state, max_attempts)
    return "verify"


def route_verify(state: Mapping[str, Any], max_attempts: int | None = None) -> str:
    """Route after verify node."""
    last_verify = state.get("last_verify")
    if _is_verify_ok(last_verify):
        return "commit"
    return _retry_or_quarantine(state, max_attempts)


def _retry_or_quarantine(state: Mapping[str, Any], max_attempts: int | None) -> str:
    limit: int
    if max_attempts is not None:
        limit = max_attempts
    elif "max_attempts" in state and isinstance(state["max_attempts"], int):
        limit = state["max_attempts"]
    elif hasattr(state.get("config"), "limits") and hasattr(state["config"].limits, "max_attempts"):
        limit = int(state["config"].limits.max_attempts)
    else:
        limit = 3

    attempt = int(state.get("attempt") or 0)
    if attempt < limit:
        return "implement"
    return "quarantine"


def route_commit(state: Mapping[str, Any]) -> str:
    """Route after commit node: a branch that can't advance waits for the user."""
    return "pause" if state.get("agent_signal") == "blocked" else "pick_next"


def route_pause(state: Mapping[str, Any]) -> str:
    """Route after pause node when resumed."""
    resume_target = state.get("resume_to")
    if isinstance(resume_target, str) and resume_target:
        return resume_target
    return "implement"
