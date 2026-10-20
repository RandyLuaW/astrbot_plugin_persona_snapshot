# 人格改动自动存档，改坏了能退回去

> AstrBot 插件 · 后台盯着 personas 表，一变就存旧版本；提供查看与一键回滚

## 它解决什么问题

有些 Bot 会自己改人格——摸到数据库，`UPDATE personas`，给自己加一段话。

这条路能走通是好事，但它**没有任何退路**：改坏了、改歪了、或者只是手滑写错一个字，原来的版本就没了。AstrBot 自带的 persona 编辑是覆盖式的，没有历史。

这个插件补上退路：后台轮询 personas 表，一旦内容变了就先把**旧版本**存下来，再让改动落地。她改多少次都无所谓，随时能退回去。

## 为什么要让她知道

只有我们这边偷偷备份，她下次改人格时仍然会紧张——不知道改坏了会怎样，就会倾向于「不敢改」或者「一次改很多」。

把退路明说出来，她反而敢小步试。所以除了工具，还会在管理员私聊注入一句提示，让她知道：*你有快照机制，改坏了能回滚*。

## 功能一览

| 名称 | 类型 | 作用 |
|---|---|---|
| `persona_snapshots` | LLM 工具 | 查看某个人格的历史快照列表 |
| `persona_restore` | LLM 工具 | 把某个人格回滚到指定快照 |
| `/人格快照` | 指令 | 手动查看当前快照状态 |

## 安装

### 方式一：插件市场（推荐）

WebUI 插件市场搜索「人格快照」。

### 方式二：Releases 下载

从 [Releases](../../releases) 下载 zip，WebUI → 插件 → 上传安装。

### 方式三：手动克隆

```bash
cd AstrBot/data/plugins
git clone https://github.com/RandyLuaW/astrbot_plugin_persona_snapshot.git
```

无额外依赖。

## 配置项

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 总开关；关闭后不再自动存档，工具也不可用（已存快照保留） |
| `check_interval_seconds` | int | `60` | 每隔多少秒检查一次 personas 表 |
| `max_snapshots_per_persona` | int | `40` | 每个人格最多保留几份，超出删最旧 |
| `snapshot_on_start` | bool | `true` | 启动时先存一份作基线（内容相同则跳过） |
| `admin_only` | bool | `true` | 只允许管理员私聊查看和回滚 |
| `inject_hint` | bool | `true` | 是否告诉她「你有快照机制、改坏了能回滚」 |
| `restore_requires_confirm` | bool | `true` | 回滚时是否要求先看清再退（防手快退错） |

## 工作机制

```
每 60 秒轮询 personas 表
   ├─ 内容与上次记录一致 → 跳过
   └─ 内容变了 → 把**变化前**的内容写成一份快照 → 更新记录
                        ↓
        snapshots/<persona_id>@<时间戳>_<hash>.md
```

快照语义：启动时存 baseline，之后每次检测到变化存**改动前的版本**。所以「改坏之前的那个版本」一定在。

回滚通过框架的 persona API 写回，不是直接改数据库——直接 `UPDATE` 会被 PersonaManager 的内存缓存挡住（改了也不生效）。

## 调参建议

- **改完立刻想回滚，但快照还没存**：把 `check_interval_seconds` 调到 10~15
- **快照占空间**：`max_snapshots_per_persona` 调到 10~20
- **不想让她知道这个能力**：`inject_hint: false`（工具仍可调用）

## 注意事项

- 只对 `personas` 表生效，不管插件自己的配置文件
- 回滚走 `PUT /api/v1/personas/by-id`，热更新内存缓存；直接改 sqlite 不会生效
- `admin_only` 默认开启，群聊与普通用户拿不到这两个工具

## License

MIT
