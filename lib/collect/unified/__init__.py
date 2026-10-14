"""统一账号级采集框架：所有平台共享同一契约。

契约（每个平台 adapter 必须实现）：
  resolve_uid(name_or_uid) -> str           平台内唯一 ID（结果缓存 accounts.extra）
  fetch_profile(uid) -> dict                主页 1 次：{nickname, followers, works:[{wid,title,likes,comments,shares,content_type,desc,ts,url}]}
  fetch_work(wid, token="") -> dict         作品详情 1 次：{title, desc, content_type, media_urls:[], cover, likes, comments, shares}
  download_media(wid, urls, dest) -> Path   媒体下载（视频/图集/音频，多 URL = 图集多图）
  fetch_comments(wid) -> list[dict]         评论（无实现返回 []）

页面开销契约：每账号每轮 = fetch_profile 1 次页面 + 每个缺口作品 1 次（详情页/下载页），零重复。
"""
from .base import UnifiedAccountCollector, Work
