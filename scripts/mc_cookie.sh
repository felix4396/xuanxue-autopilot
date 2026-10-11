#!/bin/bash
# 用法: mc_cookie.sh dy|bili|xhs
set -e
export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
PLATFORM="$1"
PROJ="$(cd "$(dirname "$0")/.." && pwd)"
F="$PROJ/data/browser-cookies/${PLATFORM}.txt"
[ "$PLATFORM" = "dy" ] && F="$PROJ/data/browser-cookies/douyin.txt"
[ -f "$F" ] || { echo "missing $F"; exit 1; }
CFG="$PROJ/vendor/MediaCrawler/config/base_config.py"
COOKIE=$(cat "$F")
python3 -c "
import sys, re
cookie = sys.argv[1]
path = sys.argv[2]
text = open(path).read()
text = re.sub(r\"COOKIES = '[^']*'\", 'COOKIES = ' + repr(cookie), text, count=1)
open(path, 'w').write(text)
print('injected $PLATFORM cookie')" "$COOKIE" "$CFG"
