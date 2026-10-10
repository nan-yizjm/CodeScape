# CodeScape · 源境

从语义模块进入函数图，再沿真实调用证据阅读只读源码，记录自己的学习状态。

当前版本是 **v0.1.0 / M0 开发预览**，用于验证分析和交互方案。尚未完成全部 P0，也不是安装包。完整需求见 [PRD](docs/CodeScape_PRD.md)，实施基线见 [开发设计](docs/CodeScape_Development_Design.md)，本轮结果见 [开发与验收报告](docs/reports/m0/开发与验收报告.md)。

## 本轮可用功能

- 本地目录或公开 Git 默认分支导入，先预览范围再确认；创建独立源码快照，记录 Commit、包含/排除规则和文件哈希。
- Python AST、TypeScript Compiler API、C/C++ Tree-sitter 与 libclang 基础静态解析。
- 纵向模块架构、父子模块嵌套容器、大型项目源码域抽象与逐层展开、全部模块切换，以及包含直接跨模块调用者和被调用者的函数画布。
- 递归边、同向调用证据聚合、全屏探索、阅读尺寸与自适应缩放。
- 单侧详情面板、只读全文源码、行范围高亮、当前层级完整 SVG 导出。
- SQLite 保存学习状态和各层级画布位置，修订号防止同时操作互相覆盖。
- DeepSeek / 兼容 Chat Completions 接口；Windows DPAPI 保存密钥；模型 JSON 和架构实体归属校验。
- 发送项目源码前，明确展示接收端、模型和范围；无源码连接测试独立提供。

预置样例包含三个文件、八个函数。它的初始模块和概览为**人工预置**，调用关系是实际静态提取。真实 DeepSeek 联调结果另存于 [验证记录](docs/reports/m0/deepseek-validation.json)，不会拿人工模块冒充 AI 分析结果。

## Windows 开发运行

需要 Windows、Python 3.12、Node.js 22.12+ 或 24，以及 uv。当前主机已准备好依赖和前端构建，可直接运行 `Start-CodeScape.ps1`。

首次从仓库准备环境：

```powershell
uv sync --frozen
npm ci
npm run build
```

启动：

```powershell
.\Start-CodeScape.ps1
```

启动脚本使用项目自己的 `.venv`，本地服务监听 `127.0.0.1:8123`，自动打开包含一次性启动凭证的浏览器入口。Ctrl+C 停止服务。端口占用时可用 `-Port 8124`。不要将开发服务暴露到外网。

数据默认位于 `%LOCALAPPDATA%\CodeScape`，不进入 Git。可以在启动前设置 `CODESCAPE_DATA_DIR` 使用独立开发数据目录。程序不执行被导入项目的代码、安装、构建或测试。

配置 AI 时点击右上角设置，填写服务地址、模型和 API Key。默认接收端为 `https://api.deepseek.com`；本轮验证模型为 `deepseek-flash`。密钥不写入 `.env`，不进入命令行参数或日志。其他服务需提供兼容的 `/chat/completions`、JSON 对象输出和当前模型参数。

## 开发检查

```powershell
.venv\Scripts\ruff.exe check server scripts tests
.venv\Scripts\pytest.exe -q
npm run typecheck
npm test
npm run build
npm audit
```

测试运行 CodeScape 本身；`tests/fixtures` 中的项目只作为解析输入，禁止执行。真实模型测试与离线固定响应测试分开记录，不在常规测试中产生 API 费用。`scripts/verify_ai.py` 固定仅发送已授权的三个预置样例，运行前仍应确认当前授权与服务配置。

## 当前边界

当前 AI 分析会在上下文接近保护上限时按源码文件和行范围拆分为受控批次，逐批校验函数归属后合并架构；跨批次的模块边界会明确标记为需要人工复核，并保存逐批检查点以便失败后继续。尚未实现多阶段受控补读、函数讲解、完整任务队列和版本发布迁移。C/C++ 暂不读取编译数据库或引入系统头文件；无法在快照内验证的包含会降级为语法索引。Python/TypeScript 动态分派、复杂名称改写、C++ 宏配置与跨语言接口不作完整覆盖承诺。

目前仅支持不带 BOM 的 UTF-8；项目删除与模块学习状态、完整会话历史、Windows 安装包和代表性仓库最终验收尚待后续实施。函数层可恢复当前外层模块的同级模块。P0 实施进度与验收证据见 [P0 开发记录](docs/reports/p0/开发与验收记录.md)。
