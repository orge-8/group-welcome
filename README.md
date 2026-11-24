# 群新人欢迎（group-welcome）

群聊新人欢迎插件。**插件自己不写欢迎语** —— 它只负责发现「有新人」，然后把这件事
交给 Maisaka Planner，由 LLM 结合人格和群氛围自行决定怎么开口。

- 插件 ID：`org.orge-8.group-welcome`
- 版本：1.5.0
- SDK 区间：2.5.0 ~ 2.99.99
- 依赖：无（不需要安装任何适配器插件之外的依赖）
- 适配器：兼容 **snowluma-adapter**（官方 napcat-adapter 已并入，Host 1.3.0 起唯一官方链路）；旧 napcat-adapter 载荷仍兼容

---

## 1. 它是怎么工作的

**为什么不直接发欢迎语？** `proactive.trigger` 的语义是「把意图写进内部上下文并唤醒
Planner」，不伪装用户消息、不发固定文案。这样欢迎语能跟随人格设定、群聊氛围和上下文，
而不是一句所有群都一样的模板。若要改成固定文案，改这一处即可（`_wake_planner`）。

### 三条信号路径（按优先级）

| 优先级 | 路径 | 数据来源 | 可靠性 | 覆盖范围 |
|---|---|---|---|---|
| **1** | **原始 notice 载荷** | `additional_config.napcat_notice_payload.user_id` | ⭐ **权威**：真实入群者 QQ | 潜水新人也覆盖 |
| 2 | 文本匹配入群提示 | 消息文本 + `notice_patterns` | ⚠️ 兜底：文本可能被标成操作者 | 同上，但身份可能错 |
| 3 | 成员首次发言 | 落盘档案比对 | ✅ 稳定 | 只覆盖"开口说话"的新人 |

**为什么路径 1 是权威的**：QQ 的 `group_increase` notice **没有"消息发送者"概念**，
适配器转换时必须指派一个发送者，**它取了 `operator_id`**（同意申请/发出邀请的人）。
于是群主邀请新人时，消息文本会显示成「群主 加入了群聊」——**文本与 `user_info` 双双失真**。
只有 `additional_config` 里的原始载荷保留了 `user_id`（真正入群的人）与 `operator_id`（操作者）。

路径 1 不可用时（适配器版本较旧）自动降级到路径 2、3。

### 适配器合并说明（v1.5.0）

官方 napcat-adapter 已合并进 **snowluma-adapter**（Host 1.3.0 起；旧 napcat-adapter
在 Host ≥1.4.0 不可加载）。本插件按 MaiBot-SnowLuma-Adapter **v1.3.2 源码**核实契约：

| 契约点 | 合并后行为 | 插件应对 |
|---|---|---|
| `additional_config.napcat_notice_*` 键名 | **保留不变**（`message_codec.py:63-67`） | 权威路径 1 无需改动 |
| 通知 `message_id` 前缀 | `napcat-notice-` → **`qq-notice-`**（`message_codec.py:93`） | 前缀表新增 `qq-notice-`，旧前缀保留 |
| 入群通知文本 | `[事件-群成员增加] {昵称} 加入了群聊 / 被邀请入群`（`renderer.py:174-186`） | 新增锚定正则；「被邀请入群」是旧正则盲区 |
| 发送者映射 | 仍取 `operator_id` 优先（`helpers.py:69-83`），操作者陷阱依旧 | 已知成员过滤等防御全部保留 |
| 昵称查询 API | `adapter.napcat.group/account.*` **同名保留**、public=True | 调用不变 |

---

## 2. 安装

1. 把整个 `group-welcome/` 目录放到 MaiBot 的 `plugins/` 下。
2. **完整重启 MaiBot**（manifest 变更不热重载）。
3. 在 WebUI 插件页确认加载成功，按需修改 `config.toml`。

开发机自检：

```bash
python check_plugin.py --plugin .      # 结构自检
python tests/smoke_test.py             # FakeHost 生命周期冒烟
python -m pytest -q tests              # 离线单测 + 真机载荷回归
python run_gates.py --plugin .         # 三合一门禁
```

