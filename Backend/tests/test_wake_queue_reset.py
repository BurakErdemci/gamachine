"""reset() drops the deferral flag too, so the next completion's notice is not lost."""
from agentic import wake_queue as q


def test_notice_after_reset_is_accepted_on_its_first_deferral():
    q.reset_all()
    cid = 777001
    try:
        assert q.requeue_deferred(cid, ["tasks_done|old"]) is True
        q.reset(cid)
        assert q.drain(cid) == []
        assert q.requeue_deferred(cid, ["tasks_done|new"]) is True
        assert q.drain(cid) == ["tasks_done|new"]
    finally:
        q.reset_all()
