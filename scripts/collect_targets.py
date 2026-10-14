"""对标账号定向采集：每账号 近期10条 + 热度最高10条。

账号清单来自 accounts 表（extra.target=recent_10+top_10）。
平台路由：xhs（resolve uid → 笔记卡 → 详情批量）/ douyin / bilibili。
"近期10"从用户页/搜索取最新 N；"最热10"抓到 limit 条后按点赞排序补齐。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.bench.store import BenchStore


def upsert_xhs_work(db, wid, token, media, err, uid_hint=""):
    db.upsert_work("xhs", wid, uid=uid_hint, media_path=str(media) if media else "",
                   extra=json.dumps({"xsec_token": token, "err": err},
                                    ensure_ascii=False))


def main():
    ap_args = sys.argv[1:]
    db = BenchStore("data/bench.db")
    rows = db.list_accounts() if hasattr(db, "list_accounts") else []
    con = db.conn
    accounts = con.execute(
        "SELECT platform, uid, nickname, followers, extra FROM accounts "
        "ORDER BY platform, followers DESC").fetchall()
    # 只跑 works 数不足的账号（断点续跑）；xhs 账号间强制 60s 间隔（IP 频控恢复）
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-works", type=int, default=10)
    a = ap.parse_args()
    print(f"目标账号 {len(accounts)} 个")
    prev_platform = None
    _xhs_empty_streak = {}
    for p, uid, nick, fans, extra_s in accounts:
        have = con.execute(
            "SELECT count(*) FROM works WHERE platform=? AND uid=? AND media_path!=''",
            (p, uid)).fetchone()[0]
        if have >= a.min_works:
            print(f"=== [{p}] {uid} 已有 {have} works，跳过 ===")
            continue
        if p == "xhs" and prev_platform == "xhs":
            print("  [限速保护] xhs 账号间隔 75s…")
            time.sleep(75)
        prev_platform = p
        extra = json.loads(extra_s or "{}")
        # douyin profile 已成功且作品足量才跳过
        if p == "douyin" and fans and fans > 0:
            _n = db.conn.execute(
                "SELECT count(*) FROM works WHERE platform='douyin' AND uid=? AND media_path!=''",
                (uid,)).fetchone()[0]
            if _n >= 10:
                print(f"=== [{p}] {uid} 已有 profile+{_n} media，跳过 ===")
                continue
        # xhs 连续空轮熔断：上轮 0 卡的账号本轮跳过（风控中，等下轮自然重试）
        if p == "xhs" and _xhs_empty_streak.get(uid, 0) >= 2:
            print(f"=== [{p}] {uid} 连续 {_xhs_empty_streak[uid]} 轮空卡，熔断跳过 ===")
            continue
        print(f"\n=== [{p}] {uid} ({fans} 粉) ===")
        if p == "xhs":
            from lib.profiles import scrape_xhs_user_works, resolve_xhs_uid
            from lib.collect.xhs_search import XhsSearchAdapter
            ad = XhsSearchAdapter()
            # hex_uid 已缓存 → 直接用（省一次 resolve 搜索页）
            _hex = (extra.get("hex_uid") or "").strip()
            _rnick = extra.get("resolved_nick") or ""
            if _hex:
                prof = {"uid": _hex, "nickname": _rnick}
            else:
                prof = resolve_xhs_uid(ad, uid)
                for attempt in range(3):
                    if prof and prof.get("uid"):
                        break
                    wait_s = 120 * (attempt + 1)
                    print(f"  频控，等 {wait_s}s 重试（{attempt+1}/3）…")
                    time.sleep(wait_s)
                    prof = resolve_xhs_uid(ad, uid)
            if not prof or not prof.get("uid"):
                print("  resolve 失败（重试后），跳过")
                continue
            hex_uid = prof["uid"]
            nickname = prof.get("nickname") or ""
            db.upsert_account(p, uid, nickname=nickname or nick,
                              followers=prof.get("followers") or fans,
                              extra=json.dumps({**extra, "hex_uid": hex_uid,
                                                "resolved_nick": nickname},
                                               ensure_ascii=False))
            # 作品卡（走用户 tab 搜索 + 笔记卡收集；跳过 db 已有 media 的 wid）
            have_media = {row[0] for row in db.conn.execute(
                "SELECT wid FROM works WHERE platform='xhs' AND uid=? AND media_path!=''",
                (uid,)).fetchall()}
            if len(have_media) >= 15:
                print(f"  已有 {len(have_media)} media，跳过搜索")
                continue
            r = scrape_xhs_user_works(ad, uid, limit=25, skip_wids=have_media, prof=prof)
            if not isinstance(r, dict):
                print("  works 抓取失败，跳过")
                _xhs_empty_streak[uid] = _xhs_empty_streak.get(uid, 0) + 1
                continue
            works = r.get("works", [])
            ok = 0
            for w in works:
                # err 非空且无媒体 → 单条重试（批量竞态兜底）
                if w.get("err") and not w.get("media_path"):
                    try:
                        # download_media 内部重跑 fetch_note_detail（单条路径稳定）
                        out = ad.download_media(w["wid"], "",
                                                xsec_token=w.get("xsec_token", ""))
                        w["media_path"], w["err"] = str(out), ""
                    except Exception as e2:
                        w["err"] = f"retry_fail: {str(e2)[:30]}"
                if w.get("media_path") or not w.get("err"):
                    ok += 1
                db.upsert_work("xhs", w["wid"], uid=uid,
                               title=w.get("title", ""),
                               likes=w.get("likes", 0),
                               media_path=str(w["media_path"]) if w.get("media_path") else "",
                               extra=json.dumps({"xsec_token": w.get("xsec_token", ""),
                                                 "desc": w.get("desc", ""),
                                                 "err": w.get("err", "")},
                                                ensure_ascii=False))
            print(f"  {len(works)} 条卡，{ok} 条成功详情")
            _xhs_empty_streak[uid] = 0 if works else _xhs_empty_streak.get(uid, 0) + 1
        elif p == "douyin":
            from lib.collect.douyin_dom import DouyinDomAdapter
            from lib.profiles import fetch_douyin_profile
            ad = DouyinDomAdapter()
            _fresh_prof = False
            if fans and fans > 0:
                prof = {"followers": fans, "nickname": nick or uid}
            else:
                prof = fetch_douyin_profile(ad, uid)
                _fresh_prof = True
            if prof and prof.get("followers"):
                # 缓存 profile 用旧 extra（保留 sec_uid），新抓的才更新
                db.upsert_account(p, uid, nickname=prof.get("nickname", uid),
                                  followers=prof["followers"],
                                  extra=json.dumps({**extra, "profile": prof},
                                                   ensure_ascii=False) if _fresh_prof
                                  else extra_s)
                print(f"  profile ✅ 粉丝 {prof['followers']}")
            else:
                print(f"  profile 失败: {prof}")
            # 作品采集：sec_uid → user 页作品卡 → 逐条下载
            from lib.profiles import resolve_douyin_sec_uid, scrape_douyin_user_works, download_douyin_video
            # sec_uid 优先用缓存（extra.sec_uid），避免每轮重复搜索 resolve
            _cached_sec = (extra.get("sec_uid") or "").strip()
            have = db.conn.execute(
                "SELECT count(*) FROM works WHERE platform='douyin' AND uid=? AND media_path!=''",
                (uid,)).fetchone()[0]
            if have >= 10:
                print(f"  已有 {have} media，跳过作品采集")
            else:
                sec = {"sec_uid": _cached_sec} if _cached_sec else resolve_douyin_sec_uid(nick or uid)
                if sec and sec.get("sec_uid") and not _cached_sec:
                    db.upsert_account(p, uid, nickname=nick or uid, followers=fans or 0,
                                      extra=json.dumps({**extra, "sec_uid": sec["sec_uid"]},
                                                       ensure_ascii=False))
                if not sec:
                    print("  sec_uid 解析失败")
                else:
                    print(f"  sec_uid ✅ {sec['sec_uid'][:30]}…")
                    dws = scrape_douyin_user_works(sec["sec_uid"], limit=12)
                    print(f"  {len(dws)} 条作品卡")
                    okn = 0
                    dy_have = {row[0] for row in db.conn.execute(
                        "SELECT wid FROM works WHERE platform='douyin' AND uid=? AND media_path!=''",
                        (uid,)).fetchall()}
                    import pathlib as _pl2
                    for w in dws:
                        outp = f"output/media/douyin-{w['id']}.mp4"
                        if w["id"] in dy_have and _pl2.Path(outp).exists():
                            okn += 1
                            continue
                        try:
                            db.upsert_work("douyin", w["id"], uid=uid,
                                           extra=json.dumps({"title": w.get("title", ""),
                                                             "plays": w.get("plays", "")},
                                                            ensure_ascii=False))
                            download_douyin_video(w["id"], outp)
                            db.upsert_work("douyin", w["id"], uid=uid, media_path=outp,
                                           extra=json.dumps({"title": w.get("title", ""),
                                                             "plays": w.get("plays", "")},
                                                            ensure_ascii=False))
                            okn += 1
                            print(f"    {w['id']} ✅ {okn}")
                        except Exception as e3:
                            print(f"    {w['id']} ❌ {str(e3)[:40]}")
                    print(f"  下载 {okn}/{len(dws)}")
        elif p == "bilibili":
            from lib.profiles import resolve_bili_mid, scrape_bili_user_works
            prof = resolve_bili_mid(nick or uid)
            if not prof:
                print("  resolve 失败，跳过")
                continue
            con.execute("UPDATE accounts SET uid=?, nickname=?, followers=? WHERE platform='bilibili' AND uid=?",
                        (prof["mid"], prof["nickname"], prof["followers"], uid))
            con.commit()
            print(f"  mid={prof['mid']} {prof['nickname']} {prof['followers']}粉 {prof['videos']}视频")
            works = scrape_bili_user_works(prof["mid"], limit=25)
            ok = 0
            import subprocess as _sp
            import pathlib as _pl
            from lib.profiles import download_bili_video
            for w in works:
                out = _pl.Path(f"output/media/bilibili-{w['bv']}.mp4")
                if not out.exists():
                    try:
                        download_bili_video(w["bv"], str(out))
                    except Exception as e2:
                        print(f"    {w['bv']} 下载失败: {str(e2)[:40]}")
                        out = None
                if out and out.exists():
                    ok += 1
                db.upsert_work("bilibili", w["bv"], uid=prof["mid"],
                               title=w.get("title", ""),
                               extra=json.dumps({"date": w.get("date", ""),
                                                 "plays": w.get("plays", ""),
                                                 "duration": w.get("duration", "")},
                                                ensure_ascii=False),
                               media_path=str(out) if (out and out.exists()) else "")
            print(f"  {len(works)} 条视频，{ok} 条已下载")
        time.sleep(3)
    print("\n完成")


if __name__ == "__main__":
    main()
