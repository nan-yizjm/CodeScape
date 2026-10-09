"""分析引擎：提取语法并汇总结果。"""

from input import read_config


def analyze_project(source):
    """使用配置完成语法提取和结果汇总。"""
    configuration = read_config(source)
    parsed = parse_source(source)
    return summarize(parsed, configuration)


def parse_source(source):
    """遍历语法结构；两处调用保留为同一边的两条证据。"""
    first = walk_syntax(source)
    second = walk_syntax(source)
    return first + second


def walk_syntax(node):
    """递归访问子节点。"""
    children = node.get("children", [])
    if not children:
        return 1
    total = 0
    for child in children:
        total += walk_syntax(child)
    return total


def summarize(parsed, configuration):
    """组合语法统计和配置结果。"""
    return {"nodes": parsed, "configured": configuration}
