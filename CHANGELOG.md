# 更新日志

所有重要变更记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.0] - 2026-10-20

### 新增

- 首个公开版本。
- 后台每 60 秒轮询 `personas` 表，检测到内容变化时先存档**改动前**的版本。
- `persona_snapshots`：查看某个人格的历史快照列表。
- `persona_restore`：回滚到指定快照（走框架 persona API，确保内存缓存同步）。
- 管理员私聊注入提示，让她知道「改坏了能回滚」，鼓励小步修改。
- 快照数量上限（默认 40/人格），超出自动删最旧。
- 回滚默认要求二次确认，避免手快退错版本。
