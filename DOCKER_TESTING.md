# Docker 算法测试

## 使用

进入一个顶层算法目录，点击“算法测试”。如果没有 `.tar` / `.tar.gz` / `.tgz` Docker save 镜像包，弹出“没有找到docker文件”。不执行 Dockerfile 构建，也不执行没有 Docker 镜像的本机 Python/CARLA 工程。

- 最左侧保留算法目录导航，点击算法可返回对应文件管理页。中间按层级显示运行文件，复用文件管理页的文件图标、名称样式和预览功能，隐藏 DOC/DOCX/MD/PDF/PPT 等说明文档；支持目录分页。
- GPU 默认全部可见，可选择某一张物理 GPU 或不使用 GPU。选择 GPU 1 时通过 UUID 指定该物理设备，容器内部编号可能从 0 开始。多任务可以共享同一张 GPU，但并不隔离显存或算力配额。
- “查看运行命令”弹窗只显示命令，不显示来源、注释或说明文字。占位任务名在实际启动时替换成唯一 ID；镜像已存在时跳过 load。实际执行命令也会出现在终端。
- “开始”加载镜像并通过 `docker create` + `docker start --attach --interactive` 运行；等价于交互式 `docker run`，但能够更可靠地处理启动/停止竞争。
- 终端使用本地 xterm.js 与服务端 PTY，约 300ms 增量读取输出，支持键盘、粘贴、Ctrl+C 和窗口尺寸调整。输入只发送给容器进程，不会变成宿主机命令。
- 文件列表在运行期间每 3 秒刷新。输出保存到当前算法的真实 output 目录，运行不会自动清空旧结果。
- “清空 output”需要用户确认，删除目录内的文件、子目录和软链接本身，不跟随软链接。保留 output 目录。删除不可恢复；成功、失败及已删除项目数量写入 `operations.log`。遇到容器生成的 root 所有、且服务用户无权删除的子目录时会报错，需要管理员先调整该目录权限。
- “停止”只停止、清理本任务创建且标签匹配的容器，不操作机器上的其他 Docker 服务。

## 并发与后台任务

服务端原子校验同一算法目录只能存在一个任务，跨设备、跨标签页同时点击也不能重复启动；已有任务时弹出“需要先停止在运行的算法”。包括加载、创建、运行、停止中的任务均占用名额。运行中的算法目录禁止上传、新建文件夹、删除和清空 output，但正常查看、下载不受影响。

默认最多 6 个不同算法并行。修改上限后重新启动服务：

```bash
ALGO_MAX_TESTS=6 ./start.sh
```

关闭测试页、标签页或浏览器不会停止容器。重新登录后，可在任意设备的右上角“后台任务”中重新连接；同一账号的任务可以相互查看和停止。右上角保留最近 50 个完成任务，每个任务的内存终端缓冲上限约 2 MiB，超过后截断最早输出。

任务状态不是跨服务重启持久化的数据库。正常停止网页服务会停止其管理的任务；不要在任务运行时重启、强制杀死网页服务或 Docker daemon。异常强杀后应由管理员核对带 `kt1.file-manager` 标签的遗留容器。不要对同一数据根目录同时部署多个服务进程来绕过互斥和并发限制。

## 运行配置来源

1. 挂载当前算法的 input 到 `/app/data/input:ro`，output 到 `/app/data/output`，与提交说明及成功实测记录一致。
2. 每次开始读取当前 `params.json` 和 `<算法名>测试说明.docx`。支持 `gpus`、`shm-size`、`platform`、`memory`、`cpus`、`pids-limit`。DOCX 中明确的允许参数补充缺失配置；与当前 params.json 冲突时拒绝启动，要求核对。
3. `verified_runs.json` 保存人工核对后纳入版本控制的历史成功配置及相对记录路径，不包含历史输入输出或原文文档。优先使用较新的退出码 0 且实际产生输出的记录，排除 `failed-attempts`。历史缺失参数可补充当前配置；与当前明确配置不同时使用当前配置并在终端提示。
4. 历史记录中的输入输出绝对路径、容器名和 GPU 编号不会照搬；网页 GPU 选择始终优先。容器入口参数只作为 Docker 镜像后的 argv 传入，历史环境变量不能覆盖 GPU 可见性。
5. 通过镜像摘要而不是可变 tag 启动，兼容经典 Docker save 和 Docker 29 OCI/containerd 归档。必要时 load 后解析真实 ID，并恢复 load 前已有镜像标签，避免影响其他服务。

