# 当前版本备份与恢复

- 项目名称：协同计算基础算法模型库
- 项目用途：此项目只用于ATS计算技术课题一
- GitHub 仓库：https://github.com/CheeryLeeyy/collaborative-computing-algorithm-model-library
- 开发分支：`main`
- 初始版本标签：`snapshot-2026-09-14`（保留原始指向）
- Docker 测试版标签：`docker-testing-2026-09-14`
- Docker 权限与界面修复版标签：`docker-testing-fix-2026-09-14`
- 本次版本标签：`release-2026-09-29`
- 备份范围：网页后端、前端、DOCX 预览组件及许可证、启动脚本、测试和说明文档。
- 不包含：`unzips` 算法数据、运行日志、操作日志、环境配置及 Python 缓存。此标签用于恢复网页代码，不能恢复被删除或修改的算法文件。

初始版本包含登录与文件管理、真实磁盘容量显示、文件/文件夹变更日志、直接读取最新 DOCX 的公式预览，以及预览弹窗按钮样式。初始备份时已有 8 项服务器测试通过。

Docker 测试版新增独立测试页、PTY 交互终端、单算法互斥、最多 6 个并行任务、GPU 选择、后台任务重连及历史成功配置核对。验证说明见 [DOCKER_TESTING.md](DOCKER_TESTING.md)。

权限与界面修复版恢复 Docker 默认 capabilities，修复 6、10 号算法的 output 写入错误；保留算法测试页左侧导航、统一运行文件图标和文字，并去掉历史成功记录来源提示。27 项自动测试、真实权限对照测试与浏览器/Docker 回归通过。恢复此版本时，将下面命令末尾的标签改为 `docker-testing-fix-2026-09-14`，并使用新的 worktree 目录与分支名。

本次版本保存当前全部网页改进：8 任务并发、计算库线程预算、Docker 镜像缓存识别、可调整宽度的文件栏、输出目录导航修复，以及全量网页测试和 CSV 汇总脚本。新增本地 `.env` 部署配置读取和旧默认凭据启动保护。

登录信息仅保存在服务器本地 `.env`，不属于 Git 备份。公开发布前已更换当前部署凭据；旧提交和旧标签中的测试凭据不得再次用于真实部署。恢复任何旧标签前必须显式提供新的认证环境变量，因为旧版本不会自动读取 `.env`，也没有新增的启动保护。

## 恢复本次版本

在当前仓库根目录运行，先确认没有运行中的算法测试任务：

```bash
git fetch origin --tags
git worktree add -b restore-2026-09-29 ../algorithm_file_manager-restored-2026-09-29 release-2026-09-29
```

恢复目录不会复制本地 `.env`。可用 `ALGO_ENV_FILE` 指向现有受保护的配置文件，另选端口验证；不要让恢复服务与原服务同时对同一数据根目录执行 Docker 测试。

恢复 Docker 测试版到新目录（先确保没有运行中的测试任务）：

```bash
# 在当前仓库根目录执行。
git fetch origin --tags
git worktree add -b restore-docker-testing ../algorithm_file_manager-docker-restored docker-testing-2026-09-14
```

以下命令仍用于恢复最初的文件管理版本，不包含 Docker 测试功能。

## 查看备份版本

```bash
# 在当前仓库根目录执行。
git show --stat snapshot-2026-09-14
git rev-parse 'snapshot-2026-09-14^{commit}'
```

## 恢复到独立目录

以下命令从 GitHub 获取标签，并在旁边的独立目录恢复该版本，保留当前目录和未提交修改：

```bash
# 在当前仓库根目录执行。
git fetch origin --tags
git worktree add -b restore-2026-09-14 ../algorithm_file_manager-restore-2026-09-14 snapshot-2026-09-14
```

可以在另一个端口先验证恢复的版本：

```bash
cd ../algorithm_file_manager-restore-2026-09-14
# 必须先显式设置新的认证环境变量，再启动旧版本。
./start.sh --host 127.0.0.1 --port 8081 --root /path/to/unzips
```

这是恢复后的网页代码，但仍访问同一份实时算法数据。独立目录不会自动复制原目录的日志或环境变量。再次执行恢复时，使用一个尚不存在的分支名和目录名。

如果本机代码目录已经丢失，可重新克隆当前版本：

```bash
gh repo clone CheeryLeeyy/collaborative-computing-algorithm-model-library algorithm_file_manager-recovered -- --branch release-2026-09-29
```

## 保存后续修改

```bash
# 在当前仓库根目录执行。
git status --short
git diff
git add -- server.py docker_runner.py verified_runs.json scripts start.sh static tests README.md DOCKER_TESTING.md VERSION_BACKUP.md .gitignore .env.example
git commit -m "说明本次修改"
git push origin main
```

保留 `snapshot-2026-09-14` 标签的原始指向。后续重要版本另建新标签并推送，不要覆盖已有快照标签。
