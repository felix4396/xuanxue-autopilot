"""账号驱动的内容生成：画像定风格 + 内容资产供选题话术 → 直接生成成稿。

流程（全自动，无需人写初稿）：
  1. get_profile → 人设/语气/标题公式/gen_brief
  2. get_account_assets → 选题清单/金句池/话术模板/内容空白
  3. LLM 先选一个选题（优先 content_gaps 差异化方向），再按该账号
     结构与话术生成成稿（标题/开头/正文/cta），禁 AI 腔词表
  4. 产出 Draft → output/drafts/{platform}-{ts}.md
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..analyze.profile import get_profile, PROFILES_FILE
from ..analyze.content_assets import get_account_assets, ASSETS_FILE


def load_persona() -> dict:
    """创作者个人固定信息（名字/人设/口头禅/固定结尾）。缺失字段为空串。"""
    if PERSONA_FILE.exists():
        return json.loads(PERSONA_FILE.read_text())
    return {"name": "", "identity": {}, "voice": {}, "fixed_elements": {}}
from ..llm import chat_json
from .deaigen import AI_FLAVOR_WORDS

ROOT = Path(__file__).resolve().parent.parent.parent
DRAFTS_DIR = ROOT / "output" / "drafts"
PERSONA_FILE = ROOT / "output" / "assets" / "persona.json"

PLAN_SYSTEM = (
    "你是{creator_name}的选题策划。基于对标账号的内容资产和画像，挑选/设计{creator_name}的下一个选题。"
    "注意：学的是对标账号的结构和套路，但内容和人设必须是{creator_name}自己的。输出严格 JSON。"
)
PLAN_PROMPT = """账号画像简报：
{gen_brief}

内容资产：
- 选题清单：{topics}
- 内容空白（可扩展方向）：{gaps}
- 高互动代表作：{refs}

输出 JSON：
- topic: 选定选题（一句话）
- angle: 切入角度（为什么这么做会像这个账号的作品）
- reference_wids: 参考的代表作 wid 数组（1-3 个）
- structure_hint: 预期内容结构（用该账号验证过的结构）"""

GEN_SYSTEM = (
    "你是「{creator_name}」。用对标账号的内容结构和套路写一条新作品，"
    "但身份、自称、口头禅、结尾全部用创作者自己的（见个人资产）。"
    "对标账号的标志性词汇（如对方的昵称/自称）禁止照搬。"
    "硬性规则：禁用 AI 腔词汇（{banned}）；口语短句。输出严格 JSON。"
)
GEN_PROMPT = """{gen_brief}

【创作者本人资产（必须遵守）】
- 名字/自称：{creator_name}
- 人设关键词：{p_persona}
- 口头禅：{p_catchphrases}
- 固定开场仪式：{p_opening}
- 固定结尾/落款：{p_ending}
- 对观众称呼：{p_address}
- 语气要求：{p_tone}

本次选题：{topic}
切入角度：{angle}
预期结构：{structure_hint}

参考该账号的代表性写法：
- 开场钩子范例：{hook_examples}
- 金句风格范例：{golden_examples}
- 话术句式范例：{phrase_examples}
{transcript_ref}

