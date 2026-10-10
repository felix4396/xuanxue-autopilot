"""整合层测试：MediaCrawler 导入、去AI味、doctor 导入、发布执行、复盘诊断。
运行: python3 -m tests.integration
"""

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = Path(tempfile.mkdtemp(prefix="xap-int-"))


def test_mediacrawler_import():
    from lib.bench.store import BenchStore
    from lib.collect.mediacrawler import import_mediacrawler_dir

    src = TMP / "mc"
    src.mkdir()
    (src / "xhs.jsonl").write_text("\n".join([
        json.dumps({"platform": "xhs", "note_id": "n1", "user_id": "u1", "nickname": "甲",
                    "title": "笔记一", "liked_count": "1,200", "comment_count": "30", "cover_url": "http://c"}),
        json.dumps({"platform": "Douyin", "video_id": "v1", "user_id": "u2",
                    "desc": "视频描述", "liked_count": 500}),
        "not json",  # 容错
    ]), encoding="utf-8")
    db = BenchStore(TMP / "bench.db")
    stats = import_mediacrawler_dir(src, db)
    assert stats["works"] == 2 and stats["accounts"] == 2
    w = [x for x in db.list_works("xhs") if x["wid"] == "n1"][0]
    assert w["likes"] == 1200  # 千分位解析


def test_deaigen():
    from lib.compose.deaigen import deaigen

    out = deaigen("首先，非常重要的是。总之，让我们看看。")
    assert "非常" not in out and "总之" not in out and "首先" not in out
    assert "看看" in out


def test_doctor_import():
    from lib.analyze.assets import AssetStore
    from lib.analyze.doctor import import_doctor_report

    report = TMP / "r.md"
    report.write_text(
        "## 拆解\n钩子在第 3 秒：直接抛出反常识结论\n结构是清单体。\n钩子 8 分。\n标题：三个方法学会X",
        encoding="utf-8",
    )
    assets = AssetStore(TMP / "a.json")
    r = import_doctor_report(report, "xhs", "n1", assets)
    assert r["hook"]["second"] == 3
    assert r["structure"] == "list"
    assert r["score"]["hook"] == 8
    assert r["title"] == "三个方法学会X"


def test_dispatch():
    from lib.publish.queue import PublishQueue
    from lib.publish.dispatcher import Dispatcher, DryRunUploader

    q = PublishQueue(TMP / "p.db")
    qid = q.enqueue("xhs", "标题", "# 内容")
    q.transition(qid, "approved")
    q.schedule(qid, when=time.time() - 1)  # 已到期
    d = Dispatcher(q, uploaders={"dryrun": DryRunUploader(TMP / "log.tsv")})
    res = d.dispatch_due()
    assert len(res) == 1 and res[0]["remote_id"].startswith("dry-")
    assert q.list("published")[0]["remote_id"] == res[0]["remote_id"]


def test_diagnose_loop():
    from lib.analyze.assets import AssetStore
    from lib.review.metrics import MetricsStore
    from lib.review.diagnose import diagnose

    m = MetricsStore(TMP / "m.db")
    assets = AssetStore(TMP / "a.json")
    assets.add_analysis("xhs", "w1", {"hook": {"text": "好钩子"}, "score": {"hook": 9}})
    m.record("xhs", "r1", 24, views=100, likes=20, finishes=50)  # 高互动
    d = diagnose("xhs", "r1", m, assets)
    assert d["like_rate"] == 0.2
    assert "钩子有效" in str(d["verdict"])
    assert assets.data.get("validated")  # 好钩子已回流入库

    m.record("xhs", "r2", 24, views=100, likes=1, finishes=5)  # 低互动
    d2 = diagnose("xhs", "r2", m, assets)
    assert "开头钩子弱" in str(d2["verdict"]) and "内容价值感不足" in str(d2["verdict"])


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} integration tests passed")
