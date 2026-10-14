#!/usr/bin/env python3
"""清扫 CDP Chrome 中所有平台采集 tab（xhs/douyin/x/weibo/bilibili）。

用法：.venv/bin/python scripts/cleanup_tabs.py [--dry]
"""
import argparse
import requests

CDP = "http://127.0.0.1:9222"
KEYS = ("xiaohongshu.com", "douyin.com", "x.com", "twitter.com", "weibo.cn", "bilibili.com")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    tabs = requests.get(f"{CDP}/json/list", timeout=8).json()
    n = 0
    for t in tabs:
        if not (isinstance(t, dict) and t.get("type") == "page"):
            continue
        u = t.get("url") or ""
        if any(k in u for k in KEYS):
            print(("DRY " if args.dry else "KILL"), t["id"][:8], u[:70])
            if not args.dry:
                try:
                    requests.get(f"{CDP}/json/close/{t['id']}", timeout=5)
                except Exception:
                    pass
            n += 1
    print(f"共 {n} 个平台 tab")


if __name__ == "__main__":
    main()