输出 JSON：
- titles: 3 个候选标题（xhs ≤20 字，含一贯标题句式）
- opening: 开场 2 句（用该账号的开场套路）
- body: 正文段落数组（{n} 段，每段 2-4 句口语，用账号口头禅和话术）
- cta: 结尾引导（1 句，用该账号的 cta 套路）"""


def generate(platform: str, uid: str, store_dir: Path | None = None) -> dict:
    """全自动生成一条新内容。返回 draft dict 并落盘 md。"""
    profile = get_profile(platform, uid)
    assets = get_account_assets(platform, uid)
    if not profile or not assets:
        return {"error": f"画像或内容资产缺失: {platform}:{uid}"}
    nickname = profile.get("nickname") or uid
    persona = load_persona()
    creator = persona.get("name") or "(未填写 persona.json 的 name)"
    banned = "、".join(AI_FLAVOR_WORDS[:12])

    # ---- 1. 选题规划 ----
    plan = chat_json(
        PLAN_PROMPT.format(
            gen_brief=profile.get("gen_brief", ""),
            topics=json.dumps(assets.get("topic_inventory", []), ensure_ascii=False)[:3000],
            gaps=json.dumps(assets.get("content_gaps", []), ensure_ascii=False)[:1500],
            refs=json.dumps(assets.get("proven_structures", [])[:2], ensure_ascii=False)[:800]),
        system=PLAN_SYSTEM.format(creator_name=creator), max_tokens=800)
    if not plan:
        return {"error": "选题规划失败"}

    # ---- 2. 取参考素材（few-shot 原文）----
    ref_wids = plan.get("reference_wids") or assets.get("card_wids", [])[:2]
    hook_ex, golden_ex, phrase_ex, tr_ref = [], [], [], ""
    for wid in ref_wids[:2]:
        card = assets.get("cards", {}).get(wid) or _card_from_store(wid)
        if card:
            if card.get("hook"):
                hook_ex.append(str(card["hook"])[:120])
            if card.get("golden_lines"):
                golden_ex += [str(g)[:60] for g in card["golden_lines"][:2]]
            if card.get("phrases"):
                phrase_ex += [str(p)[:80] for p in card["phrases"][:2]]
        tr = _transcript_text(platform, wid, 1200)
        if tr and not tr_ref:
            tr_ref = f"\n代表作品完整语感参考（{wid}）：\n{tr}"

    # ---- 3. 生成成稿 ----
    n_body = 4 if platform != "douyin" else 3
    gen = chat_json(
        GEN_PROMPT.format(
            gen_brief=profile.get("gen_brief", ""),
            creator_name=creator,
            p_persona="、".join(persona.get("identity", {}).get("persona_keywords", [])) or "（未填）",
            p_catchphrases="、".join(persona.get("voice", {}).get("catchphrases", [])) or "（未填）",
            p_opening=persona.get("voice", {}).get("opening_self_intro") or persona.get("fixed_elements", {}).get("opening_ritual") or "（未填）",
            p_ending=persona.get("fixed_elements", {}).get("ending_cta") or persona.get("fixed_elements", {}).get("sign_off") or "（未填）",
            p_address=persona.get("audience", {}).get("address") or "（未填）",
            p_tone=persona.get("voice", {}).get("tone") or "（未填）",
            topic=plan.get("topic", ""), angle=plan.get("angle", ""),
            structure_hint=plan.get("structure_hint", ""),
            hook_examples="；".join(hook_ex)[:600] or "（无）",
            golden_examples="；".join(golden_ex)[:600] or "（无）",
            phrase_examples="；".join(phrase_ex)[:600] or "（无）",
            transcript_ref=tr_ref, n=n_body,
            banned=banned),
        system=GEN_SYSTEM.format(creator_name=creator, banned=banned),
        max_tokens=2500, temperature=0.85)
    if not gen:
        return {"error": "生成失败"}

    # ---- 4. 落盘 ----
    ts = int(time.time())
    out_dir = store_dir or DRAFTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    record = {"platform": platform, "uid": uid, "reference_account": nickname,
              "creator": creator, "plan": plan, "draft": gen, "generated_at": ts}
    (out_dir / f"{platform}-{uid[:12]}-{ts}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2))
    md = [f"# {gen['titles'][0]}", ""]
    md += [f"> 备选标题：{' / '.join(gen['titles'][1:])}", ""]
    md += [gen["opening"], ""]
    body = gen["body"] if isinstance(gen["body"], list) else [gen["body"]]
    md += [str(seg) for seg in body if not isinstance(seg, (list, dict))]
    md += ["", f"**{gen['cta']}**", "",
           f"---", f"*选题：{plan.get('topic')}｜参考：{','.join(ref_wids[:3])}｜"
           f"画像+内容资产驱动生成*"]
    md_path = out_dir / f"{platform}-{uid[:12]}-{ts}.md"
    md_path.write_text("\n".join(md))
    record["md_path"] = str(md_path)
    return record


def _card_from_store(wid: str) -> dict | None:
    if ASSETS_FILE.exists():
        return json.loads(ASSETS_FILE.read_text()).get("cards", {}).get(wid)
    return None


def _transcript_text(platform: str, wid: str, limit: int = 1200) -> str:
    f = ROOT / "output" / "corpus" / platform / wid / "asr.json"
    if not f.exists():
        return ""
    try:
        rows = json.loads(f.read_text())
        return " ".join(r.get("text", "") for r in rows)[:limit]
    except Exception:
        return ""

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="账号驱动内容生成")
    ap.add_argument("--platform", required=True)
    ap.add_argument("--uid", required=True)
    a = ap.parse_args()
    r = generate(a.platform, a.uid)
    if "error" in r:
        print("❌", r["error"])
    else:
        d = r["draft"]
        print("选题:", r["plan"]["topic"])
        for t in d["titles"]:
            print("标题:", t)
        print("\n" + d["opening"] + "\n")
        for seg in d["body"] if isinstance(d["body"], list) else [d["body"]]:
            print(str(seg) + "\n")
        print("CTA:", d["cta"])
        print("\n成稿:", r["md_path"])