当前状态：`check_plugin PASS 37 / WARN 0`、`smoke OK`、`pytest 124 passed`（含 39 条审计用例）。

---

## 3. 配置（`config.toml`）

### `[plugin]`

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 插件总开关 |
| `config_version` | `1.5.0` | 与插件版本同步，勿手改 |

### `[welcome]`

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 欢迎功能开关（总开关之外的第二道） |
| `admin_ids` | `[]` | 管理命令白名单。**留空=不鉴权（fail-open）**；兼容 `"123456789"` 与 `"qq:123456789"` |
| `group_whitelist` | `[]` | 生效群号；留空=所有群 |
| `ignore_user_ids` | `[]` | 不参与判定的 QQ（建议填 bot 自己的号） |
| `notice_patterns` | 内置 4 条 | 入群提示正则（须含命名组 `nick`）；**留空=关闭信号 A** |
| `first_message_fallback` | `true` | 是否启用信号 B |
| `warmup_seconds` | `300` | 预热期：仅约束路径 B（只登记不欢迎），**不影响入群提示** |
| `cooldown_seconds` | `10` | 同群两次唤醒最小间隔 |
| `max_triggers_per_hour` | `20` | 每群每小时唤醒上限，`0`=不限 |
| `intent_hint` | 见配置 | 追加给 planner 的措辞引导 |
| `log_unmatched_notices` | `true` | 把每条未识别为入群提示的系统通知记入日志（info 级）并存入诊断缓存 |
| `skip_known_member_notice` | `true` | 入群通知的 user_id 若已是档案中的老成员，判定为「操作者误标」并跳过欢迎 |
| `resolve_nickname` | `true` | 通过适配器接口查询新成员昵称（入群载荷只含 QQ 号；snowluma-adapter 保留 `adapter.napcat.*` API 名）；查不到则退回仅显示 QQ |
| `no_quote_hint_seconds` | `90` | 欢迎触发后多少秒内向 Planner 注入「本次不要引用」规则（0 关闭）；**仅影响欢迎当轮** |

内置 `notice_patterns`（**顺序即优先级**，改动时勿把泛化模式提前）：

```toml
notice_patterns = [
    "\\[事件-群成员增加\\]\\s+(?P<nick>\\S+)\\s+(?:加入了群聊|被邀请入群)",
    "(?P<nick>\\S+?)\\s*(?:通过|经由).{0,16}?加入",
    "(?P<nick>\\S+?)\\s*被.{0,16}?邀请(?:.{0,8}?加入|入群)",
    "欢迎新(?:成员|同学|人|朋友)\\s*[:：]?\\s*(?P<nick>\\S+)",
    "(?P<nick>\\S+?)\\s*(?:加入了?群聊|加入了?本群|加入群聊|进群了)",
]
```

> 第 1 条锚定 snowluma-adapter 的渲染格式 `[事件-群成员增加] …`（v1.5.0 新增）；
> 第 3 条兼容「被邀请入群」措辞。最后一条是泛化模式，如果被挪到最前，
> `\S+?` 会把修饰语一起吃进昵称（实测「孙七被管理员邀请加入了本群」会得到「孙七被管理员邀请」）。
>
> ⚠️ **已部署实例注意**：`config.toml` 里已写入的旧 `notice_patterns` 值**不会**随版本
> 升级自动更新（宿主只补缺键，不改已有值）。要在存量实例启用新正则，请在 WebUI
> 配置页把 `notice_patterns` 清空后保存（回落内置默认），或手动加上第 1、3 条的新形态。
> 权威路径（路径 1）不受此影响——它不依赖正则。

---

## 4. 命令

组件名 `welcome`，中文触发词写在正则里。

