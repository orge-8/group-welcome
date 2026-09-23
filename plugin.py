"""群聊新人欢迎：捕获入群提示或首次发言，唤醒 Planner 由 LLM 自主决定如何欢迎新人。

设计要点：
  - **信号源双路**（SDK 没有任何成员事件 hook，只能这样取）：
      A. 入群提示优先：入站消息文本命中「入群提示」正则（若适配器把 notice 转成了消息）
      B. 首次发言兜底：该 user_id 首次出现在本群（落盘集合判定）
  - **不自己发欢迎语**：只调 `maisaka.proactive.trigger` 把意图交给 Planner，
    由 LLM 结合人格/上下文自行组织措辞。插件不伪装用户消息、不硬编码模板。
  - **去重与防刷屏**：同一人只欢迎一次（落盘）+ 同群冷却 + 启动预热期建基线。

结构自检: python check_plugin.py --plugin .
冒烟测试: python tests/smoke_test.py
交付门禁: python run_gates.py --plugin .
"""

import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

from maibot_sdk import Command, Field, HookHandler, MaiBotPlugin, PluginConfigBase
from maibot_sdk.types import CONFIG_RELOAD_SCOPE_SELF, ErrorPolicy, HookMode, HookOrder

SUPPORTED_CONFIG_VERSION = "1.3.0"

STATE_VERSION = 1
STATE_FILENAME = "seen_members.json"

BOT_ID_HINTS = ("is_bot", "isBot", "bot")

# 真机实测（2026-09-19）：napcat-adapter 把 QQ 的 notice 事件转成了普通消息，
# message_id 形如 "napcat-notice-9532db6b5c3e40db846807a4e80601d0"，
# 且载荷带 is_notify 标志位。这两者比文本匹配可靠得多，优先用它们判定「系统通知」。
NOTICE_ID_PREFIXES = ("napcat-notice-", "notice-", "system-notice-")

# 诊断用：最近收到的系统通知条数上限（内存缓存，供 `/欢迎 diag` 查看）
MAX_RECENT_NOTICES = 20

# 命令动作的中文别名 → 内部动作名
# ---------------------------------------------------------------- Planner 注入（v1.3.0）
#
# 为什么要在 intent 之外再注入一次：intent 属于「任务描述」，模型未必当成硬约束。
# 这里改在更靠近决策的位置 —— 往 Planner 请求的 items 追加一条系统级发言规则，
# 且只在该会话的「欢迎窗口」内生效，不影响其他场合的正常引用行为。
# 注入实现参考 character-recognizer 的 inject.py（同作者维护，MIT）。
WELCOME_NO_QUOTE_MARKER = "[welcome-no-quote]"
WELCOME_NO_QUOTE_HINT = (
    f"{WELCOME_NO_QUOTE_MARKER} 本次发言规则：你正在**欢迎一位新成员**。"
    "请调用 reply 生成欢迎语，并**必须把 set_quote 设为 false**（不引用任何消息），"
    "让这条发言独立出现 —— 引用无关消息会让群友误以为你在回复他。"
)


def _build_system_item(text: str) -> dict[str, Any]:
    """构造一条可注入 Planner 请求的 System 消息项（对齐 Host 的 items 协议）。"""
    return {
        "item_type": "SystemMessageItem",
        "meta": {
            "item_id": uuid4().hex,
            "logical_turn_id": None,
            "timestamp": datetime.now().isoformat(),
        },
        "parts": [{"type": "text", "text": text}],
    }


def _contains_marker(container: Any, marker: str) -> bool:
    """递归判断注入标记是否已存在（幂等检查，避免同一请求被重复注入）。"""
    if not marker:
        return False
    if isinstance(container, str):
        return marker in container
    if isinstance(container, (list, tuple)):
        return any(_contains_marker(item, marker) for item in container)
    if isinstance(container, dict):
        if marker in str(container.get("text") or ""):
            return True
        if marker in str(container.get("content") or ""):
            return True
        return _contains_marker(container.get("parts"), marker)
    return False


def _inject_into_items(kwargs: dict[str, Any], text: str, marker: str) -> bool:
    """往 Planner 请求的 `items` 追加一条系统提示；返回是否注入成功。

    `items` 是当前 Host 版本真正生效的注入路径（`messages` / `prompt` 属旧版本兜底，
    本插件只走 items，不做多形态猜测）。
    """
    items = kwargs.get("items")
    if not isinstance(items, list):
        return False
    if _contains_marker(items, marker):
        return True  # 幂等：已在里面
    new_items = list(items)
    new_items.append(_build_system_item(text))
    kwargs["items"] = new_items
    return True


ACTION_ALIASES = {
    "状态": "status",
    "开": "on",
    "关": "off",
    "重置": "reset",
    "测试": "test",
    "诊断": "diag",
}

DEFAULT_NOTICE_PATTERNS = [
    # 顺序即优先级：带「通过 / 被邀请」修饰的模式必须排在泛化的「加入了群聊」之前。
    # 否则 \S+? 会把修饰语一起吃进昵称——实测 "孙七被管理员邀请加入了本群" 会得到
    # "孙七被管理员邀请" 而非 "孙七"。
    r"(?P<nick>\S+?)\s*(?:通过|经由).{0,16}?加入",
    r"(?P<nick>\S+?)\s*被.{0,16}?邀请.{0,8}?加入",
    r"欢迎新(?:成员|同学|人|朋友)\s*[:：]?\s*(?P<nick>\S+)",
    r"(?P<nick>\S+?)\s*(?:加入了?群聊|加入了?本群|加入群聊|进群了)",
]

MAX_NICK_CHARS = 32
SAVE_THROTTLE_SECONDS = 5.0


class PluginSectionConfig(PluginConfigBase):
    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(
        default=SUPPORTED_CONFIG_VERSION,
        description="配置版本（与插件版本同步）",
        json_schema_extra={"hidden": True, "disabled": True},
    )


