    # DEPLOY.md — 部署指南

    > 本文档说明如何在本机部署和运行 multi-agent-pipeline 系统。
    >
    > 自动生成于: delivery.py (W5-Q06)

    ---

    ## 重要前提

    **项目路径：** `C:\\tmp\\multi-agent-pipeline`

    **如果你已经在这个路径看到项目文件，说明项目已存在，不需要下载。**

    **注意：** 项目现在支持多种部署方式：
    - Windows: 使用 `setup.ps1` 和 `start.ps1`
    - Linux/macOS: 使用 `setup.sh` 和 `start.sh`
    - Docker: 使用 `docker-compose.yml`

    ---

    ## 环境要求

    - Windows 10/11 (Windows 部署)
    - Linux/macOS (Linux/macOS 部署)
    - Python 3.10+
    - Git（可选，用于 worktree 功能）
    - Docker 和 Docker Compose (Docker 部署)

    ---

    ## 安装步骤

    根据你的操作系统选择相应的安装方法：

    ### Windows 安装

    1. 确认 Python 已安装

    按 `Win + R`，输入 `cmd`，回车。

    输入：
    ```
    python --version
    ```

    看到 `Python 3.10+` 即可。如果报错，去 https://www.python.org/downloads 下载安装。

    2. 安装依赖

    在项目文件夹 `C:\\tmp\\multi-agent-pipeline` 中：

    按住 `Shift + 右键` → `在此处打开 PowerShell 窗口`。

    输入：
    ```powershell
    powershell -ExecutionPolicy Bypass -File setup.ps1
    ```

    等 1-3 分钟，看到"安装完成"即可。

    3. 验证环境

    ```powershell
    powershell -ExecutionPolicy Bypass -File verify-runtime.ps1
    ```

    全绿 = 环境就绪。

    ### Linux/macOS 安装

    1. 确认 Python 已安装

    打开终端，输入：
    ```bash
    python3 --version
    ```

    看到 `Python 3.10+` 即可。如果没有安装，使用系统包管理器安装。

    2. 安装依赖

    在项目目录 `C:\\tmp\\multi-agent-pipeline` 中：

    给安装脚本添加执行权限：
    ```bash
    chmod +x setup.sh
    ```

    运行安装脚本：
    ```bash
    ./setup.sh
    ```

    等待安装完成。

    3. 验证环境

    ```bash
    chmod +x verify-runtime.sh
    ./verify-runtime.sh
    ```

    全绿 = 环境就绪。

    ### Docker 安装

    1. 确保已安装 Docker 和 Docker Compose

    2. 在项目目录 `C:\\tmp\\multi-agent-pipeline` 中：

    构建并启动服务：
    ```bash
    docker-compose up --build
    ```

    或以后台模式运行：
    ```bash
    docker-compose up --build -d
    ```

    ---

    ## 启动系统

    根据你的安装方式选择相应的启动方法：

    ### Windows 启动

    ```powershell
    powershell -ExecutionPolicy Bypass -File start.ps1
    ```

    启动后进入命令行菜单，可以输入命令操作。

    ### Linux/macOS 启动

    给启动脚本添加执行权限：
    ```bash
    chmod +x start.sh
    ```

    运行启动脚本：
    ```bash
    ./start.sh
    ```

    启动后进入命令行菜单，可以输入命令操作。

    ### Docker 启动

    如果使用 Docker 部署，在后台运行的情况下，可以通过以下命令进入交互界面：
    ```bash
    docker exec -it multi-agent-pipeline bash
    ```

    **注意：** 这不是聊天界面。当前版本的交互方式是通过你正在使用的这个聊天窗口（Hermes Agent）。

    ---

    ## 使用方式

    ### 方式1：通过聊天窗口（当前方式）

    直接在这个对话中告诉 Hermes 你想做什么任务。

    示例：
    ```
    我想开发一个爬虫系统，需求是...
    ```

    Hermes 会自动分配任务给 AI 团队，并在关键节点问你确认。

    ### 方式2：通过命令行（start.ps1）

    启动后输入命令：

    | 命令 | 作用 |
    |------|------|
    | `status` | 查看状态仪表盘 |
| `init <项目名>` | 初始化新项目 |
| `check` | 检查当前 Phase 条件 |
| `advance` | 推进到下一 Phase |
| `rollback <phase>` | 回退到指定 Phase |
| `resume` | 从 checkpoint 恢复 |
| `report` | 生成报告 |
| `deploy` | 生成部署脚本 |
| `help` | 查看完整帮助 |
| `quit / exit` | 退出 |

    ---

    ## 项目文件说明

    ```
    C:\\tmp\\multi-agent-pipeline\  ← 项目根目录
    ├── src\                        ← 系统代码
    │   ├── pipeline.py             ← 主入口
    │   ├── adapters.py             ← AI 适配器
    │   ├── phase_checks.py         ← Phase 检查
    │   ├── delivery.py             ← 交付层
    │   └── ...                     ← 其他模块
    ├── tests\                      ← 测试代码
    ├── README.md                   ← 使用说明
    ├── DEPLOY.md                   ← 本文档
    ├── setup.ps1                   ← Windows 安装脚本
    ├── setup.sh                    ← Linux/macOS 安装脚本
    ├── start.ps1                   ← Windows 启动脚本
    ├── start.sh                    ← Linux/macOS 启动脚本
    ├── verify-runtime.ps1          ← Windows 验证脚本
    ├── verify-runtime.sh           ← Linux/macOS 验证脚本
    ├── docker-compose.yml          ← Docker Compose 配置
    ├── Dockerfile                  ← Docker 构建文件
    ├── requirements.txt            ← Python 依赖列表
    └── features.json               ← 功能规格
    ```

    ---

    ## 常见问题

    ### Q: 项目不在 C:\\tmp\\multi-agent-pipeline？

    A: 如果需要移动，复制整个文件夹到新位置，然后重新运行 setup.ps1。

    ### Q: 启动后没有聊天界面？

    A: 当前版本没有 Web UI。交互方式是通过你正在使用的这个聊天窗口（Hermes Agent）。

    ### Q: 如何开始一个新任务？

    A: 直接在这个对话中说"我想做 XXX"。

    ### Q: 如何查看项目进度？

    A: 说"查看状态"或使用 `pipeline.py status` 命令。

    ### Q: 代码在哪里？

    A: `C:\\tmp\\multi-agent-pipeline\src\` 目录下。

    ---

    ## 故障排查

    | 问题 | 现象 | 解决 |
    |------|------|------|
    | Python 未安装 | `python --version` 报错 | 安装 Python 3.10+ |
    | PowerShell 限制 | 无法运行脚本 | 加 `-ExecutionPolicy Bypass` |
    | 依赖安装失败 | setup.ps1 报错 | 手动运行 `pip install pyyaml pytest rich` |
    | 模块导入失败 | verify-runtime 失败 | 确认在项目根目录运行 |

    ---

    ## 联系支持

    如果按本文档步骤仍无法解决，请提供：

    1. `verify-runtime.ps1` 的完整输出
    2. Windows 版本（`Win + R` → `winver`）
    3. Python 版本（`python --version`）
    4. 具体报错信息

    ---

    **祝你使用顺利！** 🚀
