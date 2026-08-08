from tracker.ingest.clean import MAX_BODY_CHARS, clean_body


def test_strips_html():
    assert clean_body("<div><p>Hello <b>world</b></p></div>") == "Hello world"


def test_drops_quoted_reply_chain():
    body = "Thanks, see you Friday.\n\nOn Tue, Aug 4, 2026 at 9:00 AM Recruiter <r@x.com> wrote:\n> earlier text"
    assert clean_body(body) == "Thanks, see you Friday."


def test_drops_signature_delimiter():
    body = "Please complete the assessment.\n-- \nJane Recruiter\nAcme Corp"
    assert clean_body(body) == "Please complete the assessment."


def test_caps_length_keeping_head():
    body = "important start. " + ("x" * 20000)
    out = clean_body(body)
    assert len(out) <= MAX_BODY_CHARS
    assert out.startswith("important start.")