| 命令 | 作用 |
|---|---|
| `/欢迎` 或 `/welcome` | 查看状态（开关、正则条数、已登记人数、上次唤醒时间） |
| `/欢迎 on` / `/欢迎 off` | 开启 / 关闭欢迎 |
| `/欢迎 reset [群号]` | 清空某群成员档案（省略群号时取当前会话所在群） |
| `/欢迎 test [群号]` | 手动触发一次唤醒，用于验证链路 |
| `/欢迎 diag` | **诊断**：列出最近收到的系统通知原文，用于定位"有人入群但没反应" |

动作支持中文别名：`状态` `开` `关` `重置` `测试` `诊断`（如 `/欢迎 诊断`）。

- 命令的返回值不会自动发到群里，插件内部已显式 `ctx.send.*`。
- `admin_ids` 为空时全部放行；配置后非管理员被拒绝，只回"权限不足"，不执行动作。

---

## 4.5 跨插件 API（v1.4.0 新增）

### `get_recent_newcomers` — 只读查询最近的新成员

```python
@API("get_recent_newcomers", version="1", public=True)
async def api_get_recent_newcomers(self, since_seconds: int = 86400, limit: int = 20) -> dict
```

**参数**：

| 参数 | 默认 | 说明 |
|---|---|---|
| `since_seconds` | `86400` | 时间窗（秒），只回首次出现时间在 `now - since_seconds` 之后的成员；`<=0` = 不限窗口（全部历史） |
| `limit` | `20` | **每群**返回条数上限，钳到 `1..100`；按首次出现时间**倒序**（最新在前） |

**返回**（结构永远完整的纯 dict，任何情况下不抛异常）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | `int` | 恒为 `1` |
| `reason` | `str` | 空 = 数据正常；否则说明降级原因（见下） |
| `active` | `bool` | `on_load` 已完成、状态已加载 |
| `groups` | `dict` | `{"<群号>": {"last_welcome_at": float, "new_members": [{"user_id": str, "first_seen": float}]}}`；只含时间窗内有新成员的群 |

**`reason` 的取值**：`""`（正常）/ `插件尚未完成启动（状态未加载）`（active=False，不要用它的数据）/
`暂无任何成员记录`（档案为空）/ `时间窗内没有新成员`（窗口过滤后为空）。

**数据边界（重要）**：`seen_members.json` **只落盘 QQ 号 + 首次出现时间戳，没有昵称**——
昵称只在欢迎瞬间临时解析、从不缓存。因此本 API 只能回答"群里来了个新人（QQ 号 x，时间 t）"，
回答不了"来了谁"。`last_welcome_at` 为 `0.0` 表示该群从未触发过欢迎。

**纪律承诺（有测试守着）**：本 API **纯筛选内存态**——零网络（不查昵称接口）、零写盘
（`tests/test_cross_plugin_api.py` 断言调用前后 `_seen` 深度相等、状态文件 mtime 不变、
零 capability 调用）。调用方可按任意频率轮询。

---

## 5. 真机实测结论（2026-09-19，napcat-adapter 时代）

> 本节为历史实测记录（当时链路是独立 napcat-adapter）。合并后的 snowluma-adapter
> 契约差异与插件应对见第 1 节「适配器合并说明」；结论 1、3 在合并后依然成立
> （v1.3.2 源码核实），结论 2 的 mid 前缀已改为 `qq-notice-`（两种前缀都识别）。

**路径 A 已确认可行。** 真实日志证据：

由此确认三件事：

1. **适配器把 QQ 的 notice 转成了普通消息**，并送进 `chat.receive` hook 链 →
   入群事件可以事件驱动，无需轮询群成员列表。
2. 载荷带 **`is_notify`** 标志位，且 `message_id` 形如 **`napcat-notice-...`**（现为 `qq-notice-...`）——
   这两个是识别"系统通知"的硬信号，插件已优先使用它们（比纯文本匹配可靠）。
3. **`raw_message` 是分段 list** 而非字符串（`raw=list[text(str='...')]`），
   纯文本要从 `processed_plain_text` 取；插件已把 list 形式也兜住。

### 端到端验证结果（v1.1.0 部署后实测，2026-09-19 17:58）

**权威路径生效，身份完全正确**：

