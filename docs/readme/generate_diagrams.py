"""生成 README 的三张原创 SVG；只使用 Python 标准库，不读取项目源码或用户数据。"""
from html import escape
from pathlib import Path

OUTPUT = Path(__file__).parent / 'assets'
INK = '#142033'
MUTED = '#46566d'
LINE = '#cbd5e1'
BLUE = '#eef3ff'
PURPLE = '#f3efff'
GREEN = '#edf8f4'
AMBER = '#fff5e8'


def label(x, y, value, size=18, weight=400, color=INK):
    return f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{color}">{escape(value)}</text>'


def rect(x, y, width, height, fill='#ffffff', radius=18, dashed=False):
    dash = ' stroke-dasharray="7 6"' if dashed else ''
    return f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}" fill="{fill}" stroke="{LINE}"{dash}/>'


def card(x, y, width, height, title, lines, fill='#ffffff'):
    result = rect(x, y, width, height, fill)
    result += label(x + 24, y + 38, title, 23, 650)
    for index, line in enumerate(lines):
        result += label(x + 24, y + 70 + index * 27, line, 18, color=MUTED)
    return result


def arrow(path, dashed=False):
    dash = ' stroke-dasharray="6 5"' if dashed else ''
    return f'<path d="{path}" fill="none" stroke="#64748b" stroke-width="2" stroke-linejoin="round" marker-end="url(#arrow)"{dash}/>'


def document(name, title, description, height, content):
    header = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="{height}" viewBox="0 0 1120 {height}" role="img" aria-labelledby="title description">
