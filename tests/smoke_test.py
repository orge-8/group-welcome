"""冒烟测试：不启动 MaiBot，用 FakeHost 跑完插件生命周期与两条信号路径。

运行: python tests/smoke_test.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

PLUGIN_DIR = pathlib.Path(__file__).resolve().parent.parent

GROUP_ID = "123456"
OTHER_GROUP = "654321"


def _group_message(
    *,
    group_id: str = GROUP_ID,
    user_id: str = "10001",
    nick: str = "小明",
    text: str = "大家好",
) -> dict:
    """按 runtime-gotchas §29 的落点构造群聊入站载荷。"""
    return {
        "session_id": f"stream-{group_id}",
        "raw_message": text,
        "message_info": {
            "group_info": {"group_id": group_id},
            "user_info": {
                "user_id": user_id,
                "user_cardname": nick,
                "user_nickname": nick,
            },
        },
    }


def _notice_message(text: str = "张三加入了群聊", *, group_id: str = GROUP_ID) -> dict:
    """入群提示：发送者是系统号，新人昵称只在文本里。"""
    return {
        "session_id": f"stream-{group_id}",
        "raw_message": text,
        "message_info": {
            "group_info": {"group_id": group_id},
            "user_info": {"user_id": "99999", "user_nickname": "系统消息"},
        },
    }


async def _drain() -> None:
    """detect_newcomer 里的唤醒动作走后台任务，等它跑完。"""
    for _ in range(20):
        await asyncio.sleep(0)


def main() -> int:
    try:
        import maibot_sdk  # noqa: F401
    except Exception:
        print("SKIP: 未安装 maibot-plugin-sdk，跳过冒烟测试（这不代表通过）")
        return 0

    from fakehost import (
        FakeHost,
        bind_context,
        build_context,
        get_default_config,
        load_plugin_module,
    )

    module = load_plugin_module(PLUGIN_DIR)
    plugin = module.create_plugin()
    host = FakeHost(
        plugin_id="org.orge-8.group-welcome",
        returns={"maisaka.proactive.trigger": {"success": True}},
    )
    ctx = build_context("org.orge-8.group-welcome", rpc_call=host.rpc_call)
    bind_context(plugin, ctx, get_default_config(getattr(type(plugin), "config_model", None)))

    async def run() -> None:
        await plugin.on_load()
        assert plugin.config.plugin.enabled is True, "默认配置未生效"

        # 隔离：清掉上次运行可能留下的档案，保证测试幂等
        plugin._seen.clear()
        plugin._last_trigger.clear()
        plugin._hour_window.clear()
        cfg = plugin.config.welcome
        cfg.warmup_seconds = 0
        cfg.cooldown_seconds = 0

        triggers = lambda: host.calls_of("maisaka.proactive.trigger")  # noqa: E731

        # ---- 1. 组件必须被正确注册（防「辅助方法插队」静默错绑）----
        try:
            from maibot_sdk.components import collect_components

            handlers = set()
            for comp in collect_components(plugin):
                meta = comp.get("metadata") or {}
                name = meta.get("handler_name") or comp.get("handler_name") or ""
                if name:
                    handlers.add(name)
            assert "detect_newcomer" in handlers, f"hook 未注册: {handlers}"
            assert "cmd_welcome" in handlers, f"命令未注册: {handlers}"
        except ImportError:
            pass  # 老 SDK 无 collect_components，跳过该项

        # ---- 2. 入群提示路径（信号 A，优先）----
        await plugin.detect_newcomer(message=_notice_message("张三加入了群聊"))
        await _drain()
        assert len(triggers()) == 1, f"入群提示未唤醒 Planner: {host.calls}"
        intent = str(triggers()[0].get("intent") or "")
        assert "张三" in intent, f"意图未带上新人昵称: {intent}"
        assert "10001" not in intent, "入群提示路径不应把系统号当新人"

        # ---- 3. 首次发言路径（信号 B，兜底）----
        await plugin.detect_newcomer(message=_group_message(user_id="20001", text="新人报道"))
        await _drain()
        assert len(triggers()) == 2, f"首次发言未唤醒 Planner: {host.calls}"

        # ---- 4. 同一人再次发言：不重复欢迎 ----
        await plugin.detect_newcomer(message=_group_message(user_id="20001", text="再问一句"))
        await _drain()
        assert len(triggers()) == 2, "同一成员被重复欢迎，去重失效"

        # ---- 5. 私聊（无群上下文）：不触发 ----
        await plugin.detect_newcomer(message={"session_id": "p1", "raw_message": "你好"})
        await _drain()
        assert len(triggers()) == 2, "私聊消息不应触发群欢迎"

        # ---- 6. 冷却生效 ----
        cfg.cooldown_seconds = 3600
        await plugin.detect_newcomer(message=_group_message(user_id="30001", text="我来啦"))
        await _drain()
        assert len(triggers()) == 2, "冷却未生效"

        # ---- 7. 预热期：只约束「首次发言」路径（入群提示不受影响，见第 11 项）----
        cfg.cooldown_seconds = 0
        cfg.warmup_seconds = 3600
        import time as _time

        plugin._loaded_at = _time.time()
        await plugin.detect_newcomer(
            message=_group_message(group_id=OTHER_GROUP, user_id="40001", text="冒个泡")
        )
        await _drain()
        assert len(triggers()) == 2, "预热期不应欢迎首次发言者"
        assert "40001" in plugin._seen.get(OTHER_GROUP, {}), "预热期仍应登记成员"
        cfg.warmup_seconds = 0

        # ---- 8. 管理命令 ----
        ok, _, _ = await plugin.cmd_welcome(matched_groups={"action": "status"}, stream_id="s")
        assert ok is True, "status 命令失败"
        assert any("欢迎开关" in (t or "") for t in host.sent_texts), "status 未输出"

        ok, _, _ = await plugin.cmd_welcome(matched_groups={"action": "off"}, stream_id="s")
        assert ok is True and plugin.config.welcome.enabled is False, "off 命令未生效"
        await plugin.detect_newcomer(message=_group_message(group_id="777", user_id="50001"))
        await _drain()
        assert len(triggers()) == 2, "关闭后仍触发了欢迎"

        ok, _, _ = await plugin.cmd_welcome(matched_groups={"action": "on"}, stream_id="s")
        assert ok is True and plugin.config.welcome.enabled is True, "on 命令未生效"

        ok, _, _ = await plugin.cmd_welcome(
            matched_groups={"action": "reset", "arg": GROUP_ID}, stream_id="s"
        )
        assert ok is True and not plugin._seen.get(GROUP_ID), "reset 未清空档案"

        ok, _, _ = await plugin.cmd_welcome(
            matched_groups={"action": "test", "arg": GROUP_ID}, stream_id="s"
        )
        assert ok is True and len(triggers()) == 3, "test 未手动触发"

        # ---- 9. 权限：配置了 admin_ids 后非管理员被拒 ----
        plugin.config.welcome.admin_ids = ["123456789"]
        ok, _, _ = await plugin.cmd_welcome(
            matched_groups={"action": "status"}, stream_id="s", user_id="999"
        )
        assert ok is False, "非管理员未被拒绝"
        ok, _, _ = await plugin.cmd_welcome(
            matched_groups={"action": "status"}, stream_id="s", user_id="qq:123456789"
        )
        assert ok is True, "管理员带 qq: 前缀未被放行"

        # ---- 10. manifest 必须声明实际用到的能力（防漏声明）----
        manifest = json.loads(
            (PLUGIN_DIR / "_manifest.json").read_text(encoding="utf-8-sig")
        )
        caps = set(manifest.get("capabilities") or [])
        required = {"send.text", "maisaka.proactive.trigger", "chat.get_stream_by_group_id"}
        assert required <= caps, f"capabilities 缺声明: {required - caps}"

        # ---- 11. 预热期不抑制入群提示（真机 2026-09-19 事故回归）----
        plugin.config.welcome.admin_ids = []
        cfg.warmup_seconds = 3600
        plugin._loaded_at = __import__("time").time()
        before = len(triggers())
        await plugin.detect_newcomer(
            message=_notice_message("王五加入了群聊", group_id=OTHER_GROUP)
        )
        await _drain()
        assert len(triggers()) == before + 1, "预热期吞掉了明确的入群提示"
        cfg.warmup_seconds = 0

        await plugin.on_unload()

    asyncio.run(run())
    print("smoke: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
