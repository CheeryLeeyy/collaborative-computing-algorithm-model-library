# Docker 算法测试

## 使用

进入一个顶层算法目录，点击“算法测试”。如果没有 `.tar` / `.tar.gz` / `.tgz` Docker save 镜像包，弹出“没有找到docker文件”。不执行 Dockerfile 构建，也不执行没有 Docker 镜像的本机 Python/CARLA 工程。

- 最左侧保留算法目录导航，点击算法可返回对应文件管理页。中间按层级显示运行文件，复用文件管理页的文件图标、名称样式和预览功能，隐藏 DOC/DOCX/MD/PDF/PPT 等说明文档；支持目录分页。
- 运行文件栏默认宽 360px，可拖动它与终端之间的分隔条调整，右侧终端同步缩放。宽度记在当前浏览器；双击分隔条恢复默认，聚焦后左右方向键每次调整 20px。桌面布局保留文件栏至少 240px、终端至少 320px；窗口变小时自动限制宽度，手机上改为上下排列。
- GPU 默认全部可见，可选择某一张物理 GPU 或不使用 GPU。选择 GPU 1 时通过 UUID 指定该物理设备，容器内部编号可能从 0 开始。多任务可以共享同一张 GPU，但并不隔离显存或算力配额。
- “查看运行命令”弹窗只显示命令，不显示来源、注释或说明文字。占位任务名在实际启动时替换成唯一 ID；每次打开预览都按镜像包完整摘要查询本地 Docker 镜像缓存，一致时不显示也不执行 load；不存在时才显示 load。不能仅凭同名 tag 或 tar 文件名复用镜像。正式开始时会再次检查缓存，避免预览后被其他进程加载/删除镜像而使用过时的判断；Docker 不可用或无权限时明确报错。实际执行命令也会出现在终端。
- “开始”加载镜像并通过 `docker create` + `docker start --attach --interactive` 运行；等价于交互式 `docker run`，但能够更可靠地处理启动/停止竞争。
- 终端使用本地 xterm.js 与服务端 PTY，约 300ms 增量读取输出，支持键盘、粘贴、Ctrl+C 和窗口尺寸调整。输入只发送给容器进程，不会变成宿主机命令。
- 文件列表在运行期间每 3 秒刷新，任务结束时再刷新一次，展示算法整理后的最终输出。另一设备运行同一算法结束时也会刷新。目录被删除、移动或暂不可访问时，自动退回最近的可访问上级；只有成功加载后才更新当前路径，避免连续点击旧目录时重复拼接路径。后台刷新不会抢占用户正在进行的跳转。输出保存到当前算法的真实 output 目录，运行不会自动清空旧结果。
- “清空 output”需要用户确认，删除目录内的文件、子目录和软链接本身，不跟随软链接。保留 output 目录；若正在查看它的子目录，清空后返回 output 并刷新列表。删除不可恢复；成功、失败及已删除项目数量写入 `operations.log`。遇到容器生成的 root 所有、且服务用户无权删除的子目录时会报错，需要管理员先调整该目录权限。
- “停止”只停止、清理本任务创建且标签匹配的容器，不操作机器上的其他 Docker 服务。

## 并发与后台任务

服务端原子校验同一算法目录只能存在一个任务，跨设备、跨标签页同时点击也不能重复启动；已有任务时弹出“需要先停止在运行的算法”。包括加载、创建、运行、停止中的任务均占用名额。运行中的算法目录禁止上传、新建文件夹、删除和清空 output，但正常查看、下载不受影响。

默认最多 8 个不同算法并行。修改上限后重新启动服务：

```bash
ALGO_MAX_TESTS=8 ./start.sh
```

