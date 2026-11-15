import pytest

from slack_app.identity import runtime_session_id, runtime_user_id, thread_session_id


def test_user_id_is_workspace_scoped():
    assert runtime_user_id("T1", "U1") == "slack-T1-U1"
    assert runtime_user_id("T1", "U1") != runtime_user_id("T2", "U1")


def test_user_id_requires_both_parts():
    with pytest.raises(ValueError):
        runtime_user_id("", "U1")


def test_session_id_is_long_enough_and_per_user():
    alice = runtime_session_id("T1", "C1", "1.2", "UALICE")
    bob = runtime_session_id("T1", "C1", "1.2", "UBOB")
    assert len(alice) >= 33
    assert alice != bob
    assert alice == runtime_session_id("T1", "C1", "1.2", "UALICE")


def test_thread_session_is_shared_by_everyone_in_the_thread_and_readable():
    assert thread_session_id("T1", "C1", "1731654620.123456") == "T1:C1:1731654620.123456"
    # The per-person runtime session differs by user, the trace session doesn't.
    assert runtime_session_id("T1", "C1", "1.2", "UALICE") != runtime_session_id("T1", "C1", "1.2", "UBOB")
    assert thread_session_id("T1", "C1", "1.2") == thread_session_id("T1", "C1", "1.2")
    assert thread_session_id("T1", "C1", "1.2") != thread_session_id("T1", "C2", "1.2")
    assert thread_session_id("T1", "C1", "1.2") != thread_session_id("T2", "C1", "1.2")


@pytest.mark.parametrize("parts", [("", "C1", "1.2"), ("T1", "", "1.2"), ("T1", "C1", "")])
def test_thread_session_requires_every_part(parts):
    with pytest.raises(ValueError):
        thread_session_id(*parts)
