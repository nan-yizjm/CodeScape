"""检查 README 本地链接、目录、图像与示例资源；不访问网络、不读取用户数据。"""
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
ASSETS = Path(__file__).parent / 'assets'
SCREENSHOTS = {'workspace', 'source-evidence', 'search-notes', 'architecture-edit', 'compare', 'history-diff'}
DIAGRAMS = {'system-architecture', 'analysis-pipeline', 'version-lifecycle'}


def main():
    checked = 0
    toc_count = 0
    for path in [ROOT / 'README.md', Path(__file__).with_name('README.md')]:
        source = path.read_text(encoding='utf-8')
        assert not re.search(r'sk-[A-Za-z0-9]{12,}|C:[/\\]Users[/\\]|#nonce=', source), '文档存在敏感或机器专属内容'
        anchors = set(re.findall(r'<a id="([^"]+)"', source))
        if path == ROOT / 'README.md':
            toc_count = len(anchors)
            assert toc_count == 11
        for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', source):
            url = urlsplit(target)
            if url.scheme:
                assert url.scheme in {'https', 'http'}, target
                continue
            if not url.path:
                assert unquote(url.fragment) in anchors, f'目录不存在：{target}'
                continue
            destination = (path.parent / unquote(url.path)).resolve()
            destination.relative_to(ROOT)
            assert destination.is_file(), f'文件不存在：{destination.relative_to(ROOT)}'
            checked += 1
        assert not re.search(r'!\[\]\(', source), '图片缺少替代文本'
    for stem in SCREENSHOTS:
        raw = (ASSETS / f'{stem}.png').read_bytes()
        assert raw[:8] == b'\x89PNG\r\n\x1a\n'
        assert struct.unpack('>II', raw[16:24]) == (1600, 1060), stem
    for stem in DIAGRAMS:
        path = ASSETS / f'{stem}.svg'
        root = ET.parse(path).getroot()
        ns = '{http://www.w3.org/2000/svg}'
        assert root.find(ns + 'title') is not None and root.find(ns + 'desc') is not None
        assert root.attrib.get('viewBox') and root.attrib.get('aria-labelledby')
        assert not root.findall('.//' + ns + 'script') and not root.findall('.//' + ns + 'image')
        assert not re.search(r'https?://(?!www\.w3\.org/)', path.read_text(encoding='utf-8'))
    print(f'检查通过：{checked}个本地引用，{toc_count}项目录锚点，6张PNG，3张可访问SVG。')


if __name__ == '__main__':
    main()
