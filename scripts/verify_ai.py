"""仅验证用户明确授权的三个预置样例，不接受任意目录参数。"""

import asyncio
import json
import uuid
from pathlib import Path

from codescape.analyzers import REPOSITORY, python_analysis
from codescape.database import now
from codescape.provider import Provider
from codescape.security import data_root

paths = ("input.py", "engine.py", "presentation.py")
files = {
    path: (REPOSITORY / "tests/fixtures/learning_project" / path).read_text(encoding="utf-8")
    for path in paths
}
analysis = python_analysis(files, str(uuid.uuid4()))
provider = Provider(data_root())
configured = provider.settings()
if configured["base_url"] != "https://api.deepseek.com" or configured["model"] != "deepseek-flash":
    raise SystemExit("当前接收端或模型不在本次样例联调授权范围内。")
architecture, usage = asyncio.run(provider.architecture(analysis, files))
report = {
    "verified_at": now(),
    "model": "deepseek-flash",
    "recipient": "https://api.deepseek.com",
    "authorized_source_paths": ["tests/fixtures/learning_project/" + path for path in paths],
    "usage": usage,
    "schema_validation": "通过",
    "architecture": architecture.model_dump(),
}
output = Path("docs/reports/m0")
output.mkdir(parents=True, exist_ok=True)
(output / "deepseek-validation.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(
    json.dumps(
        {"模型": "deepseek-flash", "校验": "通过", "模块数": len(architecture.modules), "用量": usage},
        ensure_ascii=False,
    )
)
