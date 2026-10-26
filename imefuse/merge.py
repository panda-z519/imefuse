"""Merge engine: combine an incoming canonical word list into a target IME
without ever deleting or lowering an existing entry.

The three IMEs do not count on the same scale (WeType keeps decayed weights in
the hundreds, Sogou/Doubao keep small commit counters). Raw addition would let
one side swamp the other, so every strategy except P2 first rescales the
incoming counts onto the target's own scale (median-matched), and every
strategy is clamped so a merge can never lower an existing count.
"""
import statistics

STRATEGIES = {
    "P1": "scale-matched sum (default): old + new * median_old/median_new",
    "P2": "raw sum: old + new",
    "P3": "max: keep the larger side",
    "P4": "weighted blend: alpha*old + (1-alpha)*new_scaled",
}


def _median(values):
    vals = [v for v in values if v]
    return statistics.median(vals) if vals else 1.0


def scale_factor(old_values, new_values):
    """How many target-count units one source-count unit is worth."""
    mo = _median(old_values)
    mn = _median(new_values)
    if not mn:
        return 1.0
    factor = mo / mn
    # keep the mapping sane even when one side is nearly empty
    return min(max(factor, 0.05), 20.0)


def merge_counts(old, new, strategy="P1", alpha=0.5, factor=1.0):
    """Return the merged count. Guaranteed to be >= old."""
    old = int(old or 0)
    new = int(new or 0)
    if old == 0:
        return new
    if new == 0:
        return old
    if old == new:
        # same value on both sides: already in sync, never inflate
        return old
    scaled = max(1, int(round(new * factor)))
    if strategy == "P2":
        merged = old + new
    elif strategy == "P3":
        merged = max(old, new)
    elif strategy == "P4":
        merged = int(round(alpha * old + (1 - alpha) * scaled))
    else:  # P1
        merged = old + scaled
    return max(merged, old)


def incoming_weight(rec):
    """Count to use for the incoming side: its n when the source really counts."""
    if rec.get("count_known", True) is False:
        return 1
    return int(rec.get("n") or 1)


def plan_merge(existing, incoming, strategy="P1", alpha=0.5, target_has_count=True):
    """existing / incoming: dicts keyed by (word, pinyin) -> record.

    Returns (plan, summary). Every plan entry carries an action in
    {add, bump, keep, conflict}; nothing ever requests a deletion.
    """
    factor = scale_factor([r.get("n", 0) for r in existing.values()],
                          [incoming_weight(r) for r in incoming.values()])
    plan = []
    for key, rec in incoming.items():
        word, py = key
        cur = existing.get(key)
        if cur is None:
            same_word = [k for k in existing if k[0] == word]
            plan.append({
                "key": key,
                "action": "conflict" if same_word else "add",
                "old": 0,
                "new": incoming_weight(rec),
                "merged": incoming_weight(rec),
                "record": rec,
                "note": ("同一词面已存在于 %s" % same_word[0][1]) if same_word else "",
            })
            continue
        old_n = cur.get("n", 0)
        new_n = incoming_weight(rec)
        if not target_has_count:
            # target keeps no counters: the entry already exists, so this is a
            # no-op. Never rewrite it, never inflate anything.
            plan.append({
                "key": key, "action": "keep", "old": old_n, "new": new_n,
                "merged": old_n, "record": rec, "ts": 0,
                "note": "目标输入法不存词频，词条已存在 → 跳过",
            })
            continue
        merged = merge_counts(old_n, new_n, strategy, alpha, factor)
        plan.append({
            "key": key, "action": "bump" if merged != old_n else "keep",
            "old": old_n, "new": new_n, "merged": merged, "record": rec,
            "ts": max(cur.get("t", 0), rec.get("t", 0)), "note": "",
        })
    touched = {p["key"] for p in plan}
    for key, rec in existing.items():
        if key not in touched:
            plan.append({"key": key, "action": "keep", "old": rec.get("n", 0),
                         "new": 0, "merged": rec.get("n", 0), "record": None, "note": ""})
    summary = {
        "add": sum(1 for p in plan if p["action"] == "add"),
        "bump": sum(1 for p in plan if p["action"] == "bump"),
        "keep": sum(1 for p in plan if p["action"] == "keep"),
        "conflict": sum(1 for p in plan if p["action"] == "conflict"),
        "deleted": 0,
        "lowered": sum(1 for p in plan if p["merged"] < p["old"]),
        "strategy": strategy,
        "scale_factor": round(factor, 3),
        "target_has_count": bool(target_has_count),
    }
    return plan, summary
