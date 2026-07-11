from issues import select_issues, parse_labels


def _iss(n, labels, title="t"):
    return {"number": n, "title": title, "labels": [{"name": l} for l in labels]}


def test_parse_labels_extracts_dimensions():
    d = parse_labels(_iss(1, ["status:ready", "type:proof", "difficulty:small", "area:fx"]))
    assert d == {"status": "ready", "type": "proof", "difficulty": "small", "area": "fx"}


def test_select_filters_and_orders_by_difficulty():
    raw = [
        _iss(1, ["status:ready", "type:proof", "difficulty:medium", "area:risk"]),
        _iss(2, ["status:ready", "type:proof", "difficulty:small", "area:fx"]),
        _iss(3, ["status:ready", "type:proof", "good first issue", "area:fx"]),
        _iss(4, ["status:blocked-design", "type:proof", "difficulty:small"]),  # blocked -> out
        _iss(5, ["status:ready", "type:research", "difficulty:hard"]),          # research -> out
        _iss(6, ["status:ready", "type:proof", "difficulty:hard", "area:risk"]),# too hard -> out
    ]
    got = [i["number"] for i in select_issues(raw, max_difficulty="medium")]
    assert got == [3, 2, 1]  # good-first, then small, then medium; 4/5/6 excluded


def test_select_respects_max_difficulty_cap():
    raw = [
        _iss(1, ["status:ready", "type:proof", "difficulty:medium", "area:risk"]),
        _iss(2, ["status:ready", "type:proof", "good first issue", "area:fx"]),
    ]
    # cap at good-first: only the good-first issue survives
    got = [i["number"] for i in select_issues(raw, max_difficulty="good-first")]
    assert got == [2]
