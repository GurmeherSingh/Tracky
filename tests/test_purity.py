import ast
import pathlib

FORBIDDEN = {"tracker.db", "tracker.models", "sqlalchemy", "slack_sdk", "slack_bolt", "googleapiclient", "notion_client"}


def test_classify_package_is_pure():
    pkg = pathlib.Path("tracker/classify")
    for py in pkg.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not any(name == f or name.startswith(f + ".") for f in FORBIDDEN), \
                    f"{py.name} imports {name} — classify/ must stay pure"
