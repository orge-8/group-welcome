"""group-welcome 离线单测：只覆盖同步纯逻辑，不依赖 pytest-asyncio。

运行: python -m pytest -q tests
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

TESTS_DIR = pathlib.Path(__file__).resolve().parent
PLUGIN_DIR = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))

from fakehost import (  # noqa: E402
    FakeHost,
    bind_context,
    build_context,
    get_default_config,
    load_plugin_module,
)

PLUGIN_ID = "org.orge-8.group-welcome"


@pytest.fixture()
def plugin():
    """绑定 FakeHost 的插件实例（不调 on_load，避免落盘副作用）。"""
    module = load_plugin_module(PLUGIN_DIR)
    instance = module.create_plugin()
    host = FakeHost(
        plugin_id=PLUGIN_ID,
        returns={"maisaka.proactive.trigger": {"success": True}},
    )
    ctx = build_context(PLUGIN_ID, rpc_call=host.rpc_call)
    bind_context(instance, ctx, get_default_config(getattr(type(instance), "config_model", None)))
    instance._test_host = host  # 供断言 capability 调用记录
    return instance


def _group_message(
    group_id: str = "123456",
    user_id: str = "10001",
    nick: str = "小明",
    text: str = "大家好",
) -> dict:
    return {
        "session_id": f"stream-{group_id}",
        "raw_message": text,
        "message_info": {
            "group_info": {"group_id": group_id},
            "user_info": {"user_id": user_id, "user_cardname": nick, "user_nickname": nick},
        },
    }


# --------------------------------------------------------------- 入群提示识别


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("张三加入了群聊", "张三"),
        ("李四加入了本群", "李四"),
        ("欢迎新成员 王五", "王五"),
        ("赵六通过扫描二维码加入了本群", "赵六"),
        ("孙七被管理员邀请加入了本群", "孙七"),
    ],
)
def test_notice_patterns_extract_nick(plugin, text: str, expected: str) -> None:
    assert plugin._match_notice(text) == expected


@pytest.mark.parametrize(
    "text",
    ["今天天气不错", "有人打游戏吗", "", "哈哈哈", "我加入了新项目组"],
)
def test_notice_patterns_reject_normal_text(plugin, text: str) -> None:
    """普通聊天不能被误判成入群提示，否则每次发言都会唤醒 Planner。"""
    assert plugin._match_notice(text) == ""


def test_notice_blocked_by_bad_regex_does_not_crash(plugin) -> None:
    """非法正则应跳过而不是抛异常。"""
    plugin.config.welcome.notice_patterns = ["(?P<nick>", r"(?P<nick>\S+)加入了群聊"]
    assert plugin._match_notice("张三加入了群聊") == "张三"


def test_notice_patterns_empty_disables_signal(plugin) -> None:
    plugin.config.welcome.notice_patterns = []
    assert plugin._match_notice("张三加入了群聊") == ""


def test_looks_like_notice_filters_system_messages(plugin) -> None:
    assert plugin._looks_like_notice("张三 撤回了一条消息") is True
    assert plugin._looks_like_notice("今天天气不错") is False


# --------------------------------------------------------------- 载荷解析


def test_extract_group_message(plugin) -> None:
    payload = plugin._extract(_group_message())
    assert payload is not None
    assert payload["group_id"] == "123456"
    assert payload["user_id"] == "10001"
    assert payload["nickname"] == "小明"
    assert payload["stream_id"] == "stream-123456"


def test_extract_returns_none_for_private_chat(plugin) -> None:
    """私聊没有群上下文，必须直接跳过。"""
    assert plugin._extract({"session_id": "p1", "raw_message": "你好"}) is None


def test_extract_falls_back_to_nickname_when_card_empty(plugin) -> None:
    """群名片为空是常态，必须回退到昵称，否则建不了档。"""
    message = _group_message()
    message["message_info"]["user_info"]["user_cardname"] = ""
    message["message_info"]["user_info"]["user_nickname"] = "昵称君"
    payload = plugin._extract(message)
    assert payload is not None and payload["nickname"] == "昵称君"


def test_extract_falls_back_to_nested_alternative_keys(plugin) -> None:
    """字段名随适配器版本浮动，要多路径兜底。"""
    message = {
        "session_id": "s1",
        "raw_message": "hi",
        "message_info": {
            "group_info": {"group": "888"},
            "user_info": {"sender_id": "555", "card": "名片党"},
        },
    }
    payload = plugin._extract(message)
    assert payload is not None
    assert payload["group_id"] == "888"
    assert payload["user_id"] == "555"
    assert payload["nickname"] == "名片党"


def test_extract_without_user_id_still_parses(plugin) -> None:
    payload = plugin._extract(_group_message(user_id=""))
    assert payload is not None and payload["user_id"] == ""


def test_bot_message_detected(plugin) -> None:
    message = _group_message()
    message["message_info"]["user_info"]["is_bot"] = True
    assert plugin._is_bot_message(message) is True
    assert plugin._is_bot_message(_group_message()) is False


# --------------------------------------------------------------- 判定与配置


def test_group_whitelist(plugin) -> None:
    plugin.config.welcome.group_whitelist = []
    assert plugin._group_allowed("1") is True
    plugin.config.welcome.group_whitelist = ["123456"]
    assert plugin._group_allowed("123456") is True
    assert plugin._group_allowed("999") is False


def test_ignore_user_ids_with_qq_prefix(plugin) -> None:
    plugin.config.welcome.ignore_user_ids = ["qq:10001"]
    assert plugin._ignored("10001") is True
    assert plugin._ignored("10002") is False


def test_admin_fail_open_when_unconfigured(plugin) -> None:
    """未配置 admin_ids 时全部放行（fail-open），与既有插件约定一致。"""
    plugin.config.welcome.admin_ids = []
    assert plugin._is_admin({"user_id": "999"}) is True


def test_admin_prefix_and_local_operator(plugin) -> None:
    plugin.config.welcome.admin_ids = ["123456789"]
    assert plugin._is_admin({"user_id": "123456789"}) is True
    assert plugin._is_admin({"user_id": "qq:123456789"}) is True
    assert plugin._is_admin({"user_id": "999"}) is False
    assert plugin._is_admin({"is_local_operator": True}) is True


def test_caller_id_reads_nested_path(plugin) -> None:
    kwargs = {"message": _group_message(user_id="24680")}
    assert plugin._caller_id(kwargs) == "24680"


def test_new_member_detection_and_dedup(plugin) -> None:
    plugin._seen.clear()
    assert plugin._is_new_member("g1", "u1") is True
    plugin._mark_seen("g1", "u1")
    assert plugin._is_new_member("g1", "u1") is False
    assert plugin._is_new_member("g1", "u2") is True
    assert plugin._is_new_member("g2", "u1") is True  # 跨群互不影响


def test_warmup_window(plugin) -> None:
    import time as _time

    plugin.config.welcome.warmup_seconds = 3600
    plugin._loaded_at = _time.time()
    assert plugin._warmup_active() is True
    plugin.config.welcome.warmup_seconds = 0
    assert plugin._warmup_active() is False


def test_cooldown(plugin) -> None:
    import time as _time

    plugin.config.welcome.cooldown_seconds = 60
    plugin._last_trigger["g"] = _time.time()
    assert plugin._cooldown_ok("g") is False
    plugin.config.welcome.cooldown_seconds = 0
    assert plugin._cooldown_ok("g") is True


def test_hourly_quota(plugin) -> None:
    import time as _time

    plugin.config.welcome.max_triggers_per_hour = 2
    plugin._hour_window["g"] = (_time.time(), 0)
    assert plugin._quota_ok("g") is True
    plugin._bump_quota("g")
    plugin._bump_quota("g")
    assert plugin._quota_ok("g") is False
    plugin.config.welcome.max_triggers_per_hour = 0
    assert plugin._quota_ok("g") is True


def test_intent_mentions_nickname_and_source(plugin) -> None:
    intent = plugin._build_intent(nickname="张三", user_id="", source="notice")
    assert "张三" in intent
    intent2 = plugin._build_intent(nickname="小明", user_id="20001", source="first_message")
    assert "小明" in intent2 and "20001" in intent2


def test_intent_declares_event_and_gives_viable_reply_path(plugin) -> None:
    """intent 必须声明「事件通知」，并给出可执行的发言路径。

    真机 2026-09-19 19:14（v1.1.2 修正）：早期措辞「不要引用或回复任何历史消息」
    与 Planner 唯一的发言工具（`reply`，必须带 msg_id）冲突 —— 实测它先后拿
    proactive 任务编号（1789816438111）与时间串（19:13:57）当 msg_id，
    连续两次 reply 失败后才找到真实消息 id。**约束必须留出可行路径。**
    """
    for source in ("notice_payload", "notice", "first_message"):
        intent = plugin._build_intent(nickname="狸猫", user_id="10001", source=source)
        assert "事件通知" in intent, f"intent 未声明是事件通知: {intent}"
        # 判据随 v1.2.2 变更：可行路径 = 关闭引用显示（set_quote=false），
        # 而不再是"选最近一条"—— 后者会被模型执行成"引用无关消息"
        assert "set_quote" in intent, f"intent 未给出可行的回复路径: {intent}"


def test_intent_warns_that_history_notice_may_name_the_wrong_person(plugin) -> None:
    """intent 必须警告「历史里的入群通知文本可能标错人」，并指明以本通知为准。

    真机 2026-09-19 19:39：群内那条「狸猫 加入了群聊」其实是别人入群
    （文本与 `user_info` 被填成了邀请人），而 Planner 同时看到两条矛盾信息时
    信了历史消息那条 —— `reply_reference` 写成"现在狸猫正式加入群聊"，欢迎对象搞错。
    """
    intent = plugin._build_intent(
        nickname="2336884608", user_id="2336884608", source="notice_payload"
    )
    assert "可能把入群者标成" in intent, f"intent 未警告历史通知不可信: {intent}"
    assert "以本通知" in intent, f"intent 未指明以哪个信息源为准: {intent}"


# --------------------------------------------------------------- 状态落盘


def test_state_roundtrip(plugin) -> None:
    """落盘后应由新实例读回。必须复用同一个 ctx —— FakePaths 每次 build 都换临时根目录。"""
    plugin._seen.clear()
    plugin._mark_seen("123456", "10001")
    plugin._save_state(force=True)
    assert plugin._state_path().is_file(), "状态文件未写出"

    fresh = load_plugin_module(PLUGIN_DIR).create_plugin()
    bind_context(fresh, plugin.ctx, get_default_config(getattr(type(fresh), "config_model", None)))
    fresh._load_state()
    assert "10001" in fresh._seen.get("123456", {})


def test_corrupted_state_is_ignored(plugin) -> None:
    """状态文件损坏时应从干净状态启动，而不是抛异常。"""
    path = plugin._state_path()
    assert path is not None
    path.write_text("{ this is not json", encoding="utf-8")
    plugin._seen.clear()
    plugin._load_state()
    assert plugin._seen == {}


# --------------------------------------------------------------- 契约反查


def test_manifest_declares_capabilities() -> None:
    """反向验证：用了的能力必须写进 manifest，否则真机 E_CAPABILITY_DENIED。"""
    manifest = json.loads((PLUGIN_DIR / "_manifest.json").read_text(encoding="utf-8-sig"))
    caps = set(manifest.get("capabilities") or [])
    assert {"send.text", "maisaka.proactive.trigger", "chat.get_stream_by_group_id"} <= caps


def test_manifest_has_no_bom_and_required_fields() -> None:
    raw = (PLUGIN_DIR / "_manifest.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "manifest 不能带 UTF-8 BOM"
    manifest = json.loads(raw.decode("utf-8"))
    for key in ("manifest_version", "id", "version", "name", "description", "sdk", "capabilities"):
        assert key in manifest, f"manifest 缺字段: {key}"


def test_components_registered() -> None:
    """断言 Runner 眼里的真实声明，抓「装饰器被辅助方法插队」。"""
    module = load_plugin_module(PLUGIN_DIR)
    instance = module.create_plugin()
    from maibot_sdk.components import collect_components

    names = set()
    for comp in collect_components(instance):
        meta = comp.get("metadata") or {}
        name = meta.get("handler_name") or comp.get("handler_name") or ""
        if name:
            names.add(name)
    assert {"detect_newcomer", "cmd_welcome"} <= names, f"组件未注册: {names}"


def test_plugin_entrypoint_and_lifecycle_methods() -> None:
    module = load_plugin_module(PLUGIN_DIR)
    assert callable(module.create_plugin)
    instance = module.create_plugin()
    for method in ("on_load", "on_unload", "on_config_update"):
        assert callable(getattr(instance, method, None)), f"缺生命周期方法: {method}"


# --------------------------------------------------------------- 真机载荷回归
# 依据 2026-09-19 真机日志重建。日志原文：
#   [file-reader 诊断] mid='napcat-notice-9532db6b5c3e40db846807a4e80601d0'
#       raw=list[text(str='狸猫 修改了群名称')] plain='狸猫 修改了群名称'
#   [所见] [bot测试]狸猫:狸猫 加入了群聊
REAL_GROUP_ID = "1107603097"
REAL_SESSION = "8191e2488c1fce32f338d3054b1d9e5e"
REAL_MID = "napcat-notice-9532db6b5c3e40db846807a4e80601d0"


def _real_notice(text: str, message_id: str = REAL_MID) -> dict:
    """真机 notice 载荷：raw_message 是分段 list、带 is_notify、mid 有 napcat-notice- 前缀。"""
    return {
        "is_at": False,
        "is_command": False,
        "is_emoji": False,
        "is_mentioned": False,
        "is_notify": True,
        "is_picture": False,
        "message_id": message_id,
        "message_info": {
            "additional_config": {},
            "group_info": {"group_id": REAL_GROUP_ID},
            "user_info": {"user_id": "3816023959", "user_cardname": "", "user_nickname": "狸猫"},
        },
        "platform": "qq",
        "processed_plain_text": text,
        "raw_message": [{"type": "text", "data": {"text": text}}],
        "session_id": REAL_SESSION,
        "timestamp": 1789807899,
    }


def test_real_notice_payload_extracts_everything(plugin) -> None:
    assert plugin._extract(_real_notice("狸猫 加入了群聊")) == {
        "group_id": REAL_GROUP_ID,
        "user_id": "3816023959",
        "nickname": "狸猫",
        "text": "狸猫 加入了群聊",
        "stream_id": REAL_SESSION,
    }


def test_extract_text_falls_back_to_segmented_raw_message(plugin) -> None:
    """真机 raw_message 是分段 list；processed_plain_text 缺失时必须仍能取到文本。"""
    message = _real_notice("狸猫 加入了群聊")
    message.pop("processed_plain_text")
    assert plugin._extract_text(message) == "狸猫 加入了群聊"


def test_extract_text_prefers_plain_text_over_raw(plugin) -> None:
    message = _real_notice("狸猫 加入了群聊")
    message["processed_plain_text"] = "纯文本优先"
    assert plugin._extract_text(message) == "纯文本优先"


def test_notice_detected_via_is_notify_flag(plugin) -> None:
    message = _real_notice("狸猫 加入了群聊")
    assert plugin._is_notice_message(message, "狸猫 加入了群聊") is True


def test_notice_detected_via_message_id_prefix(plugin) -> None:
    """即使适配器不给 is_notify，mid 前缀也应能兜住。"""
    message = _real_notice("狸猫 加入了群聊")
    message.pop("is_notify")
    assert plugin._is_notice_message(message, "狸猫 加入了群聊") is True


def test_group_rename_notice_is_not_a_newcomer(plugin) -> None:
    """「修改了群名称」是通知但不是入群，绝不能当新人欢迎。"""
    message = _real_notice("狸猫 修改了群名称")
    assert plugin._is_notice_message(message, "狸猫 修改了群名称") is True
    assert plugin._match_notice("狸猫 修改了群名称") == ""


def test_warmup_does_not_block_notice_path(plugin) -> None:
    """真机 2026-09-19 事故回归：预热期不得吞掉明确的入群提示。"""
    import asyncio
    import time as _time

    host = plugin._test_host
    plugin.config.welcome.warmup_seconds = 3600
    plugin.config.welcome.cooldown_seconds = 0
    plugin._loaded_at = _time.time()  # 刚加载，处于预热期内
    assert plugin._warmup_active() is True

    async def run() -> None:
        await plugin.detect_newcomer(message=_real_notice("狸猫 加入了群聊"))
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert len(host.calls_of("maisaka.proactive.trigger")) == 1, "预热期吞掉了入群提示"


def test_warmup_still_blocks_first_message_path(plugin) -> None:
    """首次发言路径无法区分老成员，预热期仍应只登记不欢迎。"""
    import asyncio
    import time as _time

    host = plugin._test_host
    plugin.config.welcome.warmup_seconds = 3600
    plugin.config.welcome.cooldown_seconds = 0
    plugin._loaded_at = _time.time()

    async def run() -> None:
        await plugin.detect_newcomer(
            message=_group_message(group_id="7001", user_id="60001", text="冒个泡")
        )
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert host.calls_of("maisaka.proactive.trigger") == [], "预热期不应欢迎首次发言者"
    assert "60001" in plugin._seen.get("7001", {}), "预热期仍应登记成员"


def test_rename_notice_does_not_trigger(plugin) -> None:
    """通知类消息（改群名）走完 detect_newcomer 也不应触发唤醒。"""
    import asyncio

    host = plugin._test_host
    plugin.config.welcome.warmup_seconds = 0
    plugin.config.welcome.cooldown_seconds = 0

    async def run() -> None:
        await plugin.detect_newcomer(message=_real_notice("狸猫 修改了群名称"))
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert host.calls_of("maisaka.proactive.trigger") == [], "改群名通知被误判为新人"


def test_notice_uses_session_id_as_stream(plugin) -> None:
    """notice 载荷自带 session_id，可直接作为目标聊天流。"""
    import asyncio

    host = plugin._test_host
    plugin.config.welcome.warmup_seconds = 0
    plugin.config.welcome.cooldown_seconds = 0

    async def run() -> None:
        await plugin.detect_newcomer(message=_real_notice("狸猫 加入了群聊"))
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(run())
    calls = host.calls_of("maisaka.proactive.trigger")
    assert calls and calls[0].get("stream_id") == REAL_SESSION


# --------------------------------------------------------------- 未命中通知的可观测性
# 真机 2026-09-19：同日同群「狸猫」入群被识别，「tori」入群插件毫无反应，
# 而当时未命中的通知只记 debug 级 → 日志里看不到收到了什么，无从诊断。


def test_unmatched_notice_is_remembered(plugin) -> None:
    """未识别为入群提示的通知必须留痕，否则"没反应"永远查不出原因。"""
    import asyncio

    plugin.config.welcome.warmup_seconds = 0
    plugin.config.welcome.cooldown_seconds = 0
    plugin._recent_notices.clear()

    async def run() -> None:
        await plugin.detect_newcomer(message=_real_notice("狸猫 修改了群名称"))
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert len(plugin._recent_notices) == 1, "未命中的系统通知没有被记录"
    assert plugin._recent_notices[0]["text"] == "狸猫 修改了群名称"


def test_recent_notices_is_capped(plugin) -> None:
    module = load_plugin_module(PLUGIN_DIR)
    plugin._recent_notices.clear()
    for i in range(module.MAX_RECENT_NOTICES + 10):
        plugin._remember_notice("g", f"mid-{i}", f"通知 {i}")
    assert len(plugin._recent_notices) == module.MAX_RECENT_NOTICES
    assert plugin._recent_notices[-1]["text"] == f"通知 {module.MAX_RECENT_NOTICES + 9}"


def test_diag_text_lists_recent_notices(plugin) -> None:
    plugin._recent_notices.clear()
    plugin._remember_notice(REAL_GROUP_ID, "napcat-notice-x", "tori 申请加入群聊")
    text = plugin._diag_text()
    assert "tori 申请加入群聊" in text
    assert REAL_GROUP_ID in text


def test_diag_text_empty_hint_points_to_file_reader(plugin) -> None:
    """缓存为空时要给出可执行的下一步，而不是只显示空。"""
    plugin._recent_notices.clear()
    text = plugin._diag_text()
    assert "暂无记录" in text
    assert "file-reader" in text, "空缓存时未提示对照 file-reader 日志"


def test_cmd_welcome_diag_action(plugin) -> None:
    import asyncio

    plugin.config.welcome.admin_ids = []
    plugin._recent_notices.clear()
    plugin._remember_notice("g", "mid-1", "有人申请入群")

    ok, tag, _ = asyncio.run(
        plugin.cmd_welcome(matched_groups={"action": "diag"}, stream_id="s")
    )
    assert ok is True and tag == "diag"


def test_cmd_welcome_accepts_chinese_action_alias(plugin) -> None:
    import asyncio

    plugin.config.welcome.admin_ids = []

    ok, tag, _ = asyncio.run(
        plugin.cmd_welcome(matched_groups={"action": "诊断"}, stream_id="s")
    )
    assert ok is True and tag == "diag", "中文动作别名未生效"


def test_command_pattern_covers_diag_and_chinese() -> None:
    import re

    module = load_plugin_module(PLUGIN_DIR)
    info = module.GroupWelcome.cmd_welcome.__maibot_component_info__
    for raw in ("/欢迎", "/欢迎 diag", "/欢迎 诊断", "/welcome diag", "/欢迎 状态"):
        assert re.fullmatch(info.command_pattern, raw), f"命令未匹配: {raw}"


# --------------------------------------------------------------- 命令消息隔离
# 真机 2026-09-19：管理员发 `/欢迎 diag`，因该 QQ 未在 seen 档案中，
# 被「首次发言」路径当成新人候选（恰在预热期才只登记未欢迎）。


def test_command_message_is_skipped(plugin) -> None:
    import asyncio

    host = plugin._test_host
    plugin.config.welcome.warmup_seconds = 0
    plugin.config.welcome.cooldown_seconds = 0
    plugin._seen.clear()

    message = _group_message(group_id="7100", user_id="61001", text="/欢迎 diag")
    message["is_command"] = True

    async def run() -> None:
        await plugin.detect_newcomer(message=message)
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert host.calls_of("maisaka.proactive.trigger") == [], "命令消息触发了欢迎"
    assert "61001" not in plugin._seen.get("7100", {}), "命令消息被登记为成员"


def test_command_detected_by_text_when_flag_missing(plugin) -> None:
    assert plugin._is_command_message(_group_message(text="/欢迎")) is True
    assert plugin._is_command_message(_group_message(text="／欢迎 状态")) is True
    assert plugin._is_command_message(_group_message(text="大家好")) is False
    assert plugin._is_command_message(_group_message(text="你说的/那个")) is False


def test_command_flag_is_honored(plugin) -> None:
    message = _group_message(text="随便什么")
    message["is_command"] = True
    assert plugin._is_command_message(message) is True


# --------------------------------------------------------------- 命中通知也入缓存
# 真机 2026-09-19：原设计只缓存"未命中"的通知，导致 `/欢迎 diag`
# 在"刚有人入群"时反而显示「暂无记录」，诊断价值被削弱。


def test_matched_notice_is_also_cached(plugin) -> None:
    import asyncio

    plugin.config.welcome.warmup_seconds = 0
    plugin.config.welcome.cooldown_seconds = 0
    plugin._recent_notices.clear()

    async def run() -> None:
        await plugin.detect_newcomer(message=_real_notice("tori 加入了群聊"))
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert len(plugin._recent_notices) == 1, "命中的通知没有入缓存"
    assert plugin._recent_notices[0]["matched"] is True


def test_diag_text_marks_matched_and_unmatched(plugin) -> None:
    plugin._recent_notices.clear()
    plugin._remember_notice("g", "mid-1", "tori 加入了群聊", matched=True)
    plugin._remember_notice("g", "mid-2", "某人 修改了群名称")
    text = plugin._diag_text()
    assert "已识别入群" in text
    assert "未识别为入群" in text


# --------------------------------------------------------------- notice 载荷的 QQ 号
# 真机 2026-09-19 17:24:41 实测：
#   命中入群提示：群=1107603097 昵称=狸猫 载荷user_id='3816023959'
# 即 user_info.user_id 是【入群者本人】，不是系统号。


def test_looks_like_user_id(plugin) -> None:
    assert plugin._looks_like_user_id("3816023959") is True
    assert plugin._looks_like_user_id(" 12345 ") is True
    assert plugin._looks_like_user_id("") is False
    assert plugin._looks_like_user_id("系统消息") is False
    assert plugin._looks_like_user_id("123") is False  # 太短
    assert plugin._looks_like_user_id("1" * 13) is False  # 太长
    assert plugin._looks_like_user_id("qq:12345") is False


def test_notice_intent_carries_qq(plugin) -> None:
    """两条路径都应带上 QQ 号，供 Planner 做身份关联。"""
    intent = plugin._build_intent(nickname="狸猫", user_id="3816023959", source="notice")
    assert "3816023959" in intent, f"notice 路径 intent 未带 QQ: {intent}"
    intent2 = plugin._build_intent(nickname="狸猫", user_id="", source="notice")
    # 用精确的"空标注"形态判定，不要用 `"QQ" not in intent`
    # —— 后者会被"以本通知给出的 QQ 号为准"这类正文误伤（文本匹配型断言陷阱）
    assert "（QQ ）" not in intent2 and "（QQ）" not in intent2, "无 QQ 时不应出现空的 QQ 标注"


def test_notice_registers_newcomer_qq(plugin) -> None:
    """载荷 user_id 可用时应登记档案，避免该成员之后的首次发言被重复欢迎。"""
    import asyncio

    plugin.config.welcome.warmup_seconds = 0
    plugin.config.welcome.cooldown_seconds = 0
    plugin._seen.clear()

    async def run() -> None:
        await plugin.detect_newcomer(message=_real_notice("狸猫 加入了群聊"))
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert "3816023959" in plugin._seen.get(REAL_GROUP_ID, {}), "入群者 QQ 未登记"


def test_notice_with_abnormal_user_id_falls_back(plugin) -> None:
    """载荷 user_id 不像 QQ 号时退回「仅昵称」，不污染档案。"""
    import asyncio

    plugin.config.welcome.warmup_seconds = 0
    plugin.config.welcome.cooldown_seconds = 0
    plugin._seen.clear()

    message = _real_notice("狸猫 加入了群聊")
    message["message_info"]["user_info"]["user_id"] = "系统消息"

    async def run() -> None:
        await plugin.detect_newcomer(message=message)
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert "系统消息" not in plugin._seen.get(REAL_GROUP_ID, {}), "异常 user_id 被写进档案"


# --------------------------------------------------------------- 操作者误标防御
# 真机 2026-09-19：群主「狸猫」从未退群，却两次收到「狸猫 加入了群聊」，
# 时间与「tori 被邀请 / 申请入群」完全吻合 ⇒ 适配器用 operator_id 填充了发送者，
# 通知里的"新人"其实是操作者。


def test_notice_for_known_member_is_skipped(plugin) -> None:
    """通知里的 user_id 若已是本群老成员，判定为误标事件并跳过欢迎。"""
    import asyncio

    host = plugin._test_host
    plugin.config.welcome.warmup_seconds = 0
    plugin.config.welcome.cooldown_seconds = 0
    plugin.config.welcome.skip_known_member_notice = True
    plugin._seen.clear()
    plugin._recent_notices.clear()
    plugin._mark_seen(REAL_GROUP_ID, "3816023959")  # 狸猫已是档案中的老成员

    async def run() -> None:
        await plugin.detect_newcomer(message=_real_notice("狸猫 加入了群聊"))
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert host.calls_of("maisaka.proactive.trigger") == [], "误标通知仍触发了欢迎"
    assert plugin._recent_notices and plugin._recent_notices[-1]["skipped"] is True


def test_notice_for_unknown_member_still_triggers(plugin) -> None:
    """真正的陌生成员入群不受防御影响。"""
    import asyncio

    host = plugin._test_host
    plugin.config.welcome.warmup_seconds = 0
    plugin.config.welcome.cooldown_seconds = 0
    plugin._seen.clear()

    message = _real_notice("tori 加入了群聊")
    message["message_info"]["user_info"]["user_id"] = "1264805399"

    async def run() -> None:
        await plugin.detect_newcomer(message=message)
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert len(host.calls_of("maisaka.proactive.trigger")) == 1, "陌生成员入群未触发"


def test_skip_defense_can_be_disabled(plugin) -> None:
    import asyncio

    host = plugin._test_host
    plugin.config.welcome.warmup_seconds = 0
    plugin.config.welcome.cooldown_seconds = 0
    plugin.config.welcome.skip_known_member_notice = False
    plugin._seen.clear()
    plugin._mark_seen(REAL_GROUP_ID, "3816023959")

    async def run() -> None:
        await plugin.detect_newcomer(message=_real_notice("狸猫 加入了群聊"))
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert len(host.calls_of("maisaka.proactive.trigger")) == 1, "关闭防御后仍未触发"


def test_diag_text_marks_skipped(plugin) -> None:
    plugin._recent_notices.clear()
    plugin._remember_notice("g", "mid-1", "狸猫 加入了群聊", matched=True, skipped=True)
    text = plugin._diag_text()
    assert "疑似误标·已跳过" in text


# --------------------------------------------------------------- 原始 notice 载荷（权威路径）
# 真机 2026-09-19：文本与 user_info 都被填成操作者「狸猫」，
# 只有 message_info.additional_config.napcat_notice_payload.user_id 才是真正的入群者 tori。
# 依据 ji-or-ji/group-awareness-plugin（MIT）的实现。

REAL_SELF_ID = "2472005478"  # bot 自己（鸣澜bot）


def _notice_with_payload(
    *,
    text: str = "狸猫 加入了群聊",
    sender_id: str = "3816023959",       # 被错填成发送者的操作者
    payload_user_id: str = "1264805399",  # 真正的入群者（tori）
    operator_id: str = "3816023959",
    notice_type: str = "group_increase",
) -> dict:
    message = _real_notice(text)
    message["message_info"]["user_info"]["user_id"] = sender_id
    message["message_info"]["additional_config"] = {
        "napcat_notice_type": notice_type,
        "napcat_notice_sub_type": "increase",
        "napcat_notice_payload": {
            "group_id": REAL_GROUP_ID,
            "user_id": payload_user_id,
            "operator_id": operator_id,
            "self_id": REAL_SELF_ID,
        },
    }
    return message


def test_raw_notice_extraction(plugin) -> None:
    raw = plugin._extract_raw_notice(_notice_with_payload())
    assert raw is not None
    assert raw["notice_type"] == "group_increase"
    assert raw["user_id"] == "1264805399"        # tori
    assert raw["operator_id"] == "3816023959"    # 狸猫
    assert raw["self_id"] == REAL_SELF_ID
    assert raw["group_id"] == REAL_GROUP_ID


def test_raw_notice_absent_without_additional_config(plugin) -> None:
    assert plugin._extract_raw_notice(_real_notice("狸猫 加入了群聊")) is None


def test_authoritative_path_welcomes_the_real_newcomer(plugin) -> None:
    """决定性的回归：文本写着「狸猫」（操作者），但真正的新人是 payload 里的 tori。"""
    import asyncio

    host = plugin._test_host
    plugin.config.welcome.warmup_seconds = 0
    plugin.config.welcome.cooldown_seconds = 0
    plugin._seen.clear()

    async def run() -> None:
        await plugin.detect_newcomer(message=_notice_with_payload())
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(run())
    calls = host.calls_of("maisaka.proactive.trigger")
    assert len(calls) == 1, "权威路径未触发"
    intent = str(calls[0].get("intent") or "")
    assert "1264805399" in intent, f"intent 未使用真实新人 QQ: {intent}"
    assert "3816023959" not in intent, f"intent 混入了操作者 QQ: {intent}"
    assert "1264805399" in plugin._seen.get(REAL_GROUP_ID, {}), "真实新人未被登记"


def test_authoritative_path_skips_bot_itself(plugin) -> None:
    import asyncio

    host = plugin._test_host
    plugin.config.welcome.warmup_seconds = 0
    plugin.config.welcome.cooldown_seconds = 0
    plugin._seen.clear()

    async def run() -> None:
        await plugin.detect_newcomer(
            message=_notice_with_payload(payload_user_id=REAL_SELF_ID)
        )
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert host.calls_of("maisaka.proactive.trigger") == [], "bot 自己被当成新人欢迎"


def test_authoritative_path_works_when_user_info_id_is_empty(plugin) -> None:
    """sender 的 user_id 为空时，权威路径仍应工作（不能提前 return）。"""
    import asyncio

    host = plugin._test_host
    plugin.config.welcome.warmup_seconds = 0
    plugin.config.welcome.cooldown_seconds = 0
    plugin._seen.clear()

    async def run() -> None:
        await plugin.detect_newcomer(message=_notice_with_payload(sender_id=""))
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert len(host.calls_of("maisaka.proactive.trigger")) == 1, "权威路径被空 user_id 挡住"


def test_falls_back_to_text_when_payload_missing(plugin) -> None:
    """适配器不提供 additional_config 时，仍应退回文本路径。"""
    import asyncio

    host = plugin._test_host
    plugin.config.welcome.warmup_seconds = 0
    plugin.config.welcome.cooldown_seconds = 0
    plugin._seen.clear()

    async def run() -> None:
        await plugin.detect_newcomer(message=_real_notice("tori 加入了群聊"))
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert len(host.calls_of("maisaka.proactive.trigger")) == 1, "降级路径失效"


def test_decrease_notice_not_treated_as_join(plugin) -> None:
    """退群等其它 notice 不能被权威路径当成入群。"""
    import asyncio

    host = plugin._test_host
    plugin.config.welcome.warmup_seconds = 0
    plugin.config.welcome.cooldown_seconds = 0
    plugin._seen.clear()

    async def run() -> None:
        await plugin.detect_newcomer(
            message=_notice_with_payload(
                text="1264805399 离开了群聊", notice_type="group_decrease"
            )
        )
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert host.calls_of("maisaka.proactive.trigger") == [], "退群通知被当成入群"