class WelcomeSectionConfig(PluginConfigBase):
    __ui_label__ = "欢迎"
    __ui_icon__ = "user-plus"
    __ui_order__ = 1

    enabled: bool = Field(default=True, description="是否启用新人欢迎（总开关在插件页）")
    admin_ids: list[str] = Field(
        default_factory=list,
        description="管理命令发出者白名单，兼容 '123456789' 与 'qq:123456789'；留空则不做鉴权",
    )
    group_whitelist: list[str] = Field(
        default_factory=list,
        description="生效群号白名单；留空表示对所有群生效",
    )
    ignore_user_ids: list[str] = Field(
        default_factory=list,
        description="不参与判定的 QQ 号（如实填 bot 自己的号），留空则不排除",
    )
    notice_patterns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_NOTICE_PATTERNS),
        description="入群提示文本的匹配正则（需含命名组 nick）；留空则关闭该信号源",
    )
    first_message_fallback: bool = Field(
        default=True,
        description="启用「首次发言」兜底信号（入群提示拿不到时，靠首次开口判定新人）",
    )
    warmup_seconds: int = Field(
        default=300,
        description="启动预热期（秒）：此期间只登记成员、不欢迎，避免首次部署时把老成员全当新人",
    )
    cooldown_seconds: int = Field(
        default=10,
        description="同群两次唤醒的最小间隔（秒），防止短时间内连续触发",
    )
    max_triggers_per_hour: int = Field(
        default=20,
        description="每群每小时最多唤醒次数，0 表示不限制",
    )
    intent_hint: str = Field(
        default="请以你的人格自然地向对方打个招呼，可结合群氛围简要点出这是位新成员；不要提及本条提示的来源。",
        description="追加到唤醒意图后的措辞引导",
    )
    log_unmatched_notices: bool = Field(
        default=True,
        description="把收到的每条系统通知（非入群提示）以 info 级记入日志，并可用 /欢迎 diag 查看",
    )
    skip_known_member_notice: bool = Field(
        default=True,
        description="入群通知里的 user_id 若已是档案中的老成员，判定为「操作者视角的误标事件」并跳过欢迎",
    )
    resolve_nickname: bool = Field(
        default=True,
        description="通过 napcat 接口查询新成员昵称（入群载荷只含 QQ 号）；查不到时退回仅显示 QQ 号",
    )
    no_quote_hint_seconds: float = Field(
        default=90.0,
        description=(
            "欢迎触发后多少秒内，向 Planner 注入「本次发言不要引用」的规则（0 = 关闭）。"
            "仅影响欢迎当轮，其他场合的引用行为不受影响。"
        ),
    )


class GroupWelcomeConfig(PluginConfigBase):
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    welcome: WelcomeSectionConfig = Field(default_factory=WelcomeSectionConfig)