并行测试默认通过 `--env` 设置 `OMP_NUM_THREADS=2`、`MKL_NUM_THREADS=2`、`OPENBLAS_NUM_THREADS=2`、`OPENCV_FOR_THREADS_NUM=2`，避免多个容器各自创建整台服务器规模的线程池，使小型 CPU 推理反而显著变慢。管理员可用 `ALGO_CPU_THREADS` 调整（0 不覆盖镜像默认设置），已核对历史记录中的同名显式设置优先。这是计算库线程池提示，不是 CPU 硬配额，也不限制数据加载子进程；不改变模型、输入、迭代次数或 GPU 选择。新设置只影响之后创建的容器；服务重启前必须等待已有任务结束。

关闭测试页、标签页或浏览器不会停止容器。重新登录后，可在任意设备的右上角“后台任务”中重新连接；同一账号的任务可以相互查看和停止。右上角保留最近 50 个完成任务，每个任务的内存终端缓冲上限约 2 MiB，超过后截断最早输出。

任务状态不是跨服务重启持久化的数据库。正常停止网页服务会停止其管理的任务；不要在任务运行时重启、强制杀死网页服务或 Docker daemon。异常强杀后应由管理员核对带 `kt1.file-manager` 标签的遗留容器。不要对同一数据根目录同时部署多个服务进程来绕过互斥和并发限制。

## 运行配置来源

1. 挂载当前算法的 input 到 `/app/data/input:ro`，output 到 `/app/data/output`，与提交说明及成功实测记录一致。
2. 每次开始读取当前 `params.json` 和 `<算法名>测试说明.docx`。支持 `gpus`、`shm-size`、`platform`、`memory`、`cpus`、`pids-limit`。DOCX 中明确的允许参数补充缺失配置；与当前 params.json 冲突时拒绝启动，要求核对。
3. `verified_runs.json` 保存人工核对后纳入版本控制的历史成功配置及相对记录路径，不包含历史输入输出或原文文档。优先使用较新的退出码 0 且实际产生输出的记录，排除 `failed-attempts`。历史缺失参数可补充当前配置；与当前明确配置不同时使用当前配置并在终端提示。
4. 历史记录中的输入输出绝对路径、容器名和 GPU 编号不会照搬；网页 GPU 选择始终优先。容器入口参数只作为 Docker 镜像后的 argv 传入，历史环境变量不能覆盖 GPU 可见性。
5. 通过镜像摘要而不是可变 tag 启动，兼容经典 Docker save 和 Docker 29 OCI/containerd 归档。必要时 load 后解析真实 ID，并恢复 load 前已有镜像标签，避免影响其他服务。

缓存检查适用于所有算法，预览与实际启动共用同一逻辑：优先按完整镜像摘要查询；经典配置 ID 与新版 Docker 的 manifest ID 不同时，校验归档配置文件 SHA256，再比对本地镜像的全部有序层摘要、运行配置（入口、命令、环境、用户、标签等）、平台与创建信息。找到相同内容后固定使用本地不可变 ID，因此原 tag 改名或删除也可复用。配置中被 Docker 省略的空字段不视为内容变化；非空参数、层内容或平台不同则必须加载。检查不会仅凭文件名或镜像名称命中缓存，不会为了测试清理现有镜像，也不对整个多 GB tar 反复计算哈希。正式启动会重新查询 Docker，缓存被删除后会自动恢复加载。

2026-09-14 对当前 119 个镜像包只读预检无错误：42 个匹配本地缓存，77 个需要加载；其中 11–13 号旧格式和 85 号不同名称的缓存也能复用，66 号同名但内容不同的缓存不会误用。缓存数量随服务器镜像状态变化；此检查不等于运行全部算法。35 项单元/接口测试与真实 Docker/浏览器回归通过，已验证命令预览与实际缓存命中均跳过 load。