运行默认使用 Docker bridge 网络、`no-new-privileges`、Docker 标准默认 capabilities、进程/线程数上限 512（可通过允许的 params.json 调整至最多 4096）。仅允许固定 input/output 挂载。不接受特权、host 网络、任意卷或宿主 Shell 参数。不要添加 `--cap-drop ALL`：它移除了标准 `DAC_OVERRIDE` 能力，使容器 root 无法写入服务用户所有的 0775 output 目录或覆盖 0644 结果文件。兼容默认 Docker 权限，不通过自动 chmod/chown 或清空结果规避问题；非 root 镜像仍须匹配 output 的实际访问权限。

### 2026-09-14 文档与历史记录核对

- 核对 131 份当前测试说明，其中 121 份包含 Docker run 命令。
- 106–118 共 13 个算法的 DOCX 指定 `--platform linux/amd64`，已自动补充。
- 95、96、125、129 的 DOCX 指定 `--shm-size=16g`，而根目录配置未给出，已支持从文档补充；125、129 当前没有镜像包，仍不能从网页启动。
- 从分批记录的 189 份候选元数据中，选出覆盖 119 个算法的成功配置（包括 run.json、run_summary.json 和 run_summary_compact.json）。
- 81–83 号成功命令额外指定容器内 Python 入口及 epochs/users 等小规模测试参数；89 号使用 EPOCHS、NUM_USERS 等环境变量。网页采用这些已核对配置；页面不显示“已参考…成功记录”，终端不显示成功记录路径，只保留必要的配置差异/小规模测试提醒。这不是完整训练或全量验收的保证。
- 90 号采用 2026-09-10 `default-no-env` 成功记录，不套用旧版临时环境变量。
- 所有当前含镜像包的 119 个目录均通过了只读命令配置预检。91、92、101–104、125–127、129–131 共 12 个目录当前没有 Docker 镜像包；131 的 DOCX 指定本机 CARLA/Python 流程，不能用标准 Docker 方式启动。
- 这里只复用成功命令配置，不声称重新跑完全部 119 个算法；修改镜像、输入或运行环境后仍需要重新验证。

重新审计历史元数据（只输出 JSON，不运行容器、不修改历史记录）：

```bash
python3 scripts/audit_docker_records.py /home/ly/jisuanjishu_docker/kt_1/docs_processing_records
```

## 实测与回归

无 Docker 的自动测试：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
node --check static/app.js
node --check static/runner.js
bash -n start.sh
```

真实测试需要本地 `algo1-4-j-76:v1` 镜像、两张可用 NVIDIA GPU 和相应 Toolkit，以及项目测试虚拟环境中的 Playwright/Chromium。测试在 `test-output/docker-e2e-*` 下建立独立数据，复制 76 号默认输入和镜像包，不修改生产 unzips。不会停止现有业务容器。

```bash
docker build --network=none --pull=false -t kt1-file-manager-smoke:20260914 tests/docker_fixture
PLAYWRIGHT_BROWSERS_PATH="$PWD/.test-browsers" PYTHONDONTWRITEBYTECODE=1 .test-venv/bin/python tests/smoke_docker_browser.py
```

已实测：浏览器交互输入返回实时输出、物理 GPU 1 单卡可见、5 个任务共享 GPU 0、总计 6 个容器并发、第 7 个拒绝、同算法重复启动拒绝、关闭标签页后另一设备重连、停止清理、正常/异常退出码、清空 output 确认与实际删除。浏览器未出现 JavaScript 错误。

真实 `algo1-4-j-76` 使用默认输入运行成功，退出码 0，生成 `TOHB_merge_map.png`、`TOHB_merge_process.png`、`TOHB_metrics_comparison.png`。当前测试脚本会保存报告 JSON、终端结果、输出文件和桌面/手机截图到对应 `test-output` 子目录，均不上传 GitHub。

### output 权限回归（2026-09-14）

使用真实 6、10 号镜像与当前默认输入，在独立副本中分别验证 0775 目录内新建结果、覆盖服务用户所有的 0644 结果文件。共 8 次运行：旧的 `--cap-drop ALL` 四次均复现 `PermissionError`，恢复 Docker 默认权限后四次均退出码 0 且产生有效 result.json。测试前后校验原算法 output 的内容与权限完全不变，没有清空生产结果。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/smoke_output_permissions.py
```

请以普通服务用户运行（非 root）。该脚本只在 `test-output/output-permissions-*` 建立副本并保留日志/结果，不修改原算法数据。另已复测 76 号和六容器并发、GPU 可见性、交互输入、后台重连，以及桌面/手机侧栏导航和文件预览；这些验证不等于重跑全部算法。

前端组件本地分发：[xterm.js 6.0.0](https://github.com/xtermjs/xterm.js)、addon-fit 0.11.0（MIT，许可证位于 `static/vendor/xterm/LICENSE`）。浏览器不需要访问 CDN。
