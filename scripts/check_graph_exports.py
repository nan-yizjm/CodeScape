"""核对实际浏览器导出的完整图；不操作浏览器、不访问模型。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from xml.etree import ElementTree as ET

repository = Path(__file__).resolve().parents[1]
downloads = Path.home() / "Downloads"
destination = repository / "output/graph-exports"
destination.mkdir(parents=True, exist_ok=True)
suffixes = {
    "50-sparse": (" (2)", " (3)"), "50-dense": (" (1)", " (1)"),
    "200-sparse": (" (1)", " (1)"), "200-dense": (" (2)", " (2)"),
    "1000-sparse": (" (1)", " (1)"), "1000-dense": (" (1)", " (2)"),
}
ns = {"svg": "http://www.w3.org/2000/svg"}
results = []
for scenario, (record_suffix, svg_suffix) in suffixes.items():
    record_path = downloads / f"图规模-{scenario}{record_suffix}.json"
    svg_path = downloads / f"人工图-{scenario}{svg_suffix}.svg"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["baseline"] is False and record["scenario"] == scenario
    count = record["expected_functions"]
    value = ET.parse(svg_path).getroot()
    nodes = {item.attrib["data-node-id"]: item for item in value.iter() if "data-node-id" in item.attrib}
    edges = [item for item in value.iter() if "data-relation-id" in item.attrib]
    assert set(nodes) == {"parent"} | {f"function-{index}" for index in range(count)}
    expected_edges = set()
    for index in range(count):
        for offset in range(1, (5 if scenario.endswith("dense") else 1) + 1):
            if index + offset < count:
                expected_edges.add(f"{index}:{index+offset}")
        if index % 50 == 0:
            expected_edges.add(f"{index}:{index}")
        if index % 20 == 0 and index + 1 < count:
            expected_edges.add(f"{index+1}:{index}")
    assert {item.attrib["data-relation-id"] for item in edges} == expected_edges
    assert len(edges) == len(expected_edges) == record["edges"]
    assert all(item.attrib.get("marker-end") == "url(#arrow)" for item in edges)
    for index in range(count):
        expected_label = (f"长中文函数名称用于验证布局导出与完整名称保留_{index}_function"
                          if index % 31 == 0 else f"function_{index}")
        assert nodes[f"function-{index}"].find("svg:title", ns).text == expected_label
    x, y, width, height = map(float, value.attrib["viewBox"].split())
    for node in nodes.values():
        rect = node.find("svg:rect", ns)
        nx, ny, nw, nh = (float(rect.attrib[key]) for key in ("x", "y", "width", "height"))
        assert x <= nx and y <= ny and nx + nw <= x + width and ny + nh <= y + height
    for source, target in ((record_path, f"{scenario}.json"), (svg_path, f"{scenario}.svg")):
        shutil.copyfile(source, destination / target)
    record["svg_validation"] = {"all_node_ids": True, "all_relation_ids": True,
        "arrowheads": True, "self_and_reverse_calls": True, "full_labels_in_titles": True,
        "all_nodes_inside_viewbox": True, "bytes": svg_path.stat().st_size}
    results.append(record)

baseline = []
for scenario in ("50-sparse", "200-dense"):
    value = json.loads((downloads / f"图规模-{scenario} (1).json").read_text(encoding="utf-8"))
    value.update(baseline=True, render_mode="全量挂载",
        record_note="基线页早期模式文案误标；按实际挂载数量和旧版 Canvas 源码校正。")
    baseline.append(value)
baseline.append({"scenario": "1000-dense", "nodes": 1001, "edges": 5055,
    "layout_ms": 2179, "first_paint_ms": 3608, "rendered_nodes": 1001,
    "rendered_edges": 5055, "max_main_thread_task_ms": 1188, "long_task_count": 4,
    "svg_ms": 16, "svg_bytes": 4570508, "baseline": True, "render_mode": "全量挂载",
    "record_note": "实际浏览器 footer 读取记录；同样已载入 React Flow 基础样式。"})
report = {"date": "2026-10-11", "baseline_canvas_commit": "2a1c67f",
    "environment": "Windows 11 AMD64，Codex 内置 Chromium，1280×720 浏览器视口",
    "boundary": "人工图形夹具，单次冷/热混合测量，仅用于回归检查，不是统计性能保证或真实 AI 质量评测。早期漏载基础样式的测量已排除。",
    "baseline": baseline, "optimized": results,
    "model_requests": 0, "target_code_executed": False,
    "screenshot": "output/playwright/P0千函数阅读尺寸.jpg"}
(repository / "docs/reports/p0/图规模与SVG验证.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"场景": len(results), "全量节点连线和标签": "通过", "模型请求": 0}, ensure_ascii=False))
