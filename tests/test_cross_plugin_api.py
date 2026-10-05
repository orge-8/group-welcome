# -*- coding: utf-8 -*-
"""跨插件只读 API ``get_recent_newcomers`` 的契约测试（v1.4.0 新增）。

契约纪律（与 README「跨插件 API」一节一一对应）：
- 只读：零网络、零写盘——调用前后 ``_seen`` 与状态文件 mtime 不变；
- 结构永远完整：空/坏档案 → ``groups={}`` + ``reason``，不抛异常；
- 纯 dict：bool/int/float/str/None，可过 msgpack；
- 时间窗过滤、倒序、每群 limit 封顶。
"""

import asyncio
import copy
import pathlib
import sys
import time

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
    host = FakeHost(plugin_id=PLUGIN_ID)
    ctx = build_context(PLUGIN_ID, rpc_call=host.rpc_call)
    bind_context(instance, ctx, get_default_config(getattr(type(instance), "config_model", None)))
    instance._test_host = host
    return instance


def _loaded(plugin):
    """模拟 on_load 已完成（fixture 不跑生命周期）。"""
    plugin._loaded_at = time.time()
    return plugin


def test_empty_state_degrades_with_reason(plugin):
    """空档案：groups={} + reason，不抛异常。"""
    _loaded(plugin)
    out = asyncio.run(plugin.api_get_recent_newcomers())
    assert out["schema_version"] == 1
    assert out["groups"] == {}
    assert out["active"] is True
    assert out["reason"] == "暂无任何成员记录"
    for value in out.values():
        assert value is None or isinstance(value, (bool, int, float, str, dict, list))


def test_not_started_reports_inactive(plugin):
    """on_load 未跑过（状态未加载）：active=False + reason，不猜数据。"""
    out = asyncio.run(plugin.api_get_recent_newcomers())
    assert out["active"] is False
    assert out["groups"] == {}
    assert "未完成启动" in out["reason"]


def test_window_filter_and_desc_order(plugin):
    """3 条不同时间的记录：since_seconds 正确过滤 + 按首次出现时间倒序。"""
    _loaded(plugin)
    now = time.time()
    _seed = plugin._seen.setdefault("123456", {})
    _seed["10001"] = now - 10        # 窗口内（最新）
    _seed["10002"] = now - 3600      # 窗口内
    _seed["10003"] = now - 100000.0  # 窗口外（默认 86400）

    out = asyncio.run(plugin.api_get_recent_newcomers())
    group = out["groups"]["123456"]
    assert [m["user_id"] for m in group["new_members"]] == ["10001", "10002"]
    assert group["new_members"][0]["first_seen"] >= group["new_members"][1]["first_seen"]
    assert group["last_welcome_at"] == 0.0, "从未欢迎过应为 0.0 哨兵"

    # since_seconds=0 → 不限窗口，三条全回
    out_all = asyncio.run(plugin.api_get_recent_newcomers(since_seconds=0))
    assert [m["user_id"] for m in out_all["groups"]["123456"]["new_members"]] == [
        "10001", "10002", "10003",
    ]


def test_limit_per_group_capped(plugin):
    """limit 为每群封顶：5 条 limit=2 → 最新 2 条；超界钳到 1..100。"""
    _loaded(plugin)
    now = time.time()
    members = plugin._seen.setdefault("123456", {})
    for i in range(5):
        members[f"u{i}"] = now - (i + 1) * 10  # u0 最新

    out = asyncio.run(plugin.api_get_recent_newcomers(limit=2))
    assert [m["user_id"] for m in out["groups"]["123456"]["new_members"]] == ["u0", "u1"]

    # 105 条 + limit=1000 → 钳到 100
    big = plugin._seen.setdefault("777", {})
    for i in range(105):
        big[f"b{i:03d}"] = now - i
    out_cap = asyncio.run(plugin.api_get_recent_newcomers(since_seconds=0, limit=1000))
    assert len(out_cap["groups"]["777"]["new_members"]) == 100
    assert out_cap["groups"]["777"]["new_members"][0]["user_id"] == "b000"

    # 非法 limit=0 → 钳到 1；非法类型 → 默认 20
    out_min = asyncio.run(plugin.api_get_recent_newcomers(limit=0))
    assert len(out_min["groups"]["123456"]["new_members"]) == 1
    out_bad = asyncio.run(plugin.api_get_recent_newcomers(limit="abc", since_seconds="x"))
    # 非法参数双双回退默认（window=86400 覆盖 u0..u4 全部 5 条，limit 默认 20）
    assert len(out_bad["groups"]["123456"]["new_members"]) == 5


def test_multiple_groups_and_welcome_ts(plugin):
    """多群分组；last_welcome_at 取 _last_trigger，没欢迎过的群为 0.0。"""
    _loaded(plugin)
    now = time.time()
    plugin._seen["111"] = {"a": now - 5}
    plugin._seen["222"] = {"b": now - 6}
    plugin._last_trigger["111"] = now - 100
    out = asyncio.run(plugin.api_get_recent_newcomers(since_seconds=0))
    assert set(out["groups"]) == {"111", "222"}
    assert out["groups"]["111"]["last_welcome_at"] == pytest.approx(now - 100)
    assert out["groups"]["222"]["last_welcome_at"] == 0.0
    assert out["reason"] == ""


def test_malformed_entries_skipped_not_fatal(plugin):
    """畸形时间戳（None/str）被跳过，不炸 API。"""
    _loaded(plugin)
    plugin._seen["999"] = {"ok": time.time() - 1, "bad1": None, "bad2": "昨天"}
    out = asyncio.run(plugin.api_get_recent_newcomers(since_seconds=0))
    members = out["groups"]["999"]["new_members"]
    assert [m["user_id"] for m in members] == ["ok"]


def test_readonly_seen_and_file_untouched(plugin):
    """只读性：调用前后 _seen 深度相等、状态文件 mtime 不变、host 零 capability 调用。"""
    _loaded(plugin)
    plugin._seen["123456"] = {"10001": time.time() - 10, "10002": time.time() - 3600}
    plugin._save_state(force=True)
    path = plugin._state_path()
    assert path is not None and path.is_file()
    mtime_before = path.stat().st_mtime
    seen_before = copy.deepcopy(plugin._seen)
    calls_before = len(plugin._test_host.calls) if hasattr(plugin._test_host, "calls") else None

    for _ in range(3):
        out = asyncio.run(plugin.api_get_recent_newcomers())
        assert out["groups"]["123456"]["new_members"]

    assert plugin._seen == seen_before, "API 不得修改 _seen"
    assert path.stat().st_mtime == mtime_before, "API 不得写盘"
    if calls_before is not None:
        assert len(plugin._test_host.calls) == calls_before, "API 不得发起 capability 调用"
