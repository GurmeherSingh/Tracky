from datetime import UTC, datetime, timedelta

from tracker.evals.metrics import score

T = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def test_perfect_predictions():
    pairs = [("assessment", "assessment"), ("noise", "noise")]
    report = score(pairs, [])
    assert report.overall_accuracy == 1.0
    assert report.per_category["assessment"]["precision"] == 1.0
    assert report.per_category["assessment"]["recall"] == 1.0


def test_confusion_and_precision_recall():
    pairs = [("assessment", "assessment"),
             ("assessment", "noise"),        # missed one (recall hit)
             ("noise", "assessment")]        # false positive (precision hit)
    report = score(pairs, [])
    a = report.per_category["assessment"]
    assert a["precision"] == 0.5 and a["recall"] == 0.5
    assert report.confusion["assessment"]["noise"] == 1


def test_deadline_accuracy_within_one_hour():
    pairs = [("assessment", "assessment")] * 3
    deadlines = [(T, T + timedelta(minutes=30)),   # within 1h → correct
                 (T, T + timedelta(hours=5)),      # off → wrong
                 (None, None)]                     # both None → correct
    report = score(pairs, deadlines)
    assert report.deadline_accuracy == 2 / 3


def test_pretty_renders():
    report = score([("noise", "noise")], [])
    assert "accuracy" in report.pretty()