运行默认使用 Docker bridge 网络、`no-new-privileges`、Docker 标准默认 capabilities、进程/线程数上限 512（可通过允许的 params.json 调整至最多 4096）。仅允许固定 input/output 挂载。不接受特权、host 网络、任意卷或宿主 Shell 参数。不要添加 `--cap-drop ALL`：它移除了标准 `DAC_OVERRIDE` 能力，使容器 root 无法写入服务用户所有的 0775 output 目录或覆盖 0644 结果文件。兼容默认 Docker 权限，不通过自动 chmod/chown 或清空结果规避问题；非 root 镜像仍须匹配 output 的实际访问权限。

### 2026-09-14 文档与历史记录核对

- 补充核对 `docker_reload_all_20260914/summary.csv` 及对应运行 JSON：114 个成功配置的容器入口、环境参数、非 GPU 选项与现有配置一致，已更新其来源与时间；40、41、74、95、96 在该批次失败，不以失败记录覆盖已有成功配置。GPU 编号仍由网页决定，95、96 在本轮网页单卡测试已产生新输出。
- 核对 131 份当前测试说明，其中 121 份包含 Docker run 命令。
- 106–118 共 13 个算法的 DOCX 指定 `--platform linux/amd64`，已自动补充。
- 95、96、125、129 的 DOCX 指定 `--shm-size=16g`，而根目录配置未给出，已支持从文档补充；125、129 当前没有镜像包，仍不能从网页启动。
- 从分批记录的 189 份候选元数据中，选出覆盖 119 个算法的成功配置（包括 run.json、run_summary.json 和 run_summary_compact.json）。
- 81–83 号成功命令额外指定容器内 Python 入口及 epochs/users 等小规模测试参数；89 号使用 EPOCHS、NUM_USERS 等环境变量。网页采用这些已核对配置；页面不显示“已参考…成功记录”，终端不显示成功记录路径，只保留必要的配置差异/小规模测试提醒。这不是完整训练或全量验收的保证。
- 90 号采用 2026-09-10 `default-no-env` 成功记录，不套用旧版临时环境变量。
- 所有当前含镜像包的 119 个目录均通过了只读命令配置预检。91、92、101–104、125–127、129–131 共 12 个目录当前没有 Docker 镜像包；131 的 DOCX 指定本机 CARLA/Python 流程，不能用标准 Docker 方式启动。
- 以上是命令预检阶段，不等同于实际运行。后续全量网页实测见下节；修改镜像、输入或运行环境后仍需要重新验证。

重新审计历史元数据（只输出 JSON，不运行容器、不修改历史记录）：

```bash
python3 scripts/audit_docker_records.py /path/to/docs_processing_records
```

## 实测与回归

### 1–131 号网页全量测试

2026-09-14 全量及复测已结束：119 个有镜像算法最终通过，12 个无镜像跳过；首轮峰值 8 个任务。汇总见 `test-output/web-all-summary-20260914/summary.csv`，逐次结果见同目录 `attempts.csv`，范围和限制见 `REPORT.md`。40/41/74 使用双卡可见配置；12/98/100/122 显存不足后在 GPU 0 复测；19/65/67/90 在线程池默认 2 的配置下复测。67 号完整 25,190 帧在 111.548 秒内完成，301 个输出，未缩短视频。首次失败和人工停止记录均保留。

`scripts/test_all_algorithms_web.py` 是需要显式确认的生产数据测试脚本：它使用真实 Chromium 登录网页，对存在 Docker 镜像包的算法依次点击“清空 output”（确认）、选择 GPU、查看命令、开始；不直接调用 Docker CLI 代替网页启动。**会永久删除待测算法已有 output 内容**，不会删除 output 根目录或修改 input、镜像、算法源码。没有镜像包的算法跳过且不清空。

真实部署的登录信息仅在本地 `.env` 中。执行生产网页测试前，需通过 `ALGO_TEST_USER`、`ALGO_TEST_PASSWORD` 提供当前部署凭据；脚本中的历史测试值已不适用于当前服务器。不要把真实凭据写入测试脚本或提交到仓库。