对照**同一时刻**适配器给出的消息文本：

**文本说「示例用户」，插件用的是 `1000000002`（newbie）** —— 同一秒的两条数据源指向两个不同的人，
插件取对了那个。这就是路径 1 的价值。同时实测确认 `sub_type=invite`（由群主邀请入群）。

Planner 侧的表现同样值得记录：它读到该成员在 30 秒前刚退出群聊，判断
「这个人已经离开了，再发欢迎消息会显得不合时宜，甚至有点尴尬」，于是**主动保持沉默**。
这正是 `proactive.trigger` 把决定权交给 LLM 的价值 —— 插件只负责把事件送达。

### 端到端验证结果（v1.0.1 部署后实测）

Planner 被唤醒后**结合历史记忆**产出了非模板化的欢迎语：

> 哟示例用户，好久不见，之前聊的那首歌我还记着呢，欢迎回来呀

欢迎链路完整跑通 ✅

### ⚠️ 已知上游问题：`<reject>` 文本泄漏

成因**不在本插件**，但与本插件的唤醒方式有关：

1. Planner 收到唤醒后，挑了一条**历史消息**（用户 4 小时前问歌的旧消息）去调 `reply`；
2. replyer 发现该消息已回复过 → 按机制输出 `<reject>…</reject>` 要求驳回；
3. **Host 未正确处理该标记**，把 `<reject>` 前缀和被截断的文本当作消息正文发出；
4. 该消息随后被 `repeater-recall` 插件的自评撤回功能撤回，用户侧未受影响。

v1.0.2 的应对：在 intent 中显式声明「这是一条群事件通知，不是等你回复的聊天消息，
不要引用或回复任何历史消息」，引导 Planner 直接生成新发言，从源头避开该路径。

> 若后续在其他插件里也触发 `proactive.trigger`，建议同样在 intent 里写清
> 「事件通知 ≠ 待回复消息」，否则容易命中这条上游链路。

### 深挖：入群通知的「发送者」被填成了操作者（2026-09-19 newbie 事故，v1.0.6 修正）

> ⚠️ **本节曾给出错误结论**（"通知未进入 hook 链"），后被新事实推翻；此处保留完整推理链，
> 因为推翻过程本身就是这个案例最有价值的部分。

**现象**：同群同日，「示例用户」入群被识别并欢迎，「newbie」入群"毫无反应"。

**两个关键事实**（由用户提供，直接推翻初判）：

1. `1000000002` 就是 **newbie 的 QQ 号**（依据它出现在 `1000000002 离开了群聊` 中）
2. **群主「示例用户」从来没有退出过本群**

若群主从未退群，这两条被标注成「示例用户 加入了群聊」的通知就无法解释——
而它们的时间与 newbie 的两次入群**完全吻合**（17:24 newbie 自己申请、17:31 由示例用户邀请）。

**结论：这两条通知其实就是 newbie 的入群事件，被标注成了「示例用户 加入了群聊」。**

#### 根因：notice 没有"发送者"，适配器取了 operator_id

`group_increase` notice 在 QQ 协议中的原始字段：

- `user_id` = 真正入群的人（newbie）
- `operator_id` = 操作者（群主示例用户：同意申请 / 发出邀请）

notice 事件本身**没有"消息发送者"**。适配器要把它转成一条 MaiBot 消息就必须指派发送者，
**它取了 `operator_id`**。后果：

- 文本被生成为 `示例用户 加入了群聊`（≠ QQ 客户端的 `newbie加入了群聊。点击修改TA的群昵称`）
- `message_info.user_info.user_id = 1000000001`（示例用户）
- v1.0.5 一度把这个 id 当作"入群者本人"回填进 intent 与档案 —— **等于把操作者写成了新人**

#### 插件侧兜底：v1.0.6 的「已知成员过滤」

既然通知里的 user_id 可能是操作者，而操作者通常是群内老成员：

> **若入群通知携带的 user_id 已在本群档案中（老成员），判定为「操作者视角的误标事件」，跳过欢迎。**

