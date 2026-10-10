"""冒烟测试：覆盖六个模块的核心路径。运行: python3 -m tests.smoke"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = Path(tempfile.mkdtemp(prefix="xap-test-"))


def test_bench():
    from lib.bench.store import BenchStore

    db = BenchStore(TMP / "bench.db")
    db.upsert_account("douyin", "u1", nickname="对标A", followers=1000)
    db.upsert_work("douyin", "w1", uid="u1", title="t", likes=50)
    assert len(db.list_accounts("douyin")) == 1
    assert len(db.pending_works("collected")) == 1
    db.mark("douyin", "w1", "collected")
    assert db.pending_works("collected") == []


def test_ratelimit():
    from lib.collect.ratelimit import RateLimiter

    rl = RateLimiter(interval=0.05)
    rl.wait("douyin")
    s = rl.wait("douyin")
    assert s >= 0


def test_assets():
    from lib.analyze.assets import AssetStore

    st = AssetStore(TMP / "assets.json")
    st.add_analysis("douyin", "w1", {
        "hook": {"second": 3, "text": "钩子"},
        "title": "标题", "cover_template": "big_text",
        "score": {"hook": 9},
    })
    assert st.top_hooks(1)[0]["text"] == "钩子"
    assert st.stats()["hooks"] == 1


def test_compose():
    from lib.compose.draft import compose_from_analysis, adapt_platform

    d = compose_from_analysis("测试选题", {"hook": {"text": "钩子"}, "structure": "list"}, angle="我的角度")
    assert len(d.titles) == 3 and d.opening
    md = d.to_markdown()
    assert "候选标题" in md and "CTA" in md
    adapt_platform(d, "xhs")
    assert all(len(t) <= 20 for t in d.titles)


def test_publish_gateway():
    from lib.publish.queue import PublishQueue

    q = PublishQueue(TMP / "pub.db")
    qid = q.enqueue("xhs", "标题", "# 草稿")
    # 红线：未人审不允许直接发布
    try:
        q.transition(qid, "published")
        raise AssertionError("应拒绝跳过人审")
    except ValueError:
        pass
    q.transition(qid, "approved")
    q.schedule(qid, when=0)  # 立即到期
    assert len(q.due()) == 1
    q.transition(qid, "published")
    assert q.list("published")[0]["remote_id"] == ""


def test_metrics():
    from lib.review.metrics import MetricsStore

    m = MetricsStore(TMP / "metrics.db")
    m.record("xhs", "r1", 24, views=100, likes=10)
    assert m.engagement("xhs", "r1")["like_rate"] == 0.1
    m.record("xhs", "r1", 24, views=200, likes=20)  # 覆盖更新
    assert m.engagement("xhs", "r1")["last"]["views"] == 200


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
