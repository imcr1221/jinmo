"""通用中文 TTS 生成器。放在网站根目录运行：python generate_audio.py

每个课程模块是一个子目录，必须包含 index.html。

朗读文本来源（取并集去重）：
  1. HTML/JS 中显式的 data-tts-text="..."、say("...")、speech: "..."、ttsText: "..."
  2. 页面内嵌清单：<script type="application/json" id="tts-extra">["…","…"]</script>
  3. 模块目录下的 tts-extra.json（顶层字符串数组）

页面运行时才拼接出来的句子，请写进 2 或 3，
且文本必须与运行时生成的字符串逐字节一致（标点、空格都不能差）。
"""
import asyncio
import hashlib
import html
import json
import re
from pathlib import Path

import edge_tts

VOICE = 'zh-CN-XiaoxiaoNeural'
RATE = '-25%'
ROOT = Path(__file__).resolve().parent
SEM = asyncio.Semaphore(3)


def normalize(text):
    return html.unescape(text).strip()


def load_extra_list(raw, module):
    """把一段 JSON 文本解析成字符串列表，非法内容只警告不中断。"""
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f'  ⚠️ {module.name}：额外清单不是合法 JSON（{exc}）')
        return []
    if not isinstance(items, list):
        print(f'  ⚠️ {module.name}：额外清单必须是字符串数组')
        return []
    return [s for s in items if isinstance(s, str)]


def collect_texts(source, module):
    texts = set()

    def add(s):
        s = normalize(s)
        if s and re.search(r'[\u3400-\u9fff]', s) and len(s) <= 160:
            texts.add(s)
            return True
        return False

    # 1) 源码中显式声明的朗读文本
    for match in re.finditer(r'data-tts-text\s*=\s*["\']([^"\']+)["\']', source):
        add(match.group(1))
    for match in re.finditer(r'\bsay\(\s*["\']([^"\']+)["\']\s*\)', source):
        add(match.group(1))
    for match in re.finditer(r'\b(?:speech|ttsText)\s*:\s*["\']([^"\']+)["\']', source):
        add(match.group(1))

    # 2) 页面内嵌清单
    for match in re.finditer(
        r'<script[^>]*\bid\s*=\s*["\']tts-extra["\'][^>]*>(.*?)</script>',
        source,
        re.S,
    ):
        for s in load_extra_list(match.group(1), module):
            if not add(s):
                print(f'  ⚠️ {module.name}：外置文本被过滤 → {s!r}')

    # 3) 模块目录下的 tts-extra.json
    extra_file = module / 'tts-extra.json'
    if extra_file.is_file():
        for s in load_extra_list(extra_file.read_text(encoding='utf-8'), module):
            if not add(s):
                print(f'  ⚠️ {module.name}：外置文本被过滤 → {s!r}')

    return sorted(texts)


async def make_audio(text, target):
    async with SEM:
        for attempt in range(3):
            temp = target.with_suffix('.part')
            try:
                await asyncio.wait_for(
                    edge_tts.Communicate(text, VOICE, rate=RATE).save(str(temp)),
                    timeout=35,
                )
                if temp.stat().st_size < 1000:
                    raise ValueError('音频文件异常小')
                temp.replace(target)
                return True
            except Exception as exc:
                temp.unlink(missing_ok=True)
                if attempt == 2:
                    print(f'  ❌ 生成失败：{text} ({exc})')
                else:
                    await asyncio.sleep(attempt + 1)
    return False


async def process_module(module):
    source = (module / 'index.html').read_text(encoding='utf-8')
    texts = collect_texts(source, module)
    if not texts:
        print(f'📂 {module.name}：未发现朗读文本，跳过')
        return

    folder = module / 'audio'
    folder.mkdir(exist_ok=True)

    manifest = {}
    jobs = []
    for text in texts:
        digest = hashlib.sha256((VOICE + '|' + RATE + '|' + text).encode()).hexdigest()[:20]
        filename = 'tts_' + digest + '.mp3'
        target = folder / filename
        if target.is_file() and target.stat().st_size >= 1000:
            manifest[text] = filename
        else:
            jobs.append((text, filename, target))

    print(f'📂 {module.name}：{len(texts)} 条，已有 {len(manifest)} 条，待生成 {len(jobs)} 条')

    results = await asyncio.gather(*(make_audio(t, p) for t, _, p in jobs))
    for (text, filename, _), ok in zip(jobs, results):
        if ok:
            manifest[text] = filename

    # 只引用成功的音频；旧 MP3 保留不删。
    output = 'window.TTS_AUDIO = ' + json.dumps(manifest, ensure_ascii=False, indent=2) + ';\n'
    (folder / 'tts-manifest.js').write_text(output, encoding='utf-8')
    (folder / 'tts-texts.json').write_text(
        json.dumps(texts, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )

    missing = [t for t in texts if t not in manifest]
    (folder / 'tts-missing.txt').write_text('\n'.join(missing), encoding='utf-8')

    print(f'  ✅ 成功 {len(manifest)} 条；失败 {len(missing)} 条')


async def main():
    modules = sorted(
        p for p in ROOT.iterdir()
        if p.is_dir() and not p.name.startswith('.') and (p / 'index.html').exists()
    )
    if not modules:
        print('未找到课程子目录，请将脚本放在网站根目录。')
        return
    for module in modules:
        await process_module(module)
    print('🎉 已完成所有课程的音频检查与增量生成')


if __name__ == '__main__':
    asyncio.run(main())