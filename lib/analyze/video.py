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
