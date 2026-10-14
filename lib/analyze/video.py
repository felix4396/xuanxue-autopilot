"""video 拆解 — 取自 viral-video-analyzer 的关键链路：抽帧 + 本地 ASR + 帧目录结构。

不复刻其 Vision API 调用（该由 doctor / 配置的多模态模型执行），
本模块只产出分析所需的「原料包」：
    output/media/{platform}-{wid}/frames/*.jpg   抽帧（关键帧密度可调）
    output/media/{platform}-{wid}/asr.json       本地 faster-whisper 转写
    output/media/{platform}-{wid}/meta.json      视频元信息
外部多模态模型消费该目录做逐秒拆解 → 结论走 lib/analyze.assets 入库。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

# httpx2 对 NO_PROXY 中的 [::1] 解析有 bug（Invalid port），
# faster-whisper→huggingface_hub 走 httpx2，这里进程内安全清除。
for _k in ("NO_PROXY", "no_proxy"):
    os.environ.pop(_k, None)


class VideoPrep:
    def __init__(self, media_dir: str | Path = "output/media"):
        self.media_dir = Path(media_dir)

    def work_dir(self, platform: str, wid: str) -> Path:
        d = self.media_dir / f"{platform}-{wid}"
        (d / "frames").mkdir(parents=True, exist_ok=True)
        return d

    def extract_frames(self, video: Path, platform: str, wid: str, fps: float = 0.5) -> list[Path]:
        """FFmpeg 抽帧，默认每 2 秒 1 帧。返回帧文件列表（按时间序）。"""
        d = self.work_dir(platform, wid)
        out = subprocess.run(
            ["ffmpeg", "-y", "-i", str(video), "-vf", f"fps={fps}",
             str(d / "frames" / "f%05d.jpg")],
            capture_output=True, text=True, timeout=600,
        )
        if out.returncode != 0:
            raise RuntimeError(f"ffmpeg 抽帧失败: {out.stderr[-300:]}")
        return sorted((d / "frames").glob("f*.jpg"))

    def transcribe(self, video: Path, platform: str, wid: str, model_size: str = "small") -> Path:
        """faster-whisper 本地转写 → asr.json（[{start,end,text}]）。"""
        from faster_whisper import WhisperModel

        d = self.work_dir(platform, wid)
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(video), vad_filter=True)
        rows = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]
        out = d / "asr.json"
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return out


    # ---- 云端 ASR（阿里云百炼 qwen-audio-3.1-asr-flash-filetrans）----
    # 流程：audio.m4a → OSS 签名URL → 异步任务 → 结果转 asr.json → 清理
    # 凭据从 .env 读：OSS_ACCESS_KEY_ID/SECRET/BUCKET/ENDPOINT + DASHSCOPE_API_KEY

    _OSS_OBJ_PREFIX = "xuanxue-asr"

    def _env(self, key: str) -> str:
        import sys
        sys.path.insert(0, ".")
        val = os.environ.get(key)
        if not val:
            from dotenv import load_dotenv
            load_dotenv(".env")
            val = os.environ.get(key)
        if not val:
            raise RuntimeError(f"缺环境变量 {key}")
        return val

    def _oss_sign(self, method: str, obj: str, content_type: str = "", expires: int = 0) -> tuple:
        import hmac, hashlib, base64
        from email.utils import formatdate
        ak = self._env("OSS_ACCESS_KEY_ID")
        sk = self._env("OSS_ACCESS_KEY_SECRET")
        bucket = self._env("OSS_BUCKET")
        date = formatdate(usegmt=True)
        expires_hdr = str(expires) if expires else date  # PUT/DELETE 用 Date 头，GET 签名用 Expires
        ss = f"{method}\n\n{content_type}\n{date}\n/{bucket}/{obj}"
        sig = base64.b64encode(hmac.new(sk.encode(), ss.encode(), hashlib.sha1).digest()).decode()
        return date, f"OSS {ak}:{sig}"

    def _oss_put(self, obj: str, data: bytes, content_type: str):
        import urllib.request
        bucket = self._env("OSS_BUCKET")
        host = f"{bucket}.{self._env('OSS_ENDPOINT')}"
        date, auth = self._oss_sign("PUT", obj, content_type)
        req = urllib.request.Request(f"https://{host}/{obj}", data=data, method="PUT",
            headers={"Authorization": auth, "Date": date, "Content-Type": content_type})
        urllib.request.urlopen(req, timeout=120)

    def _oss_signed_get_url(self, obj: str, ttl: int = 3600) -> str:
        import hmac, hashlib, base64, time as _t
        from urllib.parse import quote
        ak = self._env("OSS_ACCESS_KEY_ID")
        sk = self._env("OSS_ACCESS_KEY_SECRET")
        bucket = self._env("OSS_BUCKET")
        host = f"{bucket}.{self._env('OSS_ENDPOINT')}"
        expires = int(_t.time()) + ttl
        ss = f"GET\n\n\n{expires}\n/{bucket}/{obj}"
        sig = base64.b64encode(hmac.new(sk.encode(), ss.encode(), hashlib.sha1).digest()).decode()
        return f"https://{host}/{quote(obj)}?OSSAccessKeyId={ak}&Expires={expires}&Signature={quote(sig)}"

    def _oss_delete(self, obj: str):
        import urllib.request
        bucket = self._env("OSS_BUCKET")
        host = f"{bucket}.{self._env('OSS_ENDPOINT')}"
        date, auth = self._oss_sign("DELETE", obj)
        req = urllib.request.Request(f"https://{host}/{obj}", method="DELETE",
            headers={"Authorization": auth, "Date": date})
        urllib.request.urlopen(req, timeout=30)

    def transcribe_cloud(self, video: Path, platform: str, wid: str) -> Path:
        """云端 filetrans 转写 → asr.json（句级 [{start,end,text}]，秒）。"""
        import time as _t
        import urllib.request
        import json as _json

        d = self.work_dir(platform, wid)
        audio = d / "audio.m4a"
        if not audio.exists():
            self.extract_audio(video, platform, wid)
        obj = f"{self._OSS_OBJ_PREFIX}/{platform}-{wid}.m4a"

        # 1) 上传 OSS（签名 URL 需公网可读，音频不算敏感）
        self._oss_put(obj, audio.read_bytes(), "audio/mp4")
        try:
            # 2) 提交异步任务
            ds = self._env("DASHSCOPE_API_KEY")
            payload = {"model": "qwen-audio-3.1-asr-flash-filetrans",
                       "input": {"file_url": self._oss_signed_get_url(obj)},
                       "parameters": {"channel_id": [0], "enable_itn": True, "enable_words": True}}
            req = urllib.request.Request(
                "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription",
                data=_json.dumps(payload).encode(),
                headers={"Authorization": f"Bearer {ds}", "Content-Type": "application/json",
                         "X-DashScope-Async": "enable"})
            task = _json.loads(urllib.request.urlopen(req, timeout=60).read())["output"]["task_id"]
            # 3) 轮询（实测 <10s，留 5 分钟余量）
            for _ in range(150):
                _t.sleep(2)
                d2 = _json.loads(urllib.request.urlopen(urllib.request.Request(
                    f"https://dashscope.aliyuncs.com/api/v1/tasks/{task}",
                    headers={"Authorization": f"Bearer {ds}"}), timeout=30).read())
                st = d2["output"]["task_status"]
                if st in ("SUCCEEDED", "FAILED"):
                    break
            if st != "SUCCEEDED":
                code = (d2["output"].get("code") or "")
                if code == "ASR_RESPONSE_HAVE_NO_WORDS":
                    # 无语音（纯BGM/静音）→ 写空文稿，标记 skip
                    out = d / "asr.json"
                    out.write_text(_json.dumps([], ensure_ascii=False), encoding="utf-8")
                    return out
                raise RuntimeError(f"filetrans {st}: {_json.dumps(d2, ensure_ascii=False)[:200]}")
            # 4) 下载结果 → asr.json（与本地 whisper 同构）
            turl = d2["output"]["transcription_url"]
            res = _json.loads(urllib.request.urlopen(turl, timeout=30).read())
            tr = res["transcripts"][0]
            rows = [{"start": s["begin_time"] / 1000, "end": s["end_time"] / 1000, "text": s["text"]}
                    for s in tr.get("sentences", [])]
            out = d / "asr.json"
            out.write_text(_json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            return out
        finally:
            try:
                self._oss_delete(obj)
            except Exception:
                pass

    def extract_audio(self, video: Path, platform: str, wid: str) -> Path:
        """提取音频轨 → audio.m4a（ASR 之外的原始音频存档）。"""
        d = self.work_dir(platform, wid)
        out = subprocess.run(
            ["ffmpeg", "-y", "-i", str(video), "-vn", "-acodec", "aac",
             "-b:a", "64k", str(d / "audio.m4a")],
            capture_output=True, text=True, timeout=300,
        )
        if out.returncode != 0:
            raise RuntimeError(f"ffmpeg 提音频失败: {out.stderr[-200:]}")
        return d / "audio.m4a"

    def meta(self, video: Path, platform: str, wid: str) -> Path:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(video)],
            capture_output=True, text=True, timeout=60,
        )
        d = self.work_dir(platform, wid)
        meta = d / "meta.json"
        meta.write_text(out.stdout if out.returncode == 0 else "{}", encoding="utf-8")
        return meta
