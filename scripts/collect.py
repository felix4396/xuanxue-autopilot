"""统一采集 CLI。

场景：
  1. 关键词批量下载:  collect.py kw-download --platforms xhs --keywords "塔罗牌,八字" --limit 10
  2. 用户作品批量下载: collect.py user-download --platform xhs --uid <user_id> --limit 20
  3. 用户信息采集:    collect.py profiles --platform xhs --uids <id1,id2> / --from-db xhs
采集完自动重导语料库。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.bench.store import BenchStore
from lib.corpus import export_all

DB = "data/bench.db"


def _upsert_media(db, platform, wid, out):
    if out:
        if isinstance(out, list):
            out = out[0]
        db.upsert_work(platform, wid, media_path=str(out))


def cmd_kw_download(args):
    """场景1：关键词搜索 → 下载（含媒体），全平台路由。"""
    db = BenchStore(DB)
    kws = [k.strip() for k in args.keywords.split(",") if k.strip()]
    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    for p in platforms:
        for kw in kws:
            print(f"\n[{p}] 关键词下载: {kw}")
            if p == "xhs":
                from lib.collect.xhs_search import XhsSearchAdapter
                ad = XhsSearchAdapter()
                results = ad.search(kw, count=args.limit)
                items = [(r["wid"], r.get("xsec_token", "")) for r in results
                         if r.get("xsec_token")]
                print(f"  搜索 {len(results)} 条，带 token {len(items)} 条，批量下载…")
                for wid, out, err in ad.fetch_note_detail_batch(items):
                    if out:
                        db.upsert_work("xhs", wid, media_path=str(out), extra=json.dumps({"source_kw": kw}, ensure_ascii=False))
                        print(f"  ✅ {wid[:20]} → {out.name}")
                    elif err:
                        print(f"  {wid[:20]} 跳过: {err}")
            elif p in ("bilibili", "youtube"):
                from scripts.backfill_media import backfill_bilibili, backfill_youtube
                fn = backfill_bilibili if p == "bilibili" else backfill_youtube
                # 先入库搜索结果再补抓
                print("  （bilibili/youtube 走 harvest 搜索 + yt-dlp 下载）")
            elif p == "douyin":
                from lib.collect.douyin_dom import DouyinDomAdapter
                ad = DouyinDomAdapter()
                for r in ad.search(kw, count=args.limit):
                    try:
                        out = ad.download_media(r["wid"], "")
                        db.upsert_work("douyin", r["wid"], media_path=str(out))
                        print(f"  ✅ {r['wid']} → {out.name}")
                    except Exception as e:
                        print(f"  {r['wid']} 失败: {str(e)[:50]}")
    print("\n[corpus] 重导:", export_all())


def cmd_user_download(args):
    """场景2：用户主页 → 拉作品列表 → 批量下载。"""
    db = BenchStore(DB)
    p = args.platform
    print(f"\n[{p}] 用户作品下载: {args.uid}（上限 {args.limit}）")
    if p == "xhs":
        import time as _t
        import re as _re
        import urllib.parse as _up
        from lib.collect.xhs_search import XhsSearchAdapter
        ad = XhsSearchAdapter()
        # 正路（逆向结论）：用户页直链会被 SSR 降级成空壳；
        # 搜索用户昵称 → 结果页作品卡片 a[href] 自带 xsec_token → 批量详情下载
        ad.limiter.wait("xhs")
        ws, tab = ad._open(
            "https://www.xiaohongshu.com/search_result?keyword="
            + _up.quote(args.uid), wait=12.0)
        try:
            _t.sleep(4)
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
                "expression": """(() => {
                  const links = [...document.querySelectorAll('a[href*="xsec_token"]')];
                  return JSON.stringify(links.map(a => {
                    const m = a.getAttribute('href').match(/(?:explore|discovery\/item|search_result)\/([0-9a-f]{24})\?xsec_token=([^&]+)/);
                    return m ? {id: m[1], token: m[2]} : null;
                  }).filter(Boolean));
                })()""",
                "returnByValue": True}}))
            m = json.loads(ws.recv())
            val = m.get('result', {}).get('result', {}).get('value')
            raw = json.loads(val) if val else []
        finally:
            ws.close()
        # 去重
        seen = set()
        items = []
        for x in raw[:args.limit * 2]:
            if x["id"] not in seen:
                seen.add(x["id"])
                items.append((x["id"], x["token"]))
        items = items[:args.limit]
        print(f"  搜索到该用户作品卡 {len(items)} 条（带 token），批量下载…")
        for wid, out, err in ad.fetch_note_detail_batch(items):
            if out:
                db.upsert_work("xhs", wid, uid=args.uid, media_path=str(out))
                print(f"  ✅ {wid[:20]} → {out.name}")
            else:
                print(f"  {wid[:20]} 跳过: {err or 'no_media'}")
    elif p == "douyin":
        from lib.collect.douyin_dom import DouyinDomAdapter
        ad = DouyinDomAdapter()
        for w in ad.fetch_works(args.uid, limit=args.limit):
            try:
                out = ad.download_media(w["wid"], "")
                db.upsert_work("douyin", w["wid"], uid=args.uid, media_path=str(out))
                print(f"  ✅ {w['wid']} → {out.name}")
            except Exception as e:
                print(f"  {w['wid']} 失败: {str(e)[:50]}")
    elif p == "x":
        from lib.collect.x_dom import XDomAdapter
        ad = XDomAdapter()
        handle = args.uid.lstrip("@")
        for w in ad.fetch_works(handle, limit=args.limit):
            try:
                out = ad.download_media(w["wid"], w.get("media_url", ""), handle=handle)
                db.upsert_work("x", w["wid"], uid=handle, media_path=str(out))
                print(f"  ✅ {w['wid']} → {out.name}")
            except Exception as e:
                db.upsert_work("x", w["wid"], uid=handle, title=w.get("title", ""))
                print(f"  {w['wid']} 无媒体: {str(e)[:40]}")
    elif p in ("bilibili", "youtube"):
        print("  （bilibili/youtube 用户下载走 harvest --platforms 的账号维度，或 yt-dlp channel URL）")
    print("\n[corpus] 重导:", export_all())


def cmd_profiles(args):
    """场景3：用户信息（昵称/粉丝数/作品数/desc）→ accounts 表。"""
    from lib.profiles import fetch_xhs_profile, fetch_x_profile, fetch_douyin_profile
    db = BenchStore(DB)
    p = args.platform
    if args.from_db:
        # 从库里该平台的 accounts 取所有 uid
        import sqlite3
        con = sqlite3.connect(DB)
        uids = [r[0] for r in con.execute(
            "SELECT uid FROM accounts WHERE platform=?", (p,))]
        print(f"[{p}] 从库取 {len(uids)} 个账号")
    else:
        uids = [u.strip() for u in args.uids.split(",") if u.strip()]
    fn = {"xhs": fetch_xhs_profile, "x": fetch_x_profile,
          "douyin": fetch_douyin_profile}.get(p)
    if not fn:
        print(f"  {p} 暂无 profile 采集（bilibili/youtube 可从频道页取）")
        return
    ad = None
    if p == "xhs":
        from lib.collect.xhs_search import XhsSearchAdapter
        ad = XhsSearchAdapter()
    elif p == "x":
        from lib.collect.x_dom import XDomAdapter
        ad = XDomAdapter()
    elif p == "douyin":
        from lib.collect.douyin_dom import DouyinDomAdapter
        ad = DouyinDomAdapter()
    for uid in uids:
        try:
            prof = fn(ad, uid)
            if prof:
                db.upsert_account(p, uid, nickname=prof.get("nickname", ""),
                                  followers=prof.get("followers"),
                                  extra=json.dumps(prof, ensure_ascii=False))
                print(f"  ✅ {uid[:24]} | {prof.get('nickname','')[:12]} | 粉丝 {prof.get('followers')}")
            else:
                print(f"  {uid[:24]} 无 profile 数据")
        except Exception as e:
            print(f"  {uid[:24]} 失败: {str(e)[:50]}")


def main():
    ap = argparse.ArgumentParser(description="统一采集 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a1 = sub.add_parser("kw-download", help="场景1: 关键词批量下载")
    a1.add_argument("--platforms", default="xhs")
    a1.add_argument("--keywords", required=True)
    a1.add_argument("--limit", type=int, default=20)
    a1.set_defaults(func=cmd_kw_download)

    a2 = sub.add_parser("user-download", help="场景2: 用户作品批量下载")
    a2.add_argument("--platform", required=True)
    a2.add_argument("--uid", required=True, help="用户 id/handle")
    a2.add_argument("--limit", type=int, default=20)
    a2.set_defaults(func=cmd_user_download)

    a3 = sub.add_parser("profiles", help="场景3: 用户信息采集")
    a3.add_argument("--platform", required=True)
    a3.add_argument("--uids", default="", help="逗号分隔 uid")
    a3.add_argument("--from-db", action="store_true", help="从 accounts 表取全部 uid")
    a3.set_defaults(func=cmd_profiles)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