class GroupWelcome(MaiBotPlugin):
    """群聊新人欢迎插件。

    辅助方法一律排在组件装饰器之前：装饰器绑定的是紧随其后的 def，
    中间插入辅助方法会让 Runner 静默注册错人（真机报 unexpected keyword argument）。
    """

    config_model: ClassVar[type[PluginConfigBase] | None] = GroupWelcomeConfig

    def __init__(self) -> None:
        super().__init__()
        self._seen: dict[str, dict[str, float]] = {}
        self._last_trigger: dict[str, float] = {}
        self._hour_window: dict[str, tuple[float, int]] = {}
        self._loaded_at: float = 0.0
        self._last_saved_at: float = 0.0
        self._regex_cache: list[tuple[str, re.Pattern[str]]] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._recent_notices: list[dict[str, Any]] = []
        # session_id → 「欢迎窗口」截止时间戳（窗口内才向 Planner 注入不引用规则）
        self._welcome_windows: dict[str, float] = {}

    # ------------------------------------------------------------------ 状态落盘

    def _state_path(self) -> Path | None:
        """状态文件路径；路径能力不可用时返回 None（降级为纯内存）。"""
        try:
            data_dir = Path(self.ctx.paths.data_dir)
        except Exception:  # 路径能力在某些宿主版本可能不可用
            return None
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        return data_dir / STATE_FILENAME

    def _load_state(self) -> None:
        """读取落盘状态。坏即空——任何异常都从干净状态开始，不阻断插件加载。"""
        path = self._state_path()
        if path is None or not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.ctx.logger.warning("状态文件损坏，已忽略：%s", path)
            return
        if not isinstance(raw, dict):
            return
        groups = raw.get("groups")
        if isinstance(groups, dict):
            for group_id, entry in groups.items():
                if not isinstance(entry, dict):
                    continue
                members = entry.get("members")
                if isinstance(members, dict):
                    clean = {str(k): float(v) for k, v in members.items() if isinstance(v, (int, float))}
                    if clean:
                        self._seen[str(group_id)] = clean
                last = entry.get("last_trigger")
                if isinstance(last, (int, float)):
                    self._last_trigger[str(group_id)] = float(last)

    def _save_state(self, *, force: bool = False) -> None:
        """原子保存（节流）。落盘键用群号/QQ 号字符串，不用内置 hash()。"""
        now = time.time()
        if not force and (now - self._last_saved_at) < SAVE_THROTTLE_SECONDS:
            return
        path = self._state_path()
        if path is None:
            return
        payload = {
            "version": STATE_VERSION,
            "groups": {
                group_id: {
                    "members": members,
                    "last_trigger": self._last_trigger.get(group_id, 0.0),
                }
                for group_id, members in self._seen.items()
            },
        }
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
            self._last_saved_at = now
        except OSError as exc:
            self.ctx.logger.warning("状态保存失败：%s", exc)

    # ------------------------------------------------------------------ 配置判定

    def _welcome_enabled(self) -> bool:
        return bool(self.config.plugin.enabled and self.config.welcome.enabled)

    def _is_admin(self, kwargs: dict[str, Any]) -> bool:
        """插件自管鉴权：未配置 admin_ids 时全部允许（fail-open）。"""
        if bool(kwargs.get("is_local_operator")):
            return True
        admins = {
            str(item).split(":")[-1].strip().lower()
            for item in (self.config.welcome.admin_ids or [])
            if str(item).strip()
        }
        if not admins:
            return True
        user_id = self._caller_id(kwargs).split(":")[-1].strip().lower()
        return bool(user_id) and user_id in admins

    def _caller_id(self, kwargs: dict[str, Any]) -> str:
        """顶层 user_id 优先，兜底 message 深层路径。"""
        user_id = str(kwargs.get("user_id") or "").strip()
        if user_id:
            return user_id
        message = kwargs.get("message")
        if not isinstance(message, dict):
            return ""
        info = message.get("message_info")
        if isinstance(info, dict):
            user = info.get("user_info")
            if isinstance(user, dict):
                for key in ("user_id", "sender_id", "uid"):
                    value = user.get(key)
                    if value not in (None, ""):
                        return str(value).strip()
        value = message.get("user_id")
        return str(value).strip() if value not in (None, "") else ""

    def _group_allowed(self, group_id: str) -> bool:
        allow = [str(g).strip() for g in (self.config.welcome.group_whitelist or []) if str(g).strip()]
        return not allow or group_id in allow

    def _looks_like_user_id(self, value: str) -> bool:
        """判断是否为一个像样的 QQ 号（纯数字、长度合理）。

        真机实测（2026-09-19 17:24:41）：入群通知载荷的 `user_info.user_id`
        是**入群者本人**（`载荷user_id='3816023959'`，即狸猫），不是系统号。
        因此路径 A 可以带上真实 QQ 号；但仍加纯数字校验，避免异常载荷把
        "系统消息"之类的标识当成 QQ 号写进档案。
        """
        text = str(value or "").strip()
        return text.isdigit() and 5 <= len(text) <= 12

    def _ignored(self, user_id: str) -> bool:
        ignored = {str(u).split(":")[-1].strip().lower() for u in (self.config.welcome.ignore_user_ids or [])}
        return bool(user_id) and user_id.split(":")[-1].strip().lower() in ignored

    # ------------------------------------------------------------------ 载荷解析

    def _extract_text(self, message: dict[str, Any]) -> str:
        """取消息纯文本。

        真机实测（2026-09-19）：`raw_message` 是分段 list 而非字符串
        （file-reader 诊断打印为 `raw=list[text(str='狸猫 修改了群名称')]`），
        所以 `processed_plain_text` 必须优先，list 形式也要能兜住。
        """
        for key in ("processed_plain_text", "plain_text", "content", "raw_message"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list):
                parts: list[str] = []
                for seg in value:
                    if isinstance(seg, str) and seg.strip():
                        parts.append(seg.strip())
                        continue
                    if not isinstance(seg, dict):
                        continue
                    inner = seg.get("data") if isinstance(seg.get("data"), dict) else seg
                    for tkey in ("text", "content", "str"):
                        piece = inner.get(tkey)
                        if isinstance(piece, str) and piece.strip():
                            parts.append(piece.strip())
                            break
                if parts:
                    return " ".join(parts)
        return ""

    def _extract(self, message: Any) -> dict[str, str] | None:
        """从入站载荷提取群号 / 发送者 / 昵称 / 文本 / 会话 ID。群聊以外返回 None。"""
        if not isinstance(message, dict):
            return None

        group_id = ""
        info = message.get("message_info")
        if isinstance(info, dict):
            group_info = info.get("group_info")
            if isinstance(group_info, dict):
                for key in ("group_id", "group", "id"):
                    value = group_info.get(key)
                    if value not in (None, ""):
                        group_id = str(value).strip()
                        break
        if not group_id:
            value = message.get("group_id")
            group_id = str(value).strip() if value not in (None, "") else ""
        if not group_id:
            return None  # 私聊没有群上下文，直接跳过

        user_id = ""
        nickname = ""
        if isinstance(info, dict):
            user = info.get("user_info")
            if isinstance(user, dict):
                for key in ("user_id", "sender_id", "uid"):
                    value = user.get(key)
                    if value not in (None, ""):
                        user_id = str(value).strip()
                        break
                for key in ("user_cardname", "user_card", "cardname", "card", "group_card",
                            "user_nickname", "nickname", "user_name", "name"):
                    value = user.get(key)
                    if value not in (None, ""):
                        nickname = str(value).strip()
                        break
        if not user_id:
            value = message.get("user_id")
            user_id = str(value).strip() if value not in (None, "") else ""

        text = self._extract_text(message)

        stream_id = ""
        for key in ("session_id", "stream_id"):
            value = message.get(key)
            if value not in (None, ""):
                stream_id = str(value).strip()
                break

        return {
            "group_id": group_id,
            "user_id": user_id,
            "nickname": nickname or user_id,
            "text": text,
            "stream_id": stream_id,
        }

    def _is_bot_message(self, message: Any) -> bool:
        if not isinstance(message, dict):
            return False
        info = message.get("message_info")
        user = info.get("user_info") if isinstance(info, dict) else None
        if isinstance(user, dict):
            for key in BOT_ID_HINTS:
                if bool(user.get(key)):
                    return True
        return False

    def _is_command_message(self, message: Any) -> bool:
        """命令消息不参与新人判定。

        真机 2026-09-19 实测：管理员在群里发 `/欢迎 diag`，因该 QQ 尚未进入 seen 档案，
        被「首次发言」路径误判为新人候选——当时恰在预热期才只登记未欢迎，
        否则发一条管理命令就会触发一次欢迎。
        """
        if not isinstance(message, dict):
            return False
        if bool(message.get("is_command")):
            return True
        text = self._extract_text(message).lstrip()
        if not text:
            return False
        # 兜底：`/命令` 或 `／命令`（全角斜杠），且命令词不含空白
        for prefix in ("/", "／"):
            if text.startswith(prefix):
                rest = text[len(prefix):].strip()
                return bool(rest) and not rest[0].isspace()
        return False

    # ------------------------------------------------------------------ 入群提示识别

    def _regexes(self) -> list[tuple[str, re.Pattern[str]]]:
        raw = [str(p) for p in (self.config.welcome.notice_patterns or []) if str(p).strip()]
        signature = "|".join(raw)
        if self._regex_cache and getattr(self, "_regex_sig", "") == signature:
            return self._regex_cache
        compiled: list[tuple[str, re.Pattern[str]]] = []
        for pattern in raw:
            try:
                compiled.append((pattern, re.compile(pattern)))
            except re.error as exc:
                self.ctx.logger.warning("入群提示正则非法，已跳过：%s (%s)", pattern, exc)
        self._regex_cache = compiled
        self._regex_sig = signature
        return compiled

    def _match_notice(self, text: str) -> str:
        """文本命中入群提示则返回新人昵称，否则返回空串。"""
        if not text:
            return ""
        for _, regex in self._regexes():
            match = regex.search(text)
            if not match:
                continue
            nick = ""
            if "nick" in regex.groupindex:
                nick = str(match.group("nick") or "").strip()
            if not nick:
                continue
            return nick[:MAX_NICK_CHARS]
        return ""

    def _looks_like_notice(self, text: str) -> bool:
        """粗判文本是否为系统提示（用于首次发言兜底时排除系统消息）。"""
        if not text:
            return False
        if self._match_notice(text):
            return True
        markers = ("加入了群聊", "加入本群", "加入了本群", "撤回了一条消息", "邀请", "群公告")
        return any(marker in text for marker in markers)

    def _is_notice_message(self, message: Any, text: str) -> bool:
        """判定是否为系统通知消息。

        优先用适配器给的硬信号——真机实测 notice 被转成消息时带 `is_notify=True`
        且 `message_id` 形如 `napcat-notice-...`；两者都拿不到时才退回文本特征匹配。
        """
        if isinstance(message, dict):
            if bool(message.get("is_notify")):
                return True
            mid = str(message.get("message_id") or "").strip().lower()
            if mid and mid.startswith(NOTICE_ID_PREFIXES):
                return True
        return self._looks_like_notice(text)

    def _extract_raw_notice(self, message: Any) -> dict[str, Any] | None:
        """从 `message_info.additional_config` 提取 napcat 的**原始 notice 载荷**。

        这是获取「真实入群者 QQ」的唯一权威途径。
        依据（2026-09-19，参照 ji-or-ji/group-awareness-plugin 的实现，MIT）：
        napcat-adapter 会把原始 OneBot notice 放进 `additional_config`：

            additional_config = {
                "napcat_notice_type": "group_increase",
                "napcat_notice_sub_type": "increase",
                "napcat_notice_payload": {
                    "group_id": "...", "user_id": "...",     # ← 真正入群的人
                    "operator_id": "...", "self_id": "...",
                },
            }

        为什么必须用它：消息文本与 `message_info.user_info` 在某些通知里会被填成
        **操作者**（群主同意申请/发出邀请时就是群主本人），导致「群主被当成新人」。
        只有 payload 里的 `user_id` 才是真正入群/退群的人。
        """
        if not isinstance(message, dict):
            return None
        msg_info = message.get("message_info")
        if not isinstance(msg_info, dict):
            return None
        additional = msg_info.get("additional_config")
        if not isinstance(additional, dict):
            return None

        notice_type = str(additional.get("napcat_notice_type") or "").strip().lower()
        if not notice_type:
            return None
        payload = additional.get("napcat_notice_payload")
        if not isinstance(payload, dict):
            payload = {}

        def _pick(*keys: str) -> str:
            for key in keys:
                value = payload.get(key)
                if value not in (None, ""):
                    return str(value).strip()
            return ""

        return {
            "notice_type": notice_type,
            "sub_type": str(additional.get("napcat_notice_sub_type") or "").strip().lower(),
            "user_id": _pick("user_id", "target_id"),
            "operator_id": _pick("operator_id", "operator"),
            "self_id": _pick("self_id"),
            "group_id": _pick("group_id"),
        }

    def _remember_notice(
        self,
        group_id: str,
        message_id: str,
        text: str,
        *,
        matched: bool = False,
        skipped: bool = False,
    ) -> None:
        """把收到的系统通知记入内存环形缓存，供 `/欢迎 diag` 查看。

        真机事故驱动（2026-09-19）：同日同群「狸猫」入群被识别、「tori」入群无反应，
        当时未命中的通知只记 debug 级 → 无从判断卡在哪。
        另一处修正：**命中的通知也要入缓存**——最初只记未命中的，导致 `/欢迎 diag`
        在"刚有人入群"时反而显示"暂无记录"，诊断价值被削弱。
        """
        self._recent_notices.append(
            {
                "at": time.strftime("%H:%M:%S", time.localtime()),
                "group_id": group_id,
                "message_id": message_id,
                "text": text[:120],
                "matched": bool(matched),
                "skipped": bool(skipped),
            }
        )
        if len(self._recent_notices) > MAX_RECENT_NOTICES:
            del self._recent_notices[:-MAX_RECENT_NOTICES]
        if matched:
            return  # 命中路径已单独打日志，不重复输出
        if self.config.welcome.log_unmatched_notices:
            self.ctx.logger.info(
                "收到系统通知（未识别为入群提示）：群=%s mid=%s 文本=%r",
                group_id,
                message_id,
                text[:80],
            )

    # ------------------------------------------------------------------ 判定与触发

    def _is_new_member(self, group_id: str, user_id: str) -> bool:
        if not user_id:
            return False
        return user_id not in self._seen.get(group_id, {})

    def _mark_seen(self, group_id: str, user_id: str) -> None:
        if not user_id:
            return
        self._seen.setdefault(group_id, {})[user_id] = time.time()
        self._save_state()

    def _warmup_active(self) -> bool:
        warmup = max(0, int(self.config.welcome.warmup_seconds or 0))
        if warmup <= 0 or self._loaded_at <= 0:
            return False
        return (time.time() - self._loaded_at) < warmup

    def _cooldown_ok(self, group_id: str) -> bool:
        cooldown = max(0, int(self.config.welcome.cooldown_seconds or 0))
        last = self._last_trigger.get(group_id, 0.0)
        return cooldown <= 0 or (time.time() - last) >= cooldown

    def _quota_ok(self, group_id: str) -> bool:
        limit = int(self.config.welcome.max_triggers_per_hour or 0)
        if limit <= 0:
            return True
        window_start, count = self._hour_window.get(group_id, (0.0, 0))
        now = time.time()
        if (now - window_start) >= 3600:
            self._hour_window[group_id] = (now, 0)
            return True
        return count < limit

    def _bump_quota(self, group_id: str) -> None:
        window_start, count = self._hour_window.get(group_id, (time.time(), 0))
        self._hour_window[group_id] = (window_start, count + 1)

    def _build_intent(
        self, *, nickname: str, user_id: str, source: str, notice_mid: str = ""
    ) -> str:
        # 组装称呼：有昵称就写"昵称（QQ xxx）"；只有 QQ 就只写一次，
        # 避免出现 "2336884608（QQ 2336884608）" 这种重复表述。
        if nickname and user_id and nickname != user_id:
            who = f"{nickname}（QQ {user_id}）"
        elif user_id:
            who = f"QQ {user_id}"
        else:
            who = nickname or "未知成员"
        if source == "notice_payload":
            reason = "收到入群通知"
        elif source == "notice":
            reason = "检测到入群提示"
        else:
            reason = "该成员首次在本群发言"
        hint = str(self.config.welcome.intent_hint or "").strip()
        # 措辞要点（真机 2026-09-19 反馈，v1.0.2）：
        # 必须显式说明「这是事件通知、不是待回复的消息」。否则 Planner 会挑一条历史消息
        # 去调 reply（实测挑了用户 4 小时前问歌的旧消息），使欢迎变成"引用回复"，
        # 并触发 replyer 的重复回复驳回路径，<reject> 文本一度被当作消息发出。
        # 措辞演进（真机 2026-09-19 19:14 反馈，v1.1.2）：
        # v1.0.2 曾写「不要引用或回复任何历史消息」，但 Planner 唯一可用的发言工具就是
        # `reply`（且必须带 msg_id），该指令与工具箱直接冲突 —— 实测它先后拿
        # proactive 任务编号（1789816438111）和时间串（19:13:57）当 msg_id，
        # 连续两次失败（"未找到要回复的目标消息"）才找到真实消息 id。
        # 改为「不专门回复久远消息；若必须回复，选最近一条即可」：
        # 既避免引用 4 小时前的旧消息，又给出可执行路径。
        parts = [
            f"【群事件通知】新成员入群：{who}（依据：{reason}）。",
            # 真机 2026-09-19 19:39 反馈：群内聊天记录里那条「XX 加入了群聊」的通知文本
            # 在部分通知里会被标成**邀请人/操作者**，不是真正入群的人。
            # 实测 Planner 同时看到"intent 说新人是 A"与"历史消息说 B 加入了群聊"时，
            # 它信了历史消息那条（reply_reference 写成"现在狸猫正式加入群聊"）。
            # 因此必须显式提示两个信息源冲突时以谁为准。
            "注意：群内聊天记录里那条「…加入了群聊」的通知可能把入群者标成了邀请人，"
            "那一条不可信；请一律以本通知给出的 QQ 号为准。",
            "这是一条群事件通知，不是等你回复的聊天消息。",
        ]
        # 引用策略（v1.2.3 定稿，四轮迭代后的结论）：
        #   v1.0.2「不要引用或回复任何历史消息」→ 与 reply 工具（必须带 msg_id）冲突，
        #          Planner 拿任务编号/时间串乱试，连败两次；
        #   v1.1.2「选群内最近的一条即可」→ 引用了群友的表情包，对方回「?」；
        #   v1.2.1「回复本条通知（编号 xxx）」→ 通知 id 是适配器自造的
        #          `napcat-notice-<hex>`，Host 写上下文时对通知关闭了 id 展示
        #          （runtime.py:517），可回复性未获确证；
        #   v1.2.2「把 set_quote 设为 false」→ 但 set_quote 是 Planner 调工具的参数
        #          （reply.py:305 默认 True），插件无法强制，Hook 也够不着
        #          （before_model_request 在请求前，改不了模型输出）。
        # 定稿：**引导 + 兜底双保险** —— 主推关闭引用显示；万一模型没照做，
        # 也让它把引用挂在自己身上，而不是挂到群友头上。
        parts.append(
            "请调用 reply 生成一条新的群聊发言来欢迎，并把 set_quote 设为 false，"
            "让欢迎语独立显示 —— 不要引用任何消息，引用到无关消息会让人误以为在回复他。"
            "若确实需要指定回复对象，请选择你自己最近发出的一条消息，切勿引用他人的消息。"
        )
        # notice_mid 保留在签名里（三条路径都会传），但**不再写进 intent**：
        # 通知 id 的可回复性未获确证（见上方 v1.2.1 说明），
        # 写进去只会诱导模型去试一个可能查不到的 id，多绕一轮。
        del notice_mid
        if hint:
            parts.append(hint)
        return "".join(parts)

    async def _resolve_nickname(self, group_id: str, user_id: str) -> str:
        """查询成员昵称：群名片优先，回退 QQ 昵称。

        权威 notice 载荷只给 QQ 号不给昵称（见 `_extract_raw_notice`），
        所以昵称必须主动查。接口与字段名取自已验证实现
        `ji-or-ji/group-awareness-plugin`（MIT，真机跑通）：

        - `adapter.napcat.group.get_group_member_info` → `card` / `nickname`
        - `adapter.napcat.account.get_stranger_info`   → `nick` / `nickname`
          （注意是 `nick`，不是 `nickname`）

        入群场景成员已在群内，主路径通常可用。查询失败一律回退空串，
        调用方会退回"仅 QQ 号"的表述，不影响欢迎流程。
        """
        if not user_id.isdigit() or not group_id.isdigit():
            return ""
        attempts: list[tuple[str, tuple[str, ...]]] = [
            ("adapter.napcat.group.get_group_member_info", ("card", "nickname")),
            ("adapter.napcat.account.get_stranger_info", ("nick", "nickname")),
        ]
        for api_name, fields in attempts:
            payload: dict[str, Any] = {"user_id": int(user_id), "no_cache": True}
            if api_name.startswith("adapter.napcat.group."):
                payload["group_id"] = int(group_id)
            try:
                result = await self.ctx.api.call(api_name, **payload)
            except Exception as exc:  # noqa: BLE001 - 查询失败必须降级而非中断
                self.ctx.logger.debug("昵称查询失败（%s）：%s", api_name, exc)
                continue
            if not isinstance(result, dict):
                continue
            for field in fields:
                value = str(result.get(field) or "").strip()
                if value:
                    return value[:MAX_NICK_CHARS]
        return ""

    async def _resolve_stream(self, group_id: str, stream_id: str) -> str:
        """优先用载荷里的会话 ID；缺失时用群号反查已有聊天流。"""
        if stream_id:
            return stream_id
        try:
            result = await self.ctx.chat.get_stream_by_group_id(group_id, platform="qq")
        except Exception as exc:  # noqa: BLE001 - 宿主能力异常不应冒泡
            self.ctx.logger.debug("聊天流反查失败：%s", exc)
            return ""
        if isinstance(result, dict):
            for key in ("stream_id", "session_id", "id"):
                value = result.get(key)
                if value not in (None, ""):
                    return str(value).strip()
            stream = result.get("stream")
            if isinstance(stream, dict):
                value = stream.get("stream_id") or stream.get("session_id")
                if value not in (None, ""):
                    return str(value).strip()
        if isinstance(result, str):
            return result.strip()
        return ""

    @HookHandler(
        "maisaka.planner.before_request",
        name="inject_no_quote_hint",
        description="欢迎新成员的当轮，向 Planner 注入「不要引用」的发言规则",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
        timeout_ms=3000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def inject_no_quote_hint(self, **kwargs: Any) -> "dict[str, Any] | None":
        """欢迎窗口内向 Planner 注入「不要引用」规则（v1.3.0）。

        真机 2026-09-23：v1.2.3 把引用策略写进 intent，但 intent 属「任务描述」，
        模型未必当成硬约束。这里改在更靠近决策的位置 —— 往 Planner 请求的 items
        追加一条系统级发言规则，且**只在该会话的欢迎窗口内**生效，
        其他场合的正常引用行为完全不受影响。

        ⚠️ 仍是**引导而非强制**：模型输出（工具参数）无法被插件干预，
        详见 README「引用显示无法由插件强制」。本 hook 的作用是提高"选对"的概率。
        """
        ttl = float(self.config.welcome.no_quote_hint_seconds or 0)
        if ttl <= 0:
            return None
        session_id = str(kwargs.get("session_id") or "").strip()
        if not session_id:
            return None
        deadline = self._welcome_windows.get(session_id)
        if not deadline or time.time() > deadline:
            return None

        payload = dict(kwargs)
        if not _inject_into_items(payload, WELCOME_NO_QUOTE_HINT, WELCOME_NO_QUOTE_MARKER):
            self.ctx.logger.warning(
                "欢迎发言规则注入失败：items 形态不匹配（session=%s）", session_id
            )
            return None
        self.ctx.logger.info("已注入欢迎发言规则（不引用）：session=%s", session_id)
        return {"action": "continue", "modified_kwargs": payload}

    async def _wake_planner(self, stream_id: str, intent: str, *, reason: str) -> bool:
        """唤醒 Planner 自行决定如何欢迎。失败只记日志，不影响聊天主流程。"""
        if not stream_id:
            return False
        try:
            await self.ctx.maisaka.proactive.trigger(stream_id, intent, reason=reason)
            # 打开「欢迎窗口」：窗口内的 Planner 请求会被注入「不要引用」规则（v1.3.0）
            ttl = float(self.config.welcome.no_quote_hint_seconds or 0)
            if ttl > 0:
                now = time.time()
                # 顺手清掉过期窗口，避免字典随会话数无限增长
                if len(self._welcome_windows) > 8:
                    self._welcome_windows = {
                        key: deadline
                        for key, deadline in self._welcome_windows.items()
                        if deadline > now
                    }
                self._welcome_windows[stream_id] = now + ttl
                self.ctx.logger.debug("已开启欢迎窗口 %.0fs：session=%s", ttl, stream_id)
            return True
        except Exception as exc:  # noqa: BLE001 - 宿主能力异常不应冒泡到消息链
            self.ctx.logger.warning("唤醒 Planner 失败：%s", exc)
            return False

    async def _handle_candidate(
        self,
        *,
        group_id: str,
        user_id: str,
        nickname: str,
        stream_id: str,
        source: str,
        notice_mid: str = "",
    ) -> bool:
        """冷却/配额检查 + 唤醒 + 记入档案。返回是否真的欢迎了。"""
        stream_id = await self._resolve_stream(group_id, stream_id)

        # 权威载荷只给 QQ 号，占位时补查真实昵称（在后台任务里，不阻塞消息链）
        if self.config.welcome.resolve_nickname and user_id and nickname == user_id:
            resolved = await self._resolve_nickname(group_id, user_id)
            if resolved:
                self.ctx.logger.info("昵称解析成功：%s → %s", user_id, resolved)
                nickname = resolved
            else:
                self.ctx.logger.info("昵称解析未命中，改用 QQ 号表述：%s", user_id)

        if not stream_id:
            self.ctx.logger.warning("未能确定聊天流，跳过唤醒：群=%s 成员=%s", group_id, nickname or user_id)
            self._mark_seen(group_id, user_id)
            return False
        if not self._cooldown_ok(group_id):
            self.ctx.logger.info(
                "同群冷却中，本次不唤醒（间隔 %ss）：群=%s 成员=%s",
                self.config.welcome.cooldown_seconds,
                group_id,
                nickname or user_id,
            )
            self._mark_seen(group_id, user_id)
            return False
        if not self._quota_ok(group_id):
            self.ctx.logger.info(
                "已达每小时唤醒上限（%s 次），本次不唤醒：群=%s 成员=%s",
                self.config.welcome.max_triggers_per_hour,
                group_id,
                nickname or user_id,
            )
            self._mark_seen(group_id, user_id)
            return False

        intent = self._build_intent(
            nickname=nickname, user_id=user_id, source=source, notice_mid=notice_mid
        )
        ok = await self._wake_planner(stream_id, intent, reason=f"newcomer:{source}")
        self._last_trigger[group_id] = time.time()
        self._bump_quota(group_id)
        self._mark_seen(group_id, user_id)
        self._save_state(force=True)
        if ok:
            self.ctx.logger.info(
                "已唤醒 Planner：群=%s 成员=%s 依据=%s", group_id, nickname or user_id, source
            )
        return ok

    def _spawn(self, coro: Any) -> None:
        """把可能较慢的唤醒动作放到后台，避免阻塞消息主流程。

        任务异常必须显式记录（全检加固）：后台任务的异常不会冒泡到 hook 调用方，
        若不主动取 `task.exception()`，排查时就只能看到"什么都没发生"。
        """
        try:
            task = asyncio.create_task(coro)
        except RuntimeError:
            self.ctx.logger.warning("事件循环不可用，后台唤醒任务未创建")
            return
        self._tasks.add(task)

        def _on_done(t: asyncio.Task[Any]) -> None:
            self._tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                self.ctx.logger.error("后台唤醒任务异常：%r", exc, exc_info=exc)

        task.add_done_callback(_on_done)

    # ------------------------------------------------------------------ 生命周期

    async def on_load(self) -> None:
        self._loaded_at = time.time()
        self._load_state()
        groups = len(self._seen)
        members = sum(len(v) for v in self._seen.values())
        self.ctx.logger.info(
            "group-welcome 已加载：群 %d 个 / 已登记成员 %d 人，欢迎开关=%s",
            groups,
            members,
            self._welcome_enabled(),
        )

    async def on_unload(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        self._save_state(force=True)
        self.ctx.logger.info("group-welcome 已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        if scope == CONFIG_RELOAD_SCOPE_SELF:
            self._regex_cache = []
            self._regex_sig = ""
            self.ctx.logger.info("group-welcome 配置已热更新 version=%s", version)

    # ------------------------------------------------------------------ 组件

    @HookHandler(
        "chat.receive.before_process",
        name="detect_newcomer",
        description="识别入群提示或首次发言，唤醒 Planner 欢迎新人",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
        error_policy=ErrorPolicy.SKIP,
    )
    async def detect_newcomer(self, message: dict | None = None, **kwargs: Any) -> None:
        del kwargs
        if not self._welcome_enabled() or self._is_bot_message(message):
            return
        if self._is_command_message(message):
            return  # 命令消息不参与新人判定（否则发管理命令会被当成首次发言）

        payload = self._extract(message)
        if payload is None:
            return

        group_id = payload["group_id"]
        if not self._group_allowed(group_id):
            return

        stream_id = payload["stream_id"]
        warming = self._warmup_active()
        text = payload["text"]
        message_id = str((message or {}).get("message_id") or "") if isinstance(message, dict) else ""

        # ---- 路径 0（权威）：napcat 原始 notice 载荷 ----
        # 消息文本与 user_info 在某些通知里会被填成操作者，只有 payload.user_id
        # 才是真正的入群者。拿到它就完全不需要猜名字。
        raw = self._extract_raw_notice(message)
        if raw and raw["notice_type"] == "group_increase":
            if not raw["user_id"]:
                # fail-closed（上线前全检发现）：载荷明确是入群事件，却拿不到事件主体。
                # 此时若降级到文本路径，只会取到「操作者」身份（见 operator_id 陷阱），
                # 结果是欢迎一个根本没入群的人。误判代价不对称——不发 ≈ 0 成本，
                # 发错 = 群里可见的尴尬，故跳过本次而非降级。
                self.ctx.logger.warning(
                    "入群通知载荷缺失 user_id（operator_id=%r），无法确定真实新人，"
                    "已跳过本次欢迎：群=%s 文本=%r",
                    raw["operator_id"],
                    group_id,
                    text[:40],
                )
                self._remember_notice(group_id, message_id, text, matched=True, skipped=True)
                return
            newcomer_id = raw["user_id"]
            if raw["self_id"] and newcomer_id == raw["self_id"]:
                return  # bot 自己入群，不欢迎
            if self._ignored(newcomer_id):
                return
            self.ctx.logger.info(
                "原始入群通知（权威）：群=%s 新人=%s 操作者=%r sub_type=%s",
                group_id,
                newcomer_id,
                raw["operator_id"] or "-",
                raw["sub_type"] or "-",
            )
            if (
                self.config.welcome.skip_known_member_notice
                and not self._is_new_member(group_id, newcomer_id)
            ):
                self.ctx.logger.info(
                    "成员 %s 已在档案中，跳过重复欢迎：群=%s", newcomer_id, group_id
                )
                self._remember_notice(group_id, message_id, text, matched=True, skipped=True)
                return
            self._remember_notice(group_id, message_id, text, matched=True)
            # 载荷不含昵称，先用 QQ 号占位；Planner 可自行查人物资料补充称呼
            self._spawn(
                self._handle_candidate(
                    group_id=group_id,
                    user_id=newcomer_id,
                    nickname=newcomer_id,
                    stream_id=stream_id,
                    source="notice_payload",
                    notice_mid=message_id,
                )
            )
            return

        # 以下路径依赖 user_info 里的 user_id（可能被填成操作者，仅作兜底用）
        user_id = payload["user_id"]
        if not user_id or self._ignored(user_id):
            return

        notice_nick = self._match_notice(text)

        # ---- 路径 A（兜底）：文本匹配入群提示 ----
        # 不受预热期抑制：预热期是为「无法区分老成员/新人」的首次发言路径准备的，
        # 而入群提示本身就是确定性事件，抑制它只会让明确的新人欢迎失败。
        if notice_nick:
            if warming:
                self.ctx.logger.info(
                    "预热期内命中入群提示，按确定性事件放行：群=%s 昵称=%s", group_id, notice_nick
                )
            self.ctx.logger.info(
                "命中入群提示：群=%s 昵称=%s 载荷user_id=%r mid=%s 文本=%r",
                group_id,
                notice_nick,
                user_id,
                message_id,
                text[:40],
            )
            newcomer_id = user_id if self._looks_like_user_id(user_id) else ""

            # 防御（真机 2026-09-19 tori 事故）：入群通知携带的 user_info 实测可能是
            # 【操作者】而非入群者——群主「狸猫」从未退群，却两次收到「狸猫 加入了群聊」，
            # 时间与「tori 被邀请 / 申请入群」完全吻合，说明适配器用 operator_id 填充了发送者。
            # 若该 user_id 早已在本群档案中，说明此人是老成员，这条通知不可信 → 跳过欢迎。
            #
            # 代价：老成员退群重入将不再被欢迎（他已在档案里）。
            # 相比之下"热情欢迎一个根本没入群的人"是群里可见的尴尬错误，故默认开启。
            if (
                newcomer_id
                and self.config.welcome.skip_known_member_notice
                and not self._is_new_member(group_id, newcomer_id)
            ):
                self.ctx.logger.warning(
                    "入群通知的 user_id=%s 已是本群档案中的老成员，"
                    "疑似「操作者视角」的误标事件，已跳过欢迎：群=%s 文本=%r mid=%s",
                    newcomer_id,
                    group_id,
                    text[:40],
                    message_id,
                )
                self._remember_notice(group_id, message_id, text, matched=True, skipped=True)
                return

            self._remember_notice(group_id, message_id, text, matched=True)
            # user_id 可用时（v1.0.5 起）用于 intent 带 QQ 与登记档案。
            # 其可信度取决于适配器是否修好 notice 的发送者映射，上面的防御是兜底；
            # 若适配器已修，这里的 id 就是真正的入群者。
            self._spawn(
                self._handle_candidate(
                    group_id=group_id,
                    user_id=newcomer_id,
                    nickname=notice_nick,
                    stream_id=stream_id,
                    source="notice",
                    notice_mid=message_id,
                )
            )
            return

        # ---- 系统通知但不是入群提示（改名 / 撤回 / 公告 / 入群申请…）：不当作发言 ----
        if self._is_notice_message(message, text):
            self._remember_notice(group_id, message_id, text)
            return

        # ---- 路径 B：首次发言兜底（预热期内只登记）----
        is_new = self._is_new_member(group_id, user_id)
        if warming:
            self._mark_seen(group_id, user_id)
            remain = max(0.0, float(self.config.welcome.warmup_seconds) - (time.time() - self._loaded_at))
            self.ctx.logger.info(
                "预热期内登记成员（不欢迎）：群=%s 用户=%s 剩余=%.0fs", group_id, user_id, remain
            )
            return
        if not is_new:
            return
        if not self.config.welcome.first_message_fallback:
            self.ctx.logger.debug("首次发言兜底已关闭，跳过：群=%s 用户=%s", group_id, user_id)
            return

        self.ctx.logger.info("检测到成员首次发言：群=%s 用户=%s 昵称=%s", group_id, user_id, payload["nickname"])
        self._spawn(
            self._handle_candidate(
                group_id=group_id,
                user_id=user_id,
                nickname=payload["nickname"],
                stream_id=stream_id,
                source="first_message",
                # 首次发言路径的 mid 就是新人那条消息本身，引用它是自然的
                notice_mid=message_id,
            )
        )

    @Command(
        "welcome",
        description="新人欢迎管理：状态 / 开关 / 重置档案 / 手动触发 / 诊断",
        pattern=(
            r"^\s*[/／]\s*(?:欢迎|welcome)"
            r"(?:\s+(?P<action>status|on|off|reset|test|diag|状态|诊断|开|关|重置|测试))?"
            r"(?:\s+(?P<arg>\S+))?\s*$"
        ),
        aliases=["新人欢迎"],
    )
    async def cmd_welcome(
        self,
        matched_groups: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> tuple[bool, str, bool]:
        stream_id = str(kwargs.get("stream_id") or "")
        if not self._is_admin(kwargs):
            return False, "权限不足", False

        groups = matched_groups or {}
        raw_action = str(groups.get("action") or "status").strip().lower()
        action = ACTION_ALIASES.get(raw_action, raw_action)
        arg = str(groups.get("arg") or "").strip()
        target = arg or self._group_of(kwargs)

        if action == "diag":
            await self.ctx.send.text(self._diag_text(), stream_id)
            return True, "diag", False
        if action == "on":
            self.config.welcome.enabled = True
            await self.ctx.send.text("新人欢迎已开启。", stream_id)
            return True, "enabled", False
        if action == "off":
            self.config.welcome.enabled = False
            await self.ctx.send.text("新人欢迎已关闭。", stream_id)
            return True, "disabled", False
        if action == "reset":
            if target:
                removed = len(self._seen.pop(target, {}))
                self._last_trigger.pop(target, None)
                self._save_state(force=True)
                await self.ctx.send.text(f"已重置群 {target} 的成员档案（{removed} 人）。", stream_id)
                return True, "reset", False
            await self.ctx.send.text("未指定群号，且当前会话没有群上下文，无法重置。", stream_id)
            return False, "need-group", False
        if action == "test":
            if not target:
                await self.ctx.send.text("未指定群号，且当前会话没有群上下文，无法测试。", stream_id)
                return False, "need-group", False
            ok = await self._wake_planner(
                stream_id,
                "群里出现了新成员（手动测试）。请自然地打个招呼。",
                reason="manual:test",
            )
            await self.ctx.send.text("测试唤醒已发出。" if ok else "测试唤醒失败，请查看日志。", stream_id)
            return ok, "test", False

        await self.ctx.send.text(self._status_text(target), stream_id)
        return True, "status", False

    def _group_of(self, kwargs: dict[str, Any]) -> str:
        message = kwargs.get("message")
        if not isinstance(message, dict):
            return ""
        info = message.get("message_info")
        if isinstance(info, dict):
            group_info = info.get("group_info")
            if isinstance(group_info, dict):
                value = group_info.get("group_id")
                if value not in (None, ""):
                    return str(value).strip()
        value = message.get("group_id")
        return str(value).strip() if value not in (None, "") else ""

    def _diag_text(self) -> str:
        """诊断信息：最近收到的系统通知原文。

        用于判断"有人入群但插件没反应"卡在哪一步：
        缓存为空 ⇒ 通知压根没进 hook 链（适配器层问题，插件无能为力）；
        缓存有该条但未命中 ⇒ 正则漏匹配，照原文补 notice_patterns。
        """
        lines = [f"【诊断】最近收到的系统通知（最多 {MAX_RECENT_NOTICES} 条）："]
        if not self._recent_notices:
            lines.append("（暂无记录）")
            lines.append("若刚有人入群却看不到记录，说明该通知未进入 chat.receive hook 链——")
            lines.append("请对照 file-reader 插件的 [诊断] file hook 日志确认适配器是否转发。")
        else:
            for item in reversed(self._recent_notices[-10:]):
                if item.get("skipped"):
                    flag = "疑似误标·已跳过"
                elif item.get("matched"):
                    flag = "已识别入群"
                else:
                    flag = "未识别为入群"
                lines.append(f"[{item['at']}] 群={item['group_id']} [{flag}] {item['text']!r}")
        lines.append("")
        lines.append("入群提示正则：%d 条（用 /欢迎 查看整体状态）" % len(self.config.welcome.notice_patterns or []))
        return "\n".join(lines)

    def _status_text(self, group_id: str) -> str:
        groups = len(self._seen)
        members = sum(len(v) for v in self._seen.values())
        lines = [
            f"欢迎开关：{'开' if self._welcome_enabled() else '关'}",
            f"入群提示正则：{len(self.config.welcome.notice_patterns or [])} 条",
            f"首次发言兜底：{'开' if self.config.welcome.first_message_fallback else '关'}",
            f"已登记：{groups} 群 / {members} 人",
        ]
        if group_id:
            known = len(self._seen.get(group_id, {}))
            last = self._last_trigger.get(group_id, 0.0)
            lines.append(f"本群（{group_id}）已登记 {known} 人")
            lines.append(f"上次唤醒：{'—' if not last else time.strftime('%m-%d %H:%M', time.localtime(last))}")
        return "\n".join(lines)


def create_plugin() -> GroupWelcome:
    """Runner 加载入口。"""
    return GroupWelcome()
