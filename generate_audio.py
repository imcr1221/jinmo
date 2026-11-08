"""通用中文 TTS 生成器。放在网站根目录运行：python generate_audio.py"""
import asyncio
import hashlib
import html
import json
import re
from pathlib import Path

import edge_tts

VOICE = 'zh-CN-XiaoxiaoNeural'
RATE = '-10%'
ROOT = Path(__file__).resolve().parent
SEM = asyncio.Semaphore(3)


def normalize(text):
    return html.unescape(text).strip()


def collect_texts(source, module):
    texts = set()
    def add(s):
        s = normalize(s)
        if s and re.search(r'[\u3400-\u9fff]', s) and len(s) <= 160:
            texts.add(s)

    # Explicit markup and simple JavaScript calls; avoid reading arbitrary HTML/CSS as speech.
    for match in re.finditer(r'data-tts-text\s*=\s*["\']([^"\']+)["\']', source):
        add(match.group(1))
    for match in re.finditer(r'\bsay\(\s*["\']([^"\']+)["\']\s*\)', source):
        add(match.group(1))
    for match in re.finditer(r'\b(?:speech|ttsText)\s*:\s*["\']([^"\']+)["\']', source):
        add(match.group(1))

    # The transport page creates speech dynamically from arrays and answer selections.
    # Add complete phrases, not isolated fragments of JavaScript.
    if module == '10transport':
        vehicles = ['马车','自行车','小汽车','出租车','公交车','火车','地铁','飞机','直升飞机','船','警车','救护车','消防车']
        riding = {'自行车'}
        specials = {'警车','救护车','消防车'}
        for v in vehicles:
            add(v)
            add(('这是' if v in specials else '我骑' if v in riding else '我坐') + v + '。')
        for v in ['自行车','小汽车','公交车','火车','飞机','船']:
            for p in ['学校','超市','游乐场','中国']:
                add(('我骑' if v == '自行车' else '我坐') + v + '去' + p + '。')
        for s in [
            '答对啦，你真棒！','再试一次吧！','点一点图片，听一听。',
            '小朋友迷路了，谁来帮忙？','老爷爷摔倒了，谁来帮忙？','房子着火了，谁来帮忙？',
            '警车来帮忙。','救护车来救人。','消防车来灭火。',
            '公交车大，小汽车小。','火车长，小汽车短。','大船大，小船小。',
            '红色小人亮了，停下来，等一等。',
            '绿色小人亮了，先看一看，确认安全再过马路。',
            '小汽车像瓢虫。','小汽车像苹果。','公交车像一个大箱子。',
            '公交车像大面包。','火车像毛毛虫。','火车像积木。',
            '飞机像小鸟。','飞机像纸飞机。','小船像香蕉。','小船像月亮。',
            '找一找，你看到了哪些交通工具？','哪里奇怪？',
        ]:
            add(s)
    return sorted(texts)


async def make_audio(text, target):
    async with SEM:
        for attempt in range(3):
            temp = target.with_suffix('.part')
            try:
                await asyncio.wait_for(edge_tts.Communicate(text, VOICE, rate=RATE).save(str(temp)), timeout=35)
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
    texts = collect_texts(source, module.name)
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
    # Never reference failed audio. Keep existing MP3s; do not delete old files.
    output = 'window.TTS_AUDIO = ' + json.dumps(manifest, ensure_ascii=False, indent=2) + ';\n'
    (folder / 'tts-manifest.js').write_text(output, encoding='utf-8')
    (folder / 'tts-texts.json').write_text(json.dumps(texts, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    missing = [t for t in texts if t not in manifest]
    (folder / 'tts-missing.txt').write_text('\n'.join(missing), encoding='utf-8')
    print(f'  ✅ 成功 {len(manifest)} 条；失败 {len(missing)} 条')


async def main():
    modules = sorted(p for p in ROOT.iterdir() if p.is_dir() and not p.name.startswith('.') and (p / 'index.html').exists())
    if not modules:
        print('未找到课程子目录，请将脚本放在网站根目录。')
        return
    for module in modules:
        await process_module(module)
    print('🎉 已完成所有课程的音频检查与增量生成')


if __name__ == '__main__':
    asyncio.run(main())
