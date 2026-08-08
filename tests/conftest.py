import pytest

from tracker.db import make_engine, make_session_factory
from tracker.models import Base


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    s = factory()
    yield s
    s.close()
