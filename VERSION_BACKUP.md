# 当前版本备份与恢复

- GitHub 私有仓库：https://github.com/CheeryLeeyy/kt1-algorithm-file-manager
- 开发分支：`main`
- 初始版本标签：`snapshot-2026-09-14`（保留原始指向）
- Docker 测试版标签：`docker-testing-2026-09-14`
- 备份范围：网页后端、前端、DOCX 预览组件及许可证、启动脚本、测试和说明文档。
- 不包含：`unzips` 算法数据、运行日志、操作日志、环境配置及 Python 缓存。此标签用于恢复网页代码，不能恢复被删除或修改的算法文件。

初始版本包含登录与文件管理、真实磁盘容量显示、文件/文件夹变更日志、直接读取最新 DOCX 的公式预览，以及预览弹窗按钮样式。初始备份时已有 8 项服务器测试通过。

Docker 测试版新增独立测试页、PTY 交互终端、单算法互斥、最多 6 个并行任务、GPU 选择、后台任务重连及历史成功配置核对。验证说明见 [DOCKER_TESTING.md](DOCKER_TESTING.md)。

恢复 Docker 测试版到新目录（先确保没有运行中的测试任务）：

```bash
cd /home/ly/jisuanjishu_docker/kt_1/algorithm_file_manager
git fetch origin --tags
git worktree add -b restore-docker-testing ../algorithm_file_manager-docker-restored docker-testing-2026-09-14
```

以下命令仍用于恢复最初的文件管理版本，不包含 Docker 测试功能。

## 查看备份版本

```bash
cd /home/ly/jisuanjishu_docker/kt_1/algorithm_file_manager
git show --stat snapshot-2026-09-14
git rev-parse 'snapshot-2026-09-14^{commit}'
```

## 恢复到独立目录

以下命令从 GitHub 获取标签，并在旁边的独立目录恢复该版本，保留当前目录和未提交修改：

```bash
cd /home/ly/jisuanjishu_docker/kt_1/algorithm_file_manager
git fetch origin --tags
git worktree add -b restore-2026-09-14 ../algorithm_file_manager-restore-2026-09-14 snapshot-2026-09-14
```

可以在另一个端口先验证恢复的版本：

```bash
cd /home/ly/jisuanjishu_docker/kt_1/algorithm_file_manager-restore-2026-09-14
./start.sh --host 0.0.0.0 --port 8081 --root /home/ly/jisuanjishu_docker/kt_1/unzips
```

这是恢复后的网页代码，但仍访问同一份实时算法数据。独立目录不会自动复制原目录的日志或环境变量。再次执行恢复时，使用一个尚不存在的分支名和目录名。

如果本机代码目录已经丢失，可在登录该私有仓库后重新克隆：

```bash
cd /home/ly/jisuanjishu_docker/kt_1
gh repo clone CheeryLeeyy/kt1-algorithm-file-manager algorithm_file_manager-recovered -- --branch snapshot-2026-09-14
```

## 保存后续修改

```bash
cd /home/ly/jisuanjishu_docker/kt_1/algorithm_file_manager
git status --short
git diff
git add -- server.py docker_runner.py verified_runs.json scripts start.sh static tests README.md DOCKER_TESTING.md VERSION_BACKUP.md .gitignore
git commit -m "说明本次修改"
git push origin main
```

保留 `snapshot-2026-09-14` 标签的原始指向。后续重要版本另建新标签并推送，不要覆盖已有快照标签。
