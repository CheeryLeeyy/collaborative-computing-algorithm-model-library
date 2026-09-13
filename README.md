# 算法文件管理网页

一个无需安装第三方后端依赖的中文文件管理页面，专门用于浏览和管理：

```text
/home/ly/jisuanjishu_docker/kt_1/unzips
```

项目代码全部位于当前 `algorithm_file_manager` 文件夹，不会混入算法数据目录。

## 功能

- 登录后才能浏览、预览、下载、上传、新建文件夹或删除。
- 逐层加载并服务端分页，适合当前约 45 万个文件、1.2TB 的目录。
- 算法名称按数字自然排序，例如 `algo2` 会排在 `algo10` 前面。
- 支持当前目录搜索、面包屑、顶层算法侧栏和移动端界面。
- 文本/代码限量预览；图片、PDF、DOCX、常用音视频按需预览；其他格式可下载。
- 多文件逐个流式上传并显示进度，不把几十 GB 的文件读入内存或写入根分区 `/tmp`。
- 同名文件可自动改名、拒绝或明确覆盖，默认自动改名。
- 上传前同时检查单文件上限和数据盘剩余空间。
- 容量通过解析后的真实目录文件描述符计算；当前 `/home/ly/jisuanjishu_docker` 虽是软链接，页面显示和校验的是其实际存储盘 `/mnt/disk2`，不是 `/home` 根分区。
- 默认只删除文件、链接和空文件夹，避免误删整个非空算法目录。
- Session、CSRF、登录限速、操作日志、路径穿越和符号链接防护均在服务端执行。

## 启动

```bash
cd /home/ly/jisuanjishu_docker/kt_1/algorithm_file_manager
./start.sh
```

默认监听 `0.0.0.0:8080`，局域网其他电脑可通过服务器 IP 访问。

默认登录凭据：

```text
用户名：bupt
密码：jsjskt1
```

密码在源码中仅保存为 PBKDF2-SHA256 哈希。需要临时更换账号密码时：

```bash
ALGO_USERNAME='new-user' ALGO_PASSWORD='a-strong-password' ./start.sh
```

## tmux 后台运行

启动：

```bash
tmux new-session -d -s algorithm_file_manager \
  "cd /home/ly/jisuanjishu_docker/kt_1/algorithm_file_manager && exec ./start.sh >> server.log 2>&1"
```

查看日志和服务画面：

```bash
tmux attach -t algorithm_file_manager
```

从 tmux 画面退出但保持服务运行：按 `Ctrl-b`，再按 `d`。

停止：

```bash
tmux kill-session -t algorithm_file_manager
```

运行和错误日志是 `server.log`；上传、删除和新建文件夹记录是 `operations.log`。

## 操作日志

`operations.log` 使用一行一条 JSON 的格式，只记录上传、删除和新建文件夹，不记录正常浏览、预览、下载、登录或退出。成功和失败操作都会记录：

```json
{"time":"2026-08-05T10:30:00+08:00","event":"upload","result":"success","user":"bupt","client":"10.112.1.20","path":"algo1/example.tar","detail":"bytes=123456; conflict=rename"}
```

实时查看：

```bash
tail -f /home/ly/jisuanjishu_docker/kt_1/algorithm_file_manager/operations.log
```

其中 `event` 为 `upload`、`delete` 或 `mkdir`，`result` 为 `success` 或 `failed`；`delete` 的 detail 会注明删除的是文件还是文件夹。路径始终是相对于 `unzips` 的路径，不会把密码、Cookie 或 CSRF Token 写入日志。旧版 `audit.log` 会保留；服务首次使用新版本启动时，会把其中已有的文件/文件夹变更记录复制到 `operations.log`，不会复制登录记录。

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

均通过环境变量设置：

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `ALGO_ROOT` | `/home/ly/jisuanjishu_docker/kt_1/unzips` | 受管理的数据根目录 |
| `ALGO_HOST` | `0.0.0.0` | 监听地址；也可只绑定 `10.112.76.79` |
| `ALGO_PORT` | `8080` | HTTP 端口 |
| `ALGO_USERNAME` | `bupt` | 登录用户名 |
| `ALGO_PASSWORD` | 文档默认密码 | 启动时覆盖密码，内存中散列后使用 |
| `ALGO_PASSWORD_HASH` | 内置 PBKDF2 哈希 | 用预先生成的 PBKDF2 哈希覆盖密码 |
| `ALGO_MAX_UPLOAD` | `40GiB` | 单文件上传上限，支持 `MiB/GiB/TiB` |
| `ALGO_MIN_FREE` | `2GiB` | 上传完成后必须保留的磁盘空间 |
| `ALGO_MAX_UPLOADS` | `2` | 同时处理的上传数 |
| `ALGO_PREVIEW_SIZE` | `1MiB` | 文本预览最多读取的字节数 |
| `ALGO_DOCX_PREVIEW_SIZE` | `10MiB` | DOCX 在线预览的压缩文件上限 |
| `ALGO_DOCX_UNPACKED_SIZE` | `40MiB` | DOCX 在线预览的解压后体积上限 |
| `ALGO_DOCX_MAX_ENTRIES` | `1000` | DOCX 内部条目数上限 |
| `ALGO_ALLOW_RECURSIVE_DELETE` | `0` | 设为 `1` 后允许递归删除非空目录，风险较高 |
| `ALGO_ALLOWED_HOSTS` | 空 | 逗号分隔的允许 Host；空值允许 IP、本机名和 localhost |
| `ALGO_COOKIE_SECURE` | `0` | HTTPS 反向代理部署时设为 `1` |
| `ALGO_REQUEST_TIMEOUT` | `120` | 单次 socket 空闲超时秒数 |
| `ALGO_OPERATION_LOG` | 项目内 `operations.log` | 上传、删除和新建文件夹操作日志路径 |

示例：把单文件上限降为 10GiB，并只监听当前局域网网卡：

```bash
ALGO_MAX_UPLOAD=10GiB ALGO_HOST=10.112.76.79 ./start.sh
```

## 安全说明

- `0.0.0.0` 会监听所有 IPv4 网卡，不等同于“只允许局域网”。请勿在路由器上做公网端口映射；有多块网卡时优先通过 `ALGO_HOST` 绑定内网 IP。
- 普通 HTTP 在局域网中不是加密连接。只应在可信内网/VPN 使用；敏感环境请通过 Caddy/Nginx 配置 HTTPS，并设置 `ALGO_COOKIE_SECURE=1`。
- 当前数据目录权限较宽（现场为 `0777`）。若机器有其他不可信用户，建议使用专用用户/组并将目录权限收紧为 `0770` 或 `0750`。
- 页面不执行、导入或解压上传的算法包；只有用户主动预览 DOCX 时，才会在受限浏览器区域解析该文档。
- 删除是永久操作，没有网页回收站。非空目录的递归删除默认关闭。
- 页面按真实挂载盘容量判断警告。上传大文件前请再次确认 `/mnt/disk2` 的实时磁盘余量。

## 测试

测试仅使用系统临时目录，不会读写真实 `unzips` 数据：

```bash
python3 -m unittest discover -s tests -v
```
