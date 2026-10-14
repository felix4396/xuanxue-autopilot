"""账号内容资产：把每条作品文字稿浓缩成精华卡，再归并为账号级内容资产库。

与画像（profile.py）的分工：
  profile.py     → 账号「是什么风格」（人设/语气/公式，抽象层）
  content_assets → 账号「讲过什么内容」（选题/观点/话术/结构实例，素材层）
内容生成时两者配合：画像定风格，内容资产供选题与话术参考。

两层结构（output/assets/content_assets.json）：
  cards   wid → 单作品精华卡（LLM 逐条浓缩：钩子/观点/金句/结构/话术）
  accounts platform:uid → 账号资产（选题清单 + 高光话术 + 可复用框架，从 cards 归并）
"""
from __future__ import annotations

import json
from pathlib import Path

from ..llm import chat_json

ROOT = Path(__file__).resolve().parent.parent.parent
CORPUS = ROOT / "output" / "corpus"
ASSETS_FILE = ROOT / "output" / "assets" / "content_assets.json"

CARD_SYSTEM = (
    "你是短视频内容拆解专家。把一条作品的完整文字稿浓缩成结构化精华卡，"
    "保留可复用的内容素材（原文金句必须摘抄原文），供后续创作参考。输出严格 JSON。"
)
CARD_PROMPT = """浓缩以下作品文字稿：

标题：{title}
平台：{platform}｜点赞：{likes}
文字稿：
{transcript}

输出 JSON，字段：
- logline: 一句话概括这条视频讲了什么（30字内）
- topic: 选题归类（如"复合占卜/财运预测/选组互动"）
- hook: 开场钩子（摘抄原文前 2 句 + 一句点评它为什么抓人）
- key_points: 核心内容点数组，3-6条（每条一句话，按视频推进顺序）
- golden_lines: 原文金句数组，2-5条（必须逐字摘抄，标注大致时间点）
- structure: 内容推进结构（如"选组引导→第一组解读→转折→收尾引导"，标注各段大意）
- phrases: 有复用价值的话术模板数组，2-4条（如"如果你选的是第一组，那么…"——从原文提取句式）
- reusable: 这条内容里可迁移到新作品的素材点，1-3条"""

MERGE_SYSTEM = (
    "你是内容资产库管理员。把一个账号的多张作品精华卡归并成账号级内容资产，"
    "供后续创作时检索参考。金句/话术一律保留原文摘抄。输出严格 JSON。"
)
MERGE_PROMPT = """归并以下 {n} 张精华卡为账号「{nickname}」的内容资产：

{cards}

输出 JSON，字段：
- topic_inventory: 选题清单，按主题聚类（每类含: 主题名, 出现次数, 代表作wid数组, 该主题的典型切入角度）
- best_hooks: 全账号最佳开场钩子数组，5-8条（原文摘抄+出处wid）
- golden_lines_pool: 金句池，8-15条（原文摘抄+出处wid，按主题分组）
- phrase_templates: 话术模板库，5-10条（句式+出处wid，覆盖选组/转折/收尾等场景）
- proven_structures: 验证过的内容结构数组，2-4个（结构描述+使用该结构的代表作wid+平均互动水平）
- content_gaps: 从选题清单里看出的内容空白/可扩展方向，2-4条（供新选题参考）"""


def _transcript(wid_dir: Path, max_chars: int = 20000) -> str:
    f = wid_dir / "asr.json"
    if not f.exists():
        return ""
    try:
        rows = json.loads(f.read_text())
        return " ".join(r.get("text", "") for r in rows)[:max_chars]
    except Exception:
        return ""


def build_card(platform: str, wid: str, title: str = "",
               likes: int = 0, force: bool = False,
               store: dict | None = None) -> dict:
    """单作品精华卡。store 传入时增量写回。"""
    d = CORPUS / platform / wid
    tr = _transcript(d)
    if not tr:
        return {}
    if store is not None and wid in store.get("cards", {}) and not force:
        return store["cards"][wid]
    result = chat_json(
        CARD_PROMPT.format(title=title or "(无标题)", platform=platform,
                           likes=likes, transcript=tr),
        system=CARD_SYSTEM, max_tokens=2500)
    if not result:
        return {}
    result.update({"wid": wid, "platform": platform, "title": title, "likes": likes})
    if store is not None:
        store.setdefault("cards", {})[wid] = result
    return result


