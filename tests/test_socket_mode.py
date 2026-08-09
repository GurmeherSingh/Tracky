from tracker.notify.app import run_socket_mode


def test_run_socket_mode_connects_instead_of_start():
    """SocketModeHandler.start() installs a SIGINT handler, which raises
    ValueError off the main thread — the daemon thread must use connect()."""
    calls = []

    class FakeHandler:
        def connect(self):
            calls.append("connect")

        def start(self):
            calls.append("start")

    run_socket_mode(None, None, handler=FakeHandler(),
                    block=lambda: calls.append("block"))
    assert calls == ["connect", "block"]