<title id="title">{escape(title)}</title>
<desc id="description">{escape(description)}</desc>
<defs><marker id="arrow" markerWidth="9" markerHeight="9" refX="7" refY="4.5" orient="auto" markerUnits="userSpaceOnUse"><path d="M 0 0 L 8 4.5 L 0 9 Z" fill="#64748b"/></marker></defs>
<g font-family="Segoe UI, Microsoft YaHei, PingFang SC, sans-serif">
<rect width="1120" height="{height}" rx="24" fill="#f8fafc"/>
'''
    header += label(40, 53, title, 30, 700)
    header += label(40, 87, description, 17, color=MUTED)
    (OUTPUT / name).write_text(header + content + '</g>\n</svg>\n', encoding='utf-8')


def architecture():
    result = rect(24, 120, 812, 650, '#ffffff', dashed=True)
    result += rect(860, 120, 236, 650, '#f1f5f9', dashed=True)
    result += label(44, 148, '本机 · Windows 用户工作区', 15, 650, MUTED)
    result += label(882, 151, '外部接收端', 16, 650, MUTED)
    result += label(882, 230, '仅授权后发送', 18, 650)
    result += label(882, 260, '范围内源码与上下文', 17, color=MUTED)
    result += card(64, 170, 732, 92, '浏览器工作区 · React', ['架构探索 / 只读源码 / 搜索笔记 / 双项目学习'], BLUE)
    result += card(64, 304, 732, 94, '本地服务 · FastAPI', ['会话与 CSRF / 导入与任务 / 版本与修订 / 数据访问'])
    result += card(64, 454, 346, 118, '解析工作进程', ['Python AST / TypeScript', 'Tree-sitter / libclang'], GREEN)
    result += card(450, 454, 346, 118, 'AI 工作进程', ['分批 / 补读 / 校验', '请求与用量 / 检查点'], PURPLE)
    result += card(882, 454, 192, 118, '模型服务', ['DeepSeek', '或兼容接口'], AMBER)
    result += rect(64, 618, 732, 128, '#f8fafc')
    result += label(84, 645, '本地持久化 · 数据与程序安装目录分离', 16, 650, MUTED)
    stores = [(84, 200, 'SQLite', '项目 / 任务 / 工作区'),
              (304, 246, '快照与检查点', '带版本的只读源码'),
              (570, 206, 'DPAPI 凭据', '当前 Windows 用户')]
    for x, width, title, note in stores:
        result += rect(x, 662, width, 65, '#ffffff', 12)
        result += label(x + 16, 688, title, 18, 650)
        result += label(x + 16, 712, note, 15, color=MUTED)
    result += arrow('M 430 262 V 304')
    result += label(446, 291, '同源 HTTP', 14, color=MUTED)
    result += arrow('M 430 398 V 426 H 237 V 454')
    result += arrow('M 430 426 H 623 V 454')
    result += arrow('M 64 350 H 44 V 682 H 64')
    result += arrow('M 796 513 H 882')
    result += label(811, 500, 'HTTPS', 14, color=MUTED)
    result += label(40, 810, '普通阅读、搜索、笔记与阅读指引在本地完成；AI 分析由配置的接收端处理。', 18)
    result += label(40, 840, '箭头表示调用或数据依赖，不表示被分析项目的执行顺序。', 16, color=MUTED)
    document('system-architecture.svg', 'CodeScape 系统架构', '本地工作区与外部模型之间的职责、进程和数据边界。', 870, result)


def pipeline():
    result = ''
    phases = [
        (64, 145, '01  预览导入范围', ['选择目录或公开 Git 来源', '确认包含、排除与语言范围'], BLUE),
        (596, 145, '02  固定只读快照', ['文件哈希 / 规则 / 来源 Commit', '后续阅读绑定该版本'], GREEN),
        (596, 350, '03  建立静态索引', ['实体、真实作用域、调用与位置', '保留诊断和未确认范围'], GREEN),
        (64, 350, '04  授权后的分批分析', ['在预算内归纳职责与函数讲解', '有限补读快照 / 保存批次检查点'], PURPLE),
        (64, 555, '05  校验与全局归并', ['结构、引用、归属及调用证据', '整理模块层级与简明项目概览'], AMBER),
        (596, 555, '06  事务发布候选', ['成功后切换当前版本并迁移学习', '保留旧结果、历史和待复核项'], BLUE),
    ]
    for x, y, title, lines, fill in phases:
        result += card(x, y, 460, 145, title, lines, fill)
    result += arrow('M 524 217 H 596')
    result += arrow('M 826 290 V 350')
    result += arrow('M 596 422 H 524')
    result += arrow('M 294 495 V 555')
    result += arrow('M 524 627 H 596')
    result += rect(64, 750, 992, 70, '#ffffff')
    result += label(88, 792, '请求或校验失败 → 保留检查点并暂停 / 修复，不发布未通过的结果。', 20, 600)
    result += label(40, 858, '本图描述 CodeScape 的分析方法，不是被分析程序的运行流程图。', 17, color=MUTED)
    document('analysis-pipeline.svg', '从源码到可探索架构', '静态事实、AI 归纳和发布校验逐步衔接，所有解释回到版本快照核对。', 890, result)


def versions():
    result = label(64, 139, '当前已发布版本', 16, 650, MUTED)
    result += label(596, 139, '重新分析与新版本', 16, 650, MUTED)
    result += card(64, 165, 460, 140, '原始 AI 结果 + 源码快照', ['不可被人工编辑覆盖', '旧版本始终读取自己的快照'], BLUE)
    result += card(64, 360, 460, 140, '独立人工修改层', ['改名 / 说明 / 拆分 / 合并 / 归属', '预览保存 / 撤销重做 / 恢复原始'], PURPLE)
    result += card(64, 555, 460, 145, '有效架构与学习工作区', ['原始结果叠加人工修改', '依赖按源码证据重新聚合'], GREEN)
    result += card(596, 165, 460, 140, '独立候选 · 手动重扫', ['新快照与分批检查点', '失败或取消，继续阅读旧版本'], AMBER)
    result += card(596, 360, 460, 140, '校验通过 · 事务发布', ['可靠身份迁移学习、笔记和修改', '实现变化及匹配冲突明确提示'], GREEN)
    result += card(596, 555, 460, 145, '当前新版 + 可读历史', ['最近 5 个成功版本，固定版额外保留', '阅读中及待复核来源受保护'], BLUE)
    result += arrow('M 524 235 H 596')
    result += label(537, 222, '重扫', 15, color=MUTED)
    result += arrow('M 294 305 V 360')
    result += label(310, 341, '人工调整', 15, color=MUTED)
    result += arrow('M 294 500 V 555')
    result += arrow('M 64 235 H 42 V 625 H 64')
    result += arrow('M 826 305 V 360')
    result += arrow('M 826 500 V 555')
    result += arrow('M 524 625 H 596', dashed=True)
    result += label(530, 613, '迁移', 15, color=MUTED)
    result += rect(64, 755, 992, 92, '#ffffff')
    result += label(88, 790, '无法可靠匹配时保留正文、原版本与待复核项，不强行对应同名实体。', 20, 600)
    result += label(88, 820, '学习状态按实现指纹严格迁移；笔记关联与学习状态分别处理。', 18, color=MUTED)
    document('version-lifecycle.svg', '版本更新与人工修正', '人工编辑保留原始结果；新分析成功后才发布，可靠关联延续，不确定部分交给复核。', 880, result)


if __name__ == '__main__':
    OUTPUT.mkdir(parents=True, exist_ok=True)
    architecture()
    pipeline()
    versions()
    print('已生成三张 README SVG 图示。')
