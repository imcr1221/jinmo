import asyncio
import hashlib
import json
import os
from pathlib import Path

import edge_tts

VOICE = "zh-CN-XiaoyiNeural"
RATE = "-25%"
PITCH = "+8Hz"
OUT = Path("audio")  # 保持与原 GitHub Actions 工作流一致
FORCE_REGENERATE = True  # 本次强制覆盖旧 MP3；确认更新后可改为 False

OUT.mkdir(parents=True, exist_ok=True)
texts = json.loads(Path("tts-texts.json").read_text(encoding="utf-8"))

async def synth(text: str, path: Path, retries: int = 3) -> bool:
    temp = path.with_suffix(".tmp.mp3")
    for attempt in range(retries):
        try:
            temp.unlink(missing_ok=True)
            await edge_tts.Communicate(text, voice=VOICE, rate=RATE, pitch=PITCH).save(str(temp))
            if temp.is_file() and temp.stat().st_size > 0:
                os.replace(temp, path)  # 原文件名直接更新，避免出现半成品
                return True
        except Exception as exc:
            print(f"失败 {attempt + 1}/{retries}: {text}: {exc}")
        finally:
            temp.unlink(missing_ok=True)
        await asyncio.sleep(2 * (attempt + 1))
    return False

async def main():
    manifest = {}
    failed = []
    generated = 0
    for text in dict.fromkeys(texts):
        # 与最初的网页音频文件名保持一致
        name = hashlib.md5(text.encode("utf-8")).hexdigest()[:12] + ".mp3"
        path = OUT / name
        if not FORCE_REGENERATE and path.is_file() and path.stat().st_size > 0:
            manifest[text] = f"audio/{name}"
            continue
        if await synth(text, path):
            manifest[text] = f"audio/{name}"
            generated += 1
            print(f"已更新: {name} | {text}")
        else:
            failed.append(text)
            # 失败时保留旧文件，但不要把未成功更新说成成功
            if path.is_file() and path.stat().st_size > 0:
                manifest[text] = f"audio/{name}"
    (OUT / "tts-manifest.js").write_text(
        "window.TTS_AUDIO=" + json.dumps(manifest, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    print(f"本次成功生成/更新 {generated} 条；失败 {len(failed)} 条；音频目录: {OUT.resolve()}")
    if failed:
        print("失败句子:", failed)

asyncio.run(main())
