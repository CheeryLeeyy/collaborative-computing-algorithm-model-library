# 协同计算基础算法模型库

此项目只用于ATS计算技术课题一。

提供算法目录管理、文档预览和 Docker 算法测试的中文网页。后端基于 Python 标准库，无需安装第三方后端依赖；前端及预览组件随项目本地分发。

仓库仅保存网页代码、测试脚本、前端组件及其许可证，不包含算法数据、Docker 镜像包、运行日志或部署凭据。受管理的数据目录通过 `ALGO_ROOT` 配置，默认使用项目目录旁的 `unzips`。

## 功能

- 登录后才能浏览、预览、下载、上传、新建文件夹或删除。
- 逐层加载并服务端分页，适合大量文件和大容量目录。
- 算法名称按数字自然排序，例如 `algo2` 会排在 `algo10` 前面。
- 支持当前目录搜索、面包屑、顶层算法侧栏和移动端界面。
- 文本/代码限量预览；图片、PDF、DOCX、常用音视频按需预览；其他格式可下载。
- 多文件逐个流式上传并显示进度，不把几十 GB 的文件读入内存或写入根分区 `/tmp`。
- 同名文件可自动改名、拒绝或明确覆盖，默认自动改名。
- 上传前同时检查单文件上限和数据盘剩余空间。
- 容量通过解析后的真实目录文件描述符计算；即使数据根目录是软链接，也显示和校验实际存储盘的容量。
- 默认只删除文件、链接和空文件夹，避免误删整个非空算法目录。
- Session、CSRF、登录限速、操作日志、路径穿越和符号链接防护均在服务端执行。
- Docker 算法测试页：实时交互终端、GPU 可见设备选择、后台任务重连、停止、确认清空 output。
- 后端默认最多同时运行 8 个不同算法；同一算法目录原子互斥，运行期间禁止网页修改该算法的文件。
- 对照当前测试说明 DOCX、params.json 和已核对的历史成功配置生成命令，详情见 [Docker 测试说明](DOCKER_TESTING.md)。

## 启动

在项目根目录首次部署时，将 `.env.example` 复制为 `.env`，设置独立的登录信息及数据目录，然后启动：

```bash
cp -n .env.example .env
chmod 600 .env
# 编辑 .env，填写部署配置后运行：
./start.sh
```

默认监听 `0.0.0.0:8080`，局域网其他电脑可通过服务器 IP 访问。

登录信息请查看部署服务器项目根目录的 `.env` 文件；配置字段模板见 [.env.example](.env.example)，读取和认证逻辑见 [server.py](server.py)。`.env` 已被 Git 忽略，不随仓库分发，本 README 不列出具体凭据。

启动时只按字面量读取 `.env` 中的 `ALGO_*` 配置，不执行其中的 Shell 命令；显式设置的进程环境变量优先。服务端会拒绝使用旧版公开默认凭据的回退配置启动。测试脚本中的固定测试值不用于真实部署。

## tmux 后台运行

在项目根目录启动：

```bash
tmux new-session -d -s algorithm_file_manager \
  'exec ./start.sh >> server.log 2>&1'
```

查看日志和服务画面：

```bash
tmux attach -t algorithm_file_manager
```

从 tmux 画面退出但保持服务运行：按 `Ctrl-b`，再按 `d`。

确认没有正在运行的算法任务后停止（停止服务也会停止其管理的任务）：

```bash
tmux kill-session -t algorithm_file_manager
```

运行和错误日志是 `server.log`；上传、删除和新建文件夹记录是 `operations.log`。

## 操作日志

`operations.log` 使用一行一条 JSON 的格式，只记录上传、删除和新建文件夹，不记录正常浏览、预览、下载、登录或退出。成功和失败操作都会记录：

```json
{"time":"2026-09-29T10:30:00+08:00","event":"upload","result":"success","path":"algo1/example.tar","detail":"bytes=123456; conflict=rename"}
```

实时查看：

```bash
tail -f operations.log
```

以上为省略操作者与来源地址的示例。`event` 为 `upload`、`delete` 或 `mkdir`，`result` 为 `success` 或 `failed`；`delete` 的 detail 会注明删除的是文件还是文件夹。路径始终相对于数据根目录，不会把认证秘密、Cookie 或 CSRF Token 写入日志。旧版 `audit.log` 会保留；服务首次使用新版本启动时，会把其中已有的文件/文件夹变更记录复制到 `operations.log`，不会复制登录记录。

## DOCX 在线预览

项目本地内置 `docx-preview 0.4.0` 和 `JSZip 3.10.1`，先由服务端检查 DOCX 的压缩体积、解压体积、内部条目数和必要结构，再在浏览器隔离框架中排版。组件完全从项目的 `static/vendor` 加载，局域网客户端预览时不需要访问外网。

- 每次点击预览都会以 `no-store` 方式重新读取当前 DOCX，不生成或复用 PDF，修改后再次打开即可看到最新版。
- 在浏览器内补全 Word OMML 重音结构，并规范化上下标、求和上下限、分式和数学字体，避免复杂公式错位或丢字符。
- 默认压缩文件上限：10MiB。
- 默认解压后上限：40MiB。
- 默认内部条目上限：1000。
- 关闭 DOCX 内嵌 HTML（altChunk）、评论和脚本，并过滤外部资源与危险链接协议。
- 只支持 Office Open XML 的 `.docx`；旧版二进制 `.doc` 仍需下载后查看。

