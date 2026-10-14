"""Receive the API key on stdin; never place it in argv or repository files."""

import json
import sys

from codescape.security import CredentialStore, data_root

root = data_root()
if sys.stdin.isatty():
    import msvcrt

    characters = []
    print("等待输入密钥，输入内容不显示。", flush=True)
    while True:
        character = msvcrt.getwch()
        if character in ("\r", "\n"):
            break
        characters.append(character)
    supplied_key = "".join(characters)
else:
    supplied_key = sys.stdin.readline().strip()
CredentialStore(root).save(supplied_key)
del supplied_key
root.mkdir(parents=True, exist_ok=True)
(root / "provider.json").write_text(
    json.dumps({"base_url": "https://api.deepseek.com", "model": "deepseek-flash"}), encoding="utf-8"
)
print("密钥已使用当前用户 Windows DPAPI 加密，模型已配置。")
