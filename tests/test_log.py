import json

from tracker.log import configure_logging, get_logger


def test_logging_survives_the_process(tmp_path):
    path = tmp_path / "logs" / "tracker.log"
    configure_logging(path)
    get_logger(component="test").info("something_happened", detail=42)
    line = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert line["event"] == "something_happened"
    assert line["component"] == "test" and line["detail"] == 42
    assert line["level"] == "info" and "timestamp" in line
