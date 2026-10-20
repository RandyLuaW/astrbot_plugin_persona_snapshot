# Auto-snapshot persona edits, roll back when they go wrong

> AstrBot plugin · watches the personas table and saves the previous version on every change

## The problem

Some bots edit their own persona — reach into the database, `UPDATE personas`, append a paragraph to themselves.

That's a good thing, but it has **no undo**: a bad edit, a wrong tone, or a single typo and the previous version is gone. AstrBot's built-in persona editor overwrites in place with no history.

This plugin adds the undo: poll the personas table, and when content changes, save the **previous** version before the change lands. She can edit as often as she likes and always roll back.

## Why tell her about it

If the backup is a secret, she stays nervous about editing — not knowing what happens if she breaks it makes her either avoid editing or change too much at once.

Telling her the escape hatch exists makes her bolder with small steps. So besides the tools, the plugin injects a hint in admin private chat: *you have snapshots, you can roll back*.

## Features

| Name | Type | Purpose |
|---|---|---|
| `persona_snapshots` | LLM tool | List historical snapshots for a persona |
| `persona_restore` | LLM tool | Restore a persona to a given snapshot |
| `/人格快照` | command | Manually inspect snapshot status |

## Install

### Option 1: Plugin marketplace (recommended)

Search "人格快照" in the AstrBot WebUI plugin marketplace.

### Option 2: Download from Releases

Grab the zip from [Releases](../../releases), then WebUI → Plugins → Upload.

### Option 3: Clone manually

```bash
cd AstrBot/data/plugins
git clone https://github.com/RandyLuaW/astrbot_plugin_persona_snapshot.git
```

No extra dependencies.

## Configuration

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Master switch; existing snapshots are kept when off |
| `check_interval_seconds` | int | `60` | Polling interval |
| `max_snapshots_per_persona` | int | `40` | Max snapshots per persona; oldest removed first |
| `snapshot_on_start` | bool | `true` | Save a baseline at startup (skipped if identical) |
| `admin_only` | bool | `true` | Restrict tools to admin private chat |
| `inject_hint` | bool | `true` | Tell her the rollback capability exists |
| `restore_requires_confirm` | bool | `true` | Require reviewing the list before restoring |

## How it works

```
Poll personas every 60s
   ├─ unchanged since last seen → skip
   └─ changed → write the **previous** content as a snapshot → update record
                        ↓
        snapshots/<persona_id>@<timestamp>_<hash>.md
```

Semantics: a baseline is saved at startup; afterwards every detected change stores the **pre-change** version. So "the version before it broke" is always available.

Restore goes through the framework's persona API rather than writing sqlite directly — direct `UPDATE` is shadowed by PersonaManager's in-memory cache and silently does nothing.

## Tuning

- **Changed something and want to revert immediately, but no snapshot yet**: lower `check_interval_seconds` to 10–15
- **Snapshots take too much space**: set `max_snapshots_per_persona` to 10–20
- **Hide the capability from her**: `inject_hint: false` (tools still work)

## Notes

- Only covers the `personas` table, not plugin config files
- Restore uses `PUT /api/v1/personas/by-id` so the in-memory cache updates; raw sqlite writes won't take effect
- `admin_only` is on by default

## License

MIT