上游与许可证：[docx-preview / Apache-2.0](https://github.com/VolodymyrBaydalka/docxjs)、[JSZip / MIT 或 GPLv3](https://github.com/Stuk/jszip)。许可证原文随本地 vendor 文件保留。

## 可选配置

可通过本地 `.env` 或进程环境变量设置：

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `ALGO_ENV_FILE` | 项目根目录 `.env` | 可在启动前指定其他本地配置文件 |
| `ALGO_ROOT` | 项目目录旁的 `unzips` | 受管理的数据根目录 |
| `ALGO_HOST` | `0.0.0.0` | 监听地址；可改为服务器的局域网 IP |
| `ALGO_PORT` | `8080` | HTTP 端口 |
| `ALGO_USERNAME` | 见本地 `.env` | 部署登录配置 |
| `ALGO_PASSWORD` | 见本地 `.env` | 启动时读入，内存中散列后使用 |
| `ALGO_PASSWORD_HASH` | 可选 | 可使用预先生成的 PBKDF2-SHA256 哈希；非空时优先 |
| `ALGO_MAX_UPLOAD` | `40GiB` | 单文件上传上限，支持 `MiB/GiB/TiB` |
| `ALGO_MIN_FREE` | `2GiB` | 上传完成后必须保留的磁盘空间 |
| `ALGO_MAX_UPLOADS` | `2` | 同时处理的上传数 |
| `ALGO_MAX_TESTS` | `8` | Docker 算法测试最大并行数，1–32；同一算法最多 1 个 |
| `ALGO_CPU_THREADS` | `2` | 每个容器的 OpenMP/MKL/OpenBLAS/OpenCV 默认线程数，0–256；0 不覆盖镜像设置，历史明确设置优先 |
| `ALGO_PREVIEW_SIZE` | `1MiB` | 文本预览最多读取的字节数 |
| `ALGO_DOCX_PREVIEW_SIZE` | `10MiB` | DOCX 在线预览的压缩文件上限 |
| `ALGO_DOCX_UNPACKED_SIZE` | `40MiB` | DOCX 在线预览的解压后体积上限 |
| `ALGO_DOCX_MAX_ENTRIES` | `1000` | DOCX 内部条目数上限 |
| `ALGO_ALLOW_RECURSIVE_DELETE` | `0` | 设为 `1` 后允许递归删除非空目录，风险较高 |
| `ALGO_ALLOWED_HOSTS` | 空 | 逗号分隔的允许 Host；空值允许 IP、本机名和 localhost |
| `ALGO_COOKIE_SECURE` | `0` | HTTPS 反向代理部署时设为 `1` |
| `ALGO_REQUEST_TIMEOUT` | `120` | 单次 socket 空闲超时秒数 |
| `ALGO_OPERATION_LOG` | 项目内 `operations.log` | 上传、删除和新建文件夹操作日志路径 |

示例：把单文件上限降为 10GiB，并只监听本机回环地址：

```bash
ALGO_MAX_UPLOAD=10GiB ALGO_HOST=127.0.0.1 ./start.sh
```

## 安全说明

- `0.0.0.0` 会监听所有 IPv4 网卡，不等同于“只允许局域网”。请勿在路由器上做公网端口映射；有多块网卡时优先通过 `ALGO_HOST` 绑定内网 IP。
- 普通 HTTP 在局域网中不是加密连接。只应在可信内网/VPN 使用；敏感环境请通过 Caddy/Nginx 配置 HTTPS，并设置 `ALGO_COOKIE_SECURE=1`。
- 建议使用专用服务账号/组，并按实际需求将数据目录权限限制为 `0770` 或 `0750`。
- `.env` 只保留在部署服务器，权限设为 `0600`；不要把它、运行日志或操作日志提交到 Git。旧版测试凭据已停用于当前部署，不应再用于新部署。
- 只有登录用户点击“开始”才会加载并运行选定的 Docker 镜像包；普通浏览或预览不会启动算法。不要上传、运行来源不可信的镜像。
- Docker 测试要求服务用户能访问 Docker daemon；这个权限非常高，本网页仅适合可信账号和可信内网。终端只连当前容器的 stdin，不提供宿主机 Shell。
- 默认仅挂载 input（只读）和 output（可写），使用 Docker bridge 网络；禁止自定义宿主路径、Docker socket、特权容器、host 网络和 GPU 可见性覆盖。
- 删除是永久操作，没有网页回收站。非空目录的递归删除默认关闭。
- 页面按真实挂载盘容量判断警告。上传大文件前请再次确认目标存储盘的实时磁盘余量。

## 测试

测试仅使用系统临时目录，不会读写真实 `unzips` 数据：

```bash
python3 -m unittest discover -s tests -v
```

真实 Docker 与浏览器端到端测试为显式选择执行，见 `DOCKER_TESTING.md`，不会被上述单元测试自动启动。