def build_account_assets(platform: str, uid: str, nickname: str = "",
                         force: bool = False, store: dict | None = None) -> dict:
    """账号内容资产 = 该账号全部精华卡的归并。"""
    import sqlite3
    con = sqlite3.connect(ROOT / "data" / "bench.db")
    rows = con.execute(
        "SELECT wid, title, likes FROM works WHERE platform=? AND uid=? ORDER BY likes DESC",
        (platform, uid)).fetchall()
    st = store if store is not None else _load()
    cards = []
    for wid, title, likes in rows:
        c = build_card(platform, wid, title or "", likes or 0, force=force, store=st)
        if c:
            cards.append(c)
            # 逐卡落盘（中断不丢）
            _save(st)
    if not cards:
        return {}
    # 归并（卡多时分批拼接防超长；每卡浓缩后 ~400 字，30 卡 ~12k 安全）
    blocks = []
    for c in cards:
        blocks.append(json.dumps({
            "wid": c["wid"], "title": c["title"], "likes": c["likes"],
            "logline": c.get("logline"), "topic": c.get("topic"),
            "hook": c.get("hook"), "key_points": c.get("key_points"),
            "golden_lines": c.get("golden_lines"), "structure": c.get("structure"),
            "phrases": c.get("phrases")}, ensure_ascii=False))
    merged = chat_json(
        MERGE_PROMPT.format(n=len(cards), nickname=nickname or uid,
                            cards="\n\n".join(blocks))
        + "\n\n注意：输出务必精炼。golden_lines_pool 每条金句一行不展开，"
          "topic_inventory 每类不超过 3 行，总输出控制在 4500 字以内。",
        system=MERGE_SYSTEM, max_tokens=8000)
    if not merged:
        return {}
    merged.update({
        "platform": platform, "uid": uid, "nickname": nickname,
        "card_count": len(cards),
        "card_wids": [c["wid"] for c in cards],
    })
    st.setdefault("accounts", {})[f"{platform}:{uid}"] = merged
    _save(st)
    return merged


def _load() -> dict:
    if ASSETS_FILE.exists():
        return json.loads(ASSETS_FILE.read_text())
    return {"cards": {}, "accounts": {}}


def _save(st: dict):
    ASSETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ASSETS_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2))


def build_all(platforms: list[str] | None = None, force_cards: bool = False) -> dict:
    """全账号：逐作品补卡 → 逐账号归并。增量（卡已存在即跳过）。"""
    import sqlite3
    con = sqlite3.connect(ROOT / "data" / "bench.db")
    st = _load()
    stats = {"cards_new": 0, "accounts_merged": 0, "failed": 0}
    for platform in (platforms or ["xhs", "douyin", "bilibili"]):
        for uid, nickname in con.execute(
                "SELECT uid, nickname FROM accounts WHERE platform=?", (platform,)):
            print(f"[{platform}] {nickname or uid}")
            before = len(st.get("cards", {}))
            r = build_account_assets(platform, uid, nickname or "",
                                     force=force_cards, store=st)
            if r:
                stats["accounts_merged"] += 1
                stats["cards_new"] += len(st.get("cards", {})) - before
                print(f"  ✅ {r['card_count']} 卡归并 | 选题 {len(r.get('topic_inventory', []))} 类"
                      f" | 金句 {len(r.get('golden_lines_pool', []))}")
            else:
                stats["failed"] += 1
                print("  ❌ 无文稿或归并失败")
    _save(st)
    return stats


def get_account_assets(platform: str, uid: str) -> dict:
    return _load().get("accounts", {}).get(f"{platform}:{uid}", {})


def get_card(wid: str) -> dict:
    return _load().get("cards", {}).get(wid, {})
