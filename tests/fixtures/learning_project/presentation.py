"""结果呈现：建立图形并导出。"""

from engine import analyze_project


def build_graph(source):
    """取得分析结果并组织为图形数据。"""
    result = analyze_project(source)
    return {"title": "源码学习样例", "result": result}


def export_graph(source):
    """导出图形表示。"""
    graph = build_graph(source)
    return str(graph)
