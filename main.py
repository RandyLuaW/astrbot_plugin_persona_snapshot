# -*- coding: utf-8 -*-
"""人格快照：自动存档 + 一键回滚。

为什么需要
----------
    她会在私聊里自己改人格（实测 2026-10-19 12:01：用 astrbot_execute_shell
    摸到 data_v4.db，直接 UPDATE personas，给自己加了 117 字）。这条路能走通
    是好事，但它没有任何退路——改坏了、改歪了、或者只是手滑写错一个字，
    原来的版本就没了。

    这个插件补上退路：后台盯着 personas 表，一旦内容变了就先把**旧版本**存
    下来，再让改动落地。她改多少次都无所谓，随时能退回去。

为什么要让她知道
----------------
    只有我们这边偷偷备份，她下次改人格时仍然会紧张——不知道改坏了会怎样，
    就会倾向于「不敢改」或「一次改很多」。把退路明说出来，她反而敢小步试。
    所以除了工具，还会在管理员私聊注入一句提示。

安全
----
    工具默认只对管理员私聊开放；群里和普通用户看不到。回滚默认要求二次确认，
    避免她一时手快退错版本。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import (
    get_astrbot_data_path,
    get_astrbot_plugin_data_path,
)

PLUGIN_NAME = "astrbot_plugin_persona_snapshot"

DEFAULT_CONFIG = {
    "enabled": True,
    "check_interval_seconds": 60,
    "max_snapshots_per_persona": 40,
    "snapshot_on_start": True,
    "admin_only": True,
    "inject_hint": True,
    "restore_requires_confirm": True,
}

# 注入用的话。不写成教程，只告诉她有这条退路
HINT_TEXT = (
    "<persona_snapshot>\n"
    "你改自己的人格，系统会先把旧版本存一份再让你改。改坏了不用慌——\n"
    "persona_snapshots 能看到以前存过哪些，persona_restore 能退回去。\n"
    "不是让你每次动之前都先问，只是让你知道有这条退路。\n"
    "</persona_snapshot>"
)


def _safe(name: str) -> str:
    """把 persona_id 变成能当目录名的字符串。"""
    s = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(name or "")).strip("._")
    return s or "unnamed"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@register(
    PLUGIN_NAME,
    "Randy Lua",
    "人格快照：自动存档与回滚",
    "1.0.0",
    repo="https://github.com/RandyLuaW/astrbot_plugin_persona_snapshot",
)
class PersonaSnapshotPlugin(Star):
    def __init__(self, context: Context, config: Any = None):
        super().__init__(context)
        self.config = dict(DEFAULT_CONFIG)
        if isinstance(config, dict):
            self.config.update({k: v for k, v in config.items() if k in DEFAULT_CONFIG})

        self._db_path = os.path.join(get_astrbot_data_path(), "data_v4.db")
        self._root = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self._snap_dir = self._root / "snapshots"
        self._index_path = self._root / "index.json"
        self._index: dict = {"last_hash": {}, "items": []}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._last_error = ""

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def initialize(self) -> None:
        if not self._cfg("enabled"):
            logger.info("[persona-snapshot] 未启用")
            return
        try:
            self._snap_dir.mkdir(parents=True, exist_ok=True)
            self._load_index()
            if self._cfg("snapshot_on_start"):
                n = await self._scan_once(reason="startup")
                logger.info(f"[persona-snapshot] 启动基线：新存 {n} 份")
            else:
                logger.info("[persona-snapshot] 已跳过启动基线")
            self._task = asyncio.create_task(self._loop())
            logger.info(
                f"[persona-snapshot] 就绪，每 {self._cfg('check_interval_seconds')}s 检查一次"
                f"，现存 {len(self._index.get('items', []))} 份快照"
            )
        except Exception as e:  # noqa: BLE001
            self._last_error = str(e)
            logger.error(f"[persona-snapshot] 初始化失败: {e}")

    async def terminate(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    def _cfg(self, key: str) -> Any:
        return self.config.get(key, DEFAULT_CONFIG.get(key))

    async def _loop(self) -> None:
        interval = max(10, int(self._cfg("check_interval_seconds")))
        while True:
            await asyncio.sleep(interval)
            try:
                await self._scan_once(reason="poll")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self._last_error = str(e)
                logger.warning(f"[persona-snapshot] 轮询失败: {e}")

    # ------------------------------------------------------------------
    # 索引
    # ------------------------------------------------------------------
    def _load_index(self) -> None:
        try:
            if self._index_path.exists():
                data = json.loads(self._index_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._index = {
                        "last_hash": dict(data.get("last_hash") or {}),
                        "items": list(data.get("items") or []),
                    }
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[persona-snapshot] 索引读取失败，重建: {e}")
            self._index = {"last_hash": {}, "items": []}

    def _save_index(self) -> None:
        try:
            self._index_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._index_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._index, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            tmp.replace(self._index_path)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[persona-snapshot] 索引写入失败: {e}")

    # ------------------------------------------------------------------
    # 数据库
    # ------------------------------------------------------------------
    def _read_personas(self) -> list[dict]:
        if not os.path.exists(self._db_path):
            raise RuntimeError(f"找不到数据库：{self._db_path}")
        conn = sqlite3.connect(self._db_path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT persona_id, system_prompt FROM personas"
            ).fetchall()
            return [
                {
                    "persona_id": r["persona_id"],
                    "system_prompt": r["system_prompt"] or "",
                }
                for r in rows
            ]
        finally:
            conn.close()

    def _write_persona(self, persona_id: str, prompt: str) -> None:
        conn = sqlite3.connect(self._db_path, timeout=10)
        try:
            conn.execute(
                "UPDATE personas SET system_prompt = ?, updated_at = ? WHERE persona_id = ?",
                (prompt, datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"), persona_id),
            )
            conn.commit()
        finally:
            conn.close()

    async def _apply_persona(self, persona_id: str, prompt: str) -> str:
        """把人格写进去，并让它**立刻生效**。

        直连 sqlite 只改库——PersonaManager 把 personas_v3 缓存在内存里，
        只有走 update_persona 才会重建。之前这里只写了库，所以"回滚成功"
        其实只是文件变了，她嘴上还是原来那版。先试内存，不行再退回直写。
        """
        mgr = getattr(self.context, "persona_manager", None)
        if mgr is not None and hasattr(mgr, "update_persona"):
            try:
                await mgr.update_persona(persona_id, system_prompt=prompt)
                return "api"
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[persona-snapshot] 走 API 写人格失败，退回直写库: {e}")
        await asyncio.to_thread(self._write_persona, persona_id, prompt)
        return "sqlite"

    def _read_one(self, persona_id: str) -> str | None:
        conn = sqlite3.connect(self._db_path, timeout=10)
        try:
            row = conn.execute(
                "SELECT system_prompt FROM personas WHERE persona_id = ?", (persona_id,)
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 快照
    # ------------------------------------------------------------------
    def _write_snapshot(self, persona_id: str, prompt: str, h: str, ts: float) -> dict:
        stamp = datetime.fromtimestamp(ts).strftime("%Y%m%d-%H%M%S")
        sub = self._snap_dir / _safe(persona_id)
        sub.mkdir(parents=True, exist_ok=True)
        fname = f"{stamp}_{h[:8]}.txt"
        path = sub / fname
        path.write_text(prompt, encoding="utf-8")

        item = {
            # 加 hash 短码：只用秒级时间戳会在同一秒内连续存档时撞号，
            # 撞号会让回滚永远命中第一条、裁剪被 id 去重吃掉
            "id": f"{persona_id}@{stamp}_{h[:6]}",
            "persona_id": persona_id,
            "ts": ts,
            "hash": h,
            "chars": len(prompt),
            "file": str(path.relative_to(self._snap_dir)).replace("\\", "/"),
            "preview": prompt.strip().replace("\n", " ")[:60],
        }
        return item

    def _prune(self, persona_id: str) -> int:
        """保留最新 N 份，返回删掉几份。"""
        cap = max(1, int(self._cfg("max_snapshots_per_persona")))
        mine = [i for i in self._index["items"] if i.get("persona_id") == persona_id]
        if len(mine) <= cap:
            return 0
        mine.sort(key=lambda x: x.get("ts", 0))
        drop = mine[: len(mine) - cap]
        # 用对象身份删除，不用 id —— 历史索引里可能有旧格式的重复 id
        drop_objs = {id(d) for d in drop}
        for d in drop:
            try:
                (self._snap_dir / d["file"]).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        self._index["items"] = [
            i for i in self._index["items"] if id(i) not in drop_objs
        ]
        return len(drop)

    async def _scan_once(self, reason: str = "poll") -> int:
        """检查所有 persona，内容变了的先存旧版本。返回新存份数。"""
        async with self._lock:
            rows = await asyncio.to_thread(self._read_personas)
            now = time.time()
            added = 0
            for r in rows:
                pid = r["persona_id"]
                prompt = r["system_prompt"]
                h = _sha(prompt)
                prev = self._index["last_hash"].get(pid)
                if prev == h:
                    continue
                # 内容变了（或第一次见）→ 存当前这份
                item = self._write_snapshot(pid, prompt, h, now)
                self._index["items"].append(item)
                self._index["last_hash"][pid] = h
                self._prune(pid)
                added += 1
                logger.info(
                    f"[persona-snapshot] {reason}: {pid} 存档 {item['id']}"
                    f"（{item['chars']} 字）"
                )
            if added:
                self._save_index()
            return added

    # ------------------------------------------------------------------
    # 闸门
    # ------------------------------------------------------------------
    TOOL_NAMES = ["persona_snapshots", "persona_restore"]

    def _allowed(self, event: AstrMessageEvent) -> bool:
        if not self._cfg("admin_only"):
            return True
        try:
            return event.is_admin() is True and event.is_private_chat() is True
        except Exception:  # noqa: BLE001
            return False

    @filter.on_llm_request(priority=102)
    async def gate_tools(self, event: AstrMessageEvent, req: Any) -> None:
        if not self._cfg("enabled") or self._allowed(event):
            return
        tool_set = getattr(req, "func_tool", None)
        if tool_set is None or not hasattr(tool_set, "remove_tool"):
            return
        for name in self.TOOL_NAMES:
            try:
                if tool_set.get_tool(name) is not None:
                    tool_set.remove_tool(name)
            except Exception:  # noqa: BLE001
                continue

    @filter.on_llm_request(priority=-820)
    async def inject_hint(self, event: AstrMessageEvent, req: Any) -> None:
        if not self._cfg("enabled") or not self._cfg("inject_hint"):
            return
        if not self._allowed(event):
            return
        try:
            from astrbot.core.agent.message import TextPart

            parts = getattr(req, "extra_user_content_parts", None)
            if parts is None:
                return
            part = TextPart(text=HINT_TEXT)
            if hasattr(part, "mark_as_temp"):
                part = part.mark_as_temp()
            parts.append(part)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[persona-snapshot] 注入失败: {e}")

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    @filter.llm_tool(name="persona_snapshots")
    async def persona_snapshots(self, event: AstrMessageEvent, persona_id: str = "") -> str:
        """看看自己的人格以前存过哪些版本。

        改人格之前或之后都可以看一眼，确认有没有退路。

        Args:
            persona_id (string): 可选。只看某个人格，留空看全部
        Returns:
            str: 快照清单，含编号、时间、字数
        """
        if not self._cfg("enabled"):
            return "人格快照没开。"
        if not self._allowed(event):
            return "这个只有管理员私聊时能用。"
        try:
            await self._scan_once(reason="manual-list")
            items = list(self._index.get("items") or [])
            if persona_id:
                items = [i for i in items if i.get("persona_id") == persona_id]
            if not items:
                return "还没有存过任何快照。"
            items.sort(key=lambda x: x.get("ts", 0), reverse=True)
            lines = [f"一共 {len(items)} 份快照（新的在上面）："]
            for i in items[:20]:
                t = datetime.fromtimestamp(i.get("ts", 0)).strftime("%m-%d %H:%M")
                lines.append(
                    f"· {i['id']}　{t}　{i.get('chars', 0)} 字　{i.get('preview', '')[:28]}"
                )
            if len(items) > 20:
                lines.append(f"……还有 {len(items) - 20} 份更旧的，按人格分开看：persona_snapshots(\"人格名\")")
            lines.append("\n想退回哪一份，用 persona_restore 把它的编号传进去。")
            return "\n".join(lines)
        except Exception as e:  # noqa: BLE001
            return f"读快照失败：{e}"

    @filter.llm_tool(name="persona_restore")
    async def persona_restore(
        self, event: AstrMessageEvent, snapshot_id: str, confirm: bool = False
    ) -> str:
        """把自己的人格退回到以前存的某一版。

        退之前会先把现在的版本也存一份，所以退错了还能再退回来。

        Args:
            snapshot_id (string): persona_snapshots 里看到的那个编号，比如 蓝色大肥鱼v21@20261019-120140
            confirm (boolean): 确认执行。第一次调用会被要求确认，看清楚后再带 true 调一次
        Returns:
            str: 结果
        """
        if not self._cfg("enabled"):
            return "人格快照没开。"
        if not self._allowed(event):
            return "这个只有管理员私聊时能用。"

        sid = str(snapshot_id or "").strip()
        if not sid:
            return "要退到哪一份？先调 persona_snapshots 看看编号。"

        item = next((i for i in self._index.get("items", []) if i.get("id") == sid), None)
        if item is None:
            # 允许只给时间戳后缀
            cand = [i for i in self._index.get("items", []) if str(i.get("id", "")).endswith(sid)]
            if len(cand) == 1:
                item = cand[0]
            else:
                return f"没找到编号 {sid}。先调 persona_snapshots 看看有哪些。"

        pid = item["persona_id"]
        try:
            path = self._snap_dir / item["file"]
            if not path.exists():
                return f"快照文件不见了：{item['file']}"
            target = path.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            return f"读快照文件失败：{e}"

        current = await asyncio.to_thread(self._read_one, pid)
        if current is None:
            return f"数据库里没有 {pid} 这个人格。"

        t = datetime.fromtimestamp(item.get("ts", 0)).strftime("%Y-%m-%d %H:%M")
        if self._cfg("restore_requires_confirm") and not confirm:
            return (
                f"要退回的是：{pid}\n"
                f"存档时间：{t}（{item.get('chars', 0)} 字）\n"
                f"开头：{target.strip()[:60]}…\n\n"
                f"现在这一版会先存一份，然后才替换。确认要退就再调一次，把 confirm 传 true。"
            )

        try:
            async with self._lock:
                # 先把当前版本存一份，防止退错
                h_now = _sha(current)
                if self._index["last_hash"].get(pid) != h_now:
                    it = self._write_snapshot(pid, current, h_now, time.time())
                    self._index["items"].append(it)
                    self._index["last_hash"][pid] = h_now
                    self._prune(pid)
                how = await self._apply_persona(pid, target)
                self._index["last_hash"][pid] = _sha(target)
                self._save_index()
            logger.info(f"[persona-snapshot] 已回滚 {pid} 到 {sid}（写入方式 {how}）")
            return (
                f"退回去了：{pid} 现在是 {t} 那一版（{len(target)} 字），已经生效。\n"
                f"原来那一版也存下来了，要是退错了还能再退回来。"
            )
        except Exception as e:  # noqa: BLE001
            return f"回滚失败：{e}"

    # ------------------------------------------------------------------
    # 指令
    # ------------------------------------------------------------------
    @filter.command("人格快照")
    async def cmd_list(self, event: AstrMessageEvent):
        """手看快照清单。"""
        if not self._allowed(event):
            yield event.plain_result("这个只有管理员私聊时能用。")
            return
        yield event.plain_result(await self.persona_snapshots(event, ""))
