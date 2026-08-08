from datetime import datetime, timedelta

from pydantic import BaseModel


class EvalReport(BaseModel):
    per_category: dict[str, dict[str, float]]
    overall_accuracy: float
    deadline_accuracy: float | None
    confusion: dict[str, dict[str, int]]
    n: int

    def pretty(self) -> str:
        lines = [f"n={self.n}  accuracy={self.overall_accuracy:.2%}"]
        for cat, m in sorted(self.per_category.items()):
            lines.append(f"  {cat:18s} P={m['precision']:.2f} "
                         f"R={m['recall']:.2f} support={int(m['support'])}")
        if self.deadline_accuracy is not None:
            lines.append(f"  deadline accuracy (±1h): {self.deadline_accuracy:.2%}")
        return "\n".join(lines)


def score(pairs: list[tuple[str, str]],
          deadline_pairs: list[tuple[datetime | None, datetime | None]]) -> EvalReport:
    cats = sorted({t for t, _ in pairs} | {p for _, p in pairs})
    confusion = {t: {p: 0 for p in cats} for t in cats}
    for true, pred in pairs:
        confusion[true][pred] += 1
    per_category = {}
    for cat in cats:
        tp = confusion[cat][cat]
        fn = sum(confusion[cat][p] for p in cats if p != cat)
        fp = sum(confusion[t][cat] for t in cats if t != cat)
        per_category[cat] = {
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "support": float(tp + fn),
        }
    correct = sum(1 for t, p in pairs if t == p)
    deadline_accuracy = None
    if deadline_pairs:
        ok = 0
        for true_d, pred_d in deadline_pairs:
            if true_d is None and pred_d is None:
                ok += 1
            elif true_d is not None and pred_d is not None and \
                    abs(true_d - pred_d) <= timedelta(hours=1):
                ok += 1
        deadline_accuracy = ok / len(deadline_pairs)
    return EvalReport(per_category=per_category,
                      overall_accuracy=correct / len(pairs) if pairs else 0.0,
                      deadline_accuracy=deadline_accuracy,
                      confusion=confusion, n=len(pairs))