- 配置项 `skip_known_member_notice`（默认 `true`）
- 命中时记 warning 日志，并在 `/欢迎 diag` 中标为 `[疑似误标·已跳过]`
- **代价**：老成员退群重入不再被欢迎（他已在档案里）。权衡后接受——
  "热情欢迎一个根本没入群的人"是群里可见的尴尬错误。

#### ✅ 已解决（v1.1.0）：原始载荷里就有正确答案

**不需要等适配器修，也不需要轮询。** 原始 notice 一直躺在
`message_info.additional_config.napcat_notice_payload` 里，其中的 `user_id` 才是真正的入群者：

v1.1.0 起插件优先读它（路径 1），身份判定由"猜文本"变为"读权威字段"。

线索来源：[ji-or-ji/group-awareness-plugin](https://github.com/ji-or-ji/group-awareness-plugin)（MIT）
—— 它用同一字段处理进群/退群/禁言等事件，且已踩过「退群时 `get_group_member_info`
必然失败（成员已不在群）」这个坑。**该仓库的实现值得完整阅读。**

v1.0.6 的「已知成员过滤」仍保留，作为路径 2（文本兜底）的防线。

### 排查顺序（通用，从上游到下游）

| 步 | 检查 | 结论 |
|---|---|---|
| 1 | 全局日志搜该昵称 | 若连 `file-reader` 的 `[诊断] file hook[before][msg]` 行都没有 → **适配器未把该通知转成消息**，插件收不到，只能靠"首次发言"路径兜底 |
| 2 | `/欢迎 diag` 或搜 `收到系统通知` | 出现该条文本 → 通知进来了但**正则没匹配**，照原文补 `notice_patterns` |
| 3 | 搜 `命中入群提示` | 有 → 插件已识别，问题在下游 |
| 4 | 搜 `已唤醒 Planner` | 有 → 插件职责已完成，后续是 Planner 行为 |

`/欢迎 diag` 的输出可直接判断第 2 步：

**命中与未命中都会入缓存**，并标注 `[已识别入群]` / `[未识别为入群]`，
所以"刚有人入群"时不会出现空的诊断结果。

若缓存为空、而刚有人入群 → 直接落到第 1 步的结论。

### 部署后自查

| 日志 | 含义 |
|---|---|
| `group-welcome 已加载：群 N 个 / 已登记成员 M 人` | 落盘档案成功恢复 |
| `命中入群提示：群=... 昵称=... mid=...` | 路径 A 命中 |
| `已唤醒 Planner：群=... 成员=... 依据=notice\|first_message` | 链路打通 |
| `预热期内登记成员（不欢迎）：... 剩余=Ns` | 被预热期抑制（仅路径 B 会这样） |
| `同群冷却中，本次不唤醒（间隔 Ns）` | 被冷却抑制 |
| `已达每小时唤醒上限（N 次），本次不唤醒` | 被配额抑制 |
| `未能确定聊天流，跳过唤醒` | `session_id` 缺失且群号反查失败 |
| `唤醒 Planner 失败：...` | 宿主能力异常（多为未声明能力） |

---

## 6. 故障排查

| 症状 | 排查方向 |
|---|---|
| 插件加载失败 | `_manifest.json` 是否带 BOM；三个生命周期方法是否齐全 |
| 报 `E_CAPABILITY_DENIED: maisaka.proactive.trigger` | manifest 的 `capabilities` 是否含该名；**改动后必须完整重启** |
| 完全没有反应 | 看日志有无 `命中入群提示` / `预热期内登记成员` 行定位卡在哪一步 |
| 入群了但没欢迎 | 是否被 `cooldown_seconds` / `max_triggers_per_hour` / `group_whitelist` 拦住（均有日志） |
| 老成员被当成新人欢迎 | 调大 `warmup_seconds`，或 `/欢迎 reset` 后重新积累档案 |
| 一群人同时被欢迎（刷屏） | 调大 `cooldown_seconds` 或调小 `max_triggers_per_hour` |
| 每次发言都唤醒 | `notice_patterns` 过宽，误把普通聊天判成入群提示 |
| 改了代码不生效 | 依赖模块命中 `sys.modules` 缓存 → 完整重启 + 行为自检 |

---

## 7. 数据落盘

- 路径：`ctx.paths.data_dir / seen_members.json`
- 内容：每群的成员档案（QQ → 首次见到时间）与上次唤醒时间
- 写入：原子替换（临时文件 + rename），节流 5 秒；`on_unload` 时强制落盘
- 容错：文件损坏时按空档案启动并记 warning，**不阻断插件加载**
- 落盘键用群号/QQ 号字符串，不使用内置 `hash()`（带随机盐，跨重启不稳定）

---

## 8. 已知边界

- **路径 A 现在也带 QQ 号**（v1.0.5 起）：入群通知载荷的 `user_info.user_id` 实测即入群者本人；
  载荷异常时退回「仅昵称」。
- **部分入群通知不会进入 hook 链**（第 5 节已实锤）：适配器未转发的子类型
  （疑似"全新成员经申请通过"路径）插件**完全收不到**。此时只能依赖「首次发言」路径兜底；
  若要连潜水新人也覆盖，需经 `ctx.api.call` 轮询适配器的 `get_group_member_list` 做差集
  （**当前未实现**，代价是轮询开销 + 大群全量查询）。
- **同一人多群入群**：档案按群隔离，各群独立欢迎一次。
- **`proactive.trigger` 要求目标聊天流已存在**：notice 载荷自带 `session_id`，
  正常可用；缺失时才回退 `chat.get_stream_by_group_id`，该群从未产生过消息则会失败并跳过。
- **改群名 / 撤回 / 公告等通知不会触发欢迎**：由 `is_notify` + 文本双重判定排除。
- **引用显示无法由插件强制**（v1.2.3 查证）：`set_quote` 是 Planner 调 `reply` 工具的参数
  （`builtin_tool/reply.py:305`，**默认 `true`**），插件只能引导、无法强制 ——
  `before_model_request` Hook 在模型请求**前**触发，改不了模型输出；
  `reply_tool_args` 在 replyer 侧只读；`proactive.trigger` 也**不接受工具参数**。
  兜底：引导模型把引用挂在自己最近的消息上，避免误挂到群友头上。
- **命令消息不参与新人判定**：`/` 开头的消息即使来自未登记成员也不会被当成首次发言。
- **同一人短时间内重复入群，Planner 可能主动选择不发言**。真机实测（17:18）Planner
  读到了 15 分钟前的欢迎记录，判断"再发一条会显得重复、机械"，于是保持安静。
  **这是 `proactive.trigger` 的设计意图**——由 LLM 决定"该不该说话"，而不是插件无脑触发；
  插件只负责把事件送达（日志仍会有 `已唤醒 Planner`）。若要每次都看到欢迎效果，
  换一个未用过的账号测试即可（Planner 的记忆是按人/会话建立的）。

> **版本细节见 [CHANGELOG.md](CHANGELOG.md)** —— 本文件只写「怎么装、怎么配、怎么排障」。

## 9. 开发

```bash
python check_plugin.py --plugin .   # 结构自检（零依赖）
python tests/smoke_test.py          # 冒烟（需 maibot-plugin-sdk，缺失则 SKIP）
python -m pytest -q tests           # 离线单测
python run_gates.py --plugin .      # 交付门禁
```

测试覆盖：入群提示正则（含顺序敏感用例）、载荷多路径兜底、**真机 notice 载荷回归**
（list 形式的 `raw_message`、`is_notify`、`napcat-notice-` 前缀）、
**SnowLuma v1.3.2 契约回归**（`qq-notice-` 前缀、`[事件-群成员增加]` 两种措辞、
操作者视角 user_info、空 session_id 反查、bot 自身入群排除）、
去重/冷却/配额/预热、状态落盘与损坏容错、管理命令与鉴权前缀、组件注册落点、manifest 能力反查。