普通算法按 GPU 0/1 交替分配，最多 8 路并行；74 号在本轮其他任务结束后选择全部 GPU（已校验服务器恰有 0/1 两张卡）。不会停止服务器其他业务进程。每个任务默认超时 2 小时，超时通过网页停止并单独标为 timeout，不等同于算法报错。

```bash
PLAYWRIGHT_BROWSERS_PATH="$PWD/.test-browsers" PYTHONDONTWRITEBYTECODE=1 \
  .test-venv/bin/python -u scripts/test_all_algorithms_web.py \
  --report "$PWD/test-output/full-web-新的唯一批次名" --confirm-clear-output
```

报告目录必须是尚不存在且位于数据目录之外的新目录。运行过程中持续更新 `results.csv`（UTF-8 BOM，可用 Excel 打开）、`results.json`、`progress.json`；每个算法保存清空前文件清单、清空接口结果、实际命令、任务状态、完整增量终端日志、新输出清单和网页截图。通过标准为任务退出成功、清空后产生非空输出文件，并完成网页输出目录/文件检查；这不是算法精度验收。CSV 保留所有 131 个算法的成功、失败、无输出、网页检查失败、超时或无镜像跳过状态，不把跳过项算作成功。

复测使用新报告目录，可通过 `--algorithms 19,65,67,90` 选择编号，`--workers` 调整本批工作协程，`--gpu 0` 固定物理 GPU，`--gpu all --workers 1` 做逐项双卡对照。`--allow-active-tests --min-free-gpu-mib 6000` 允许与不重叠的已有算法任务并行，启动前等待服务器名额和所选 GPU 的空闲显存；这不是显存预留或硬隔离，重型任务仍应减少同时运行数。每次复测仍会再次确认清空该算法 output，历史日志、清空前清单和首次结论保留在原报告目录，但原输出文件本身不会自动备份。

全部批次结束后，使用 `scripts/summarize_web_tests.py <首轮目录> <复测目录> ... --output <新的汇总目录>` 生成中文 `summary.csv`、逐次记录 `attempts.csv` 和 `summary.json`。按时间先后传入目录；必须合计覆盖 1–131 且每批已结束，不会把运行中结果发布成最终汇总。汇总保留首次失败/手动停止与后续结果，并把小规模训练设置等计划提醒写入备注。

无 Docker 的自动测试：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
node --check static/app.js
node --check static/runner.js
bash -n start.sh
```

分栏拖拽的浏览器回归（需要项目 Playwright/Chromium，不启动 Docker）：

```bash
PLAYWRIGHT_BROWSERS_PATH="$PWD/.test-browsers" PYTHONDONTWRITEBYTECODE=1 .test-venv/bin/python tests/smoke_splitter_browser.py
```

验证默认宽度、拖拽与终端/服务端 PTY 尺寸联动、宽度上下限、刷新记忆、键盘调整、双击恢复、手机布局和浏览器存储不可用时的兼容性。测试仅使用独立目录及测试 PTY，不影响正在运行的算法。

运行输出目录变化的浏览器回归（真实浏览器、HTTP 接口及独立测试目录，模拟任务状态，不启动 Docker）：

```bash
PLAYWRIGHT_BROWSERS_PATH="$PWD/.test-browsers" PYTHONDONTWRITEBYTECODE=1 .test-venv/bin/python tests/smoke_output_navigation.py
```

验证子目录内清空后返回 output、运行结束整理中间目录后刷新与图片预览、连续点击失效目录、后台刷新与跳转竞争、无权限目录恢复、另一设备任务完成后刷新，以及离开页面后忽略旧响应。2026-09-14 日志确认 45、47 号均曾发生中间目录整理后的旧路径访问错误；只读检查 119 个现有算法 output 及最多三层子目录，共 401 个目录，未发现权限或软链接问题。修复后通过在线网页验证 45、47 号的最终目录与 PNG 预览。此检查没有重跑全部算法，没有修改生产输出或权限。

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
