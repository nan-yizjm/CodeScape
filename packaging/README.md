# Windows x64 构建与验证

这些脚本只处理 CodeScape 应用自有代码和构建资源，不执行被分析仓库的安装、编译或测试。

1. 在 Windows x64 开发环境使用 `uv sync --locked --group packaging`、`npm ci`，再运行 `npm run build`。
2. 使用 `.venv/Scripts/python.exe scripts/prepare_windows_runtime.py` 下载固定版本资源。下载地址、上游校验来源和 SHA-256 记录在 `runtime_sources.json`；校验失败禁止使用。
3. 校验 Inno Setup 下载文件的 Authenticode 状态为 `Valid`，签名主体为 Pyrsys B.V.，再将官方构建工具按用户安装到 `.runtime/packaging/inno`。构建工具不包含在产品运行库内。
4. 使用 `.venv/Scripts/python.exe scripts/build_windows.py` 生成预发布安装包、便携 ZIP、构建信息、组件许可证和文件哈希清单，输出到 `output/releases/<构建号>/`。
5. 使用 `.venv/Scripts/python.exe scripts/check_windows_bundle.py` 验证独立程序；安装后可用 `--executable <安装目录>/CodeScape.exe` 验证同一组功能。

运行包包含 Python 3.12、Node、TypeScript Compiler API、Git、libclang、Tree-sitter、数据库迁移和前端静态资源。冻结程序只使用随包 Node/Git，不从用户 PATH 搜索替代组件。工作进程沿用同一个可执行程序，通过隐藏参数和标准输入输出通信；不会启动目标仓库脚本。

安装器按当前用户安装，使用中文向导，提供开始菜单、可选桌面快捷方式和卸载。升级与卸载先通过受保护的本地通信请求应用保存并正常退出；超时或数据库校验失败则停止操作。应用数据与安装目录分离，卸载保留学习数据、快照和 DPAPI 密钥，原始源码始终不被删除。

0.1.1 起程序与安装器使用 `codescape.ico`，快捷方式继承程序图标；应用版本来自 `pyproject.toml`，构建信息记录源码 Commit。验证新版本时可使用 `check_windows_bundle.py --report <本轮报告.json>` 和 `check_windows_installer.py --previous-build <旧制品目录> --report <本轮报告.json>`，分别留档，不覆盖 P0 报告。

当前构建未签名，不宣称获得 SmartScreen 信任。开发机上的独立运行和安装验证不能替代干净 Windows 10/11 虚拟机验证；未完成的项目必须写入验收记录。x86 与原生 ARM64 不列入已支持平台。

参考：[PyInstaller 运行时](https://pyinstaller.org/en/stable/runtime-information.html)、[Inno Setup 官方下载](https://jrsoftware.org/isdl.php)、[Git for Windows 固定发布](https://github.com/git-for-windows/git/releases/tag/v2.56.0.windows.2)、[Node 固定发布校验](https://nodejs.org/dist/v24.16.0/SHASUMS256.txt)。
