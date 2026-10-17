# README 图示与截图

本目录服务于仓库首页，不参与程序运行或 Windows 打包。资源基于 CodeScape 0.2.0，制作于 2026-10-17。

## 界面截图

六张 PNG 均从 1600×1060px 浏览器视口直接拍摄；未绘制虚假界面，未使用真实用户数据。使用应用自带人工预置样例，在独立应用数据目录中创建第二个对照样例及一条人工历史记录，执行真实界面操作后等待转场和保存状态稳定。

| 文件 | 内容 |
| --- | --- |
| [workspace.png](assets/workspace.png) | 完整工作区、三模块全景及确认调用。 |
| [source-evidence.png](assets/source-evidence.png) | 分析引擎、函数依赖及版本快照的源码高亮。 |
| [search-notes.png](assets/search-notes.png) | 搜索定位后的函数、个人笔记与保存反馈。 |
| [architecture-edit.png](assets/architecture-edit.png) | 人工改名和说明的预览校验，未保存为原始架构。 |
| [compare.png](assets/compare.png) | 左侧全景、右侧函数详情，各侧独立操作。 |
| [history-diff.png](assets/history-diff.png) | 人工构造历史版本的名称及归属差异，仅演示界面。 |

样例只有3个文件、8个可见函数。模块与概览是人工预置，调用来自真实静态提取；历史差异不是一次真实模型重分析。未执行样例代码，模型请求为零。窗口不含地址栏、一次性启动入口、配置密钥或私人目录。

以后重拍应创建独立的 `CODESCAPE_DATA_DIR`，先体验应用自带样例；第二项目、笔记、学习状态和历史只在该测试目录构造。使用 Playwright CLI 操作实际按钮，等待布局、转场和保存完成再截图；先检查图片，再替换资源。不要从用户日常数据目录拍摄或为了截图触发模型请求。

## 原创 SVG

| 图示 | 表达内容 |
| --- | --- |
| [system-architecture.svg](assets/system-architecture.svg) | 本机工作区、服务、工作进程、本地存储与外部模型之间的职责边界。 |
| [analysis-pipeline.svg](assets/analysis-pipeline.svg) | 源码范围、快照、静态索引、授权分析、校验与发布方法。 |
| [version-lifecycle.svg](assets/version-lifecycle.svg) | 原始结果、人工层、独立候选及可靠迁移。 |

图示是说明 CodeScape 本身的设计图，不是从模型获得的架构结果，也不是被分析程序的运行流程图。箭头表达调用、数据依赖或图中标明的处理关系；不表示目标程序的实际执行顺序。

使用标准库生成，无字体下载和外部图片依赖：

```powershell
python docs/readme/generate_diagrams.py
```

修改生成脚本后再生成 SVG；正文节点、配色和连接路径均在脚本中显式声明。SVG 带标题、说明和独立 `viewBox`，可在浏览器打开、放大和维护。重生成后应检查文字是否超出卡片、连线是否遮挡，以及 README 在窄屏的缩放效果。

## 文档核验

可重复的本地引用、PNG尺寸与SVG结构检查：

```powershell
python docs/readme/check_readme.py
```

本轮仅修改 README 与本目录资源，不更改应用行为、依赖、数据库、安装包或固定版本。README 以0.2.0代码、发布附件及已记录验收为依据：

- 下载文件名与 GitHub 正式发布附件逐项一致。
- 本地链接和页内目录有效；六张截图、三张 SVG 均存在且有替代文本。
- SVG 通过 XML 解析与浏览器渲染，文字边界和图形显示人工复核。
- 使用 GitHub Markdown API 渲染正文，结合本地资源检查桌面与窄屏。预览样式只用于本地检查，不进入 README 或产品。
- 本页所述0.2.0测试数量来自既有验收记录，不把文档制作作为新增模型验证。仓库既有 CI 继续检查文档提交对应的源代码状态。
