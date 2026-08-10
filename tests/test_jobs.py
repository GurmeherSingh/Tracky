from tracker.jobs import maybe_park_alarm
from tracker.models import FailedJob, get_state


class FakeNotifier:
    def __init__(self):
        self.posts = []

    def post_alert(self, text):
        self.posts.append(text)


def park(session, email_id):
    session.add(FailedJob(email_id=email_id, stage="classify", error="boom",
                          strikes=3, parked=True))
    session.flush()


def test_parking_an_email_raises_one_alarm(session):
    n = FakeNotifier()
    park(session, "a")
    maybe_park_alarm(session, n)
    assert len(n.posts) == 1 and "1" in n.posts[0]
    maybe_park_alarm(session, n)
    assert len(n.posts) == 1  # nothing new parked — stay quiet
    assert get_state(session, "parked_count") == "1"


def test_a_second_park_raises_a_second_alarm(session):
    n = FakeNotifier()
    park(session, "a")
    maybe_park_alarm(session, n)
    park(session, "b")
    maybe_park_alarm(session, n)
    assert len(n.posts) == 2


def test_no_failures_no_noise(session):
    n = FakeNotifier()
    maybe_park_alarm(session, n)
    assert n.posts == []
