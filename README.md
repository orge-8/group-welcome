# 群新人欢迎（group-welcome）

群聊新人欢迎插件。**插件自己不写欢迎语** —— 它只负责发现「有新人」，然后把这件事
交给 Maisaka Planner，由 LLM 结合人格和群氛围自行决定怎么开口。

- 插件 ID：`org.orge-8.group-welcome`
- 版本：1.3.0
- SDK 区间：2.5.0 ~ 2.99.99
- 依赖：无（不需要 napcat-adapter）

---

## 1. 它是怎么工作的

```
入站消息 ──> chat.receive.before_process (BLOCKING/EARLY)
                │
                ├─ 是否系统通知？ is_notify / message_id 前缀 napcat-notice-
                │       │
                │       └─ 是 ──> 文本命中「入群提示」正则 ──> 提取新人昵称（路径 A）
                │                     └─ 不是入群（改名/撤回/公告）→ 跳过
                │
                └─ 否 ──> 该 QQ 首次在本群出现 ──> 取发送者身份（路径 B）
                │
                v
        冷却 / 每小时配额 / 落盘去重
                │
                v
   ctx.maisaka.proactive.trigger(stream_id, intent)
                │
                v
        Planner 被唤醒，LLM 自行决定欢迎措辞
```

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
napcat-adapter 转换时必须指派一个发送者，**它取了 `operator_id`**（同意申请/发出邀请的人）。
于是群主邀请新人时，消息文本会显示成「群主 加入了群聊」——**文本与 `user_info` 双双失真**。
只有 `additional_config` 里的原始载荷保留了 `user_id`（真正入群的人）与 `operator_id`（操作者）。

```python
additional_config = {
    "napcat_notice_type": "group_increase",
    "napcat_notice_sub_type": "increase",
    "napcat_notice_payload": {
        "group_id": "...",
        "user_id": "1264805399",      # ← 真正入群的人
        "operator_id": "3816023959",  # ← 操作者（群主）
        "self_id": "...",             # ← bot 自己
    },
}
```

路径 1 不可用时（适配器版本较旧）自动降级到路径 2、3。

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
| `config_version` | `1.3.0` | 与插件版本同步，勿手改 |

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
| `resolve_nickname` | `true` | 通过 napcat 接口查询新成员昵称（入群载荷只含 QQ 号）；查不到则退回仅显示 QQ |
| `no_quote_hint_seconds` | `90` | 欢迎触发后多少秒内向 Planner 注入「本次不要引用」规则（0 关闭）；**仅影响欢迎当轮** |

内置 `notice_patterns`（**顺序即优先级**，改动时勿把泛化模式提前）：

```toml
notice_patterns = [
    "(?P<nick>\\S+?)\\s*(?:通过|经由).{0,16}?加入",
    "(?P<nick>\\S+?)\\s*被.{0,16}?邀请.{0,8}?加入",
    "欢迎新(?:成员|同学|人|朋友)\\s*[:：]?\\s*(?P<nick>\\S+)",
    "(?P<nick>\\S+?)\\s*(?:加入了?群聊|加入了?本群|加入群聊|进群了)",
]
```

> 最后一条是泛化模式，如果被挪到最前，`\S+?` 会把修饰语一起吃进昵称
> （实测「孙七被管理员邀请加入了本群」会得到「孙七被管理员邀请」）。

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

## 5. 真机实测结论（2026-09-19）

**路径 A 已确认可行。** 真实日志证据：

```
[file-reader 诊断] file hook[before][msg] 触发:
    mid='napcat-notice-9532db6b5c3e40db846807a4e80601d0'
    raw=list[text(str='狸猫 修改了群名称')]
    plain='狸猫 修改了群名称'
[所见] [bot测试]狸猫:狸猫 加入了群聊
```

由此确认三件事：

1. **napcat-adapter 把 QQ 的 notice 转成了普通消息**，并送进 `chat.receive` hook 链 →
   入群事件可以事件驱动，无需轮询群成员列表。
2. 载荷带 **`is_notify`** 标志位，且 `message_id` 形如 **`napcat-notice-...`** ——
   这两个是识别"系统通知"的硬信号，插件已优先使用它们（比纯文本匹配可靠）。
3. **`raw_message` 是分段 list** 而非字符串（`raw=list[text(str='...')]`），
   纯文本要从 `processed_plain_text` 取；插件已把 list 形式也兜住。

### 端到端验证结果（v1.1.0 部署后实测，2026-09-19 17:58）

**权威路径生效，身份完全正确**：

```
17:58:29 [group-welcome] 原始入群通知（权威）：群=1107603097 新人=1264805399 操作者='3816023959' sub_type=invite
17:58:29 [group-welcome] 已唤醒 Planner：群=1107603097 成员=1264805399 依据=notice_payload
```

对照**同一时刻**适配器给出的消息文本：

```
17:58:29 [所见] [bot测试群]狸猫:狸猫 加入了群聊      ← 文本仍是操作者「狸猫」
```

**文本说「狸猫」，插件用的是 `1264805399`（tori）** —— 同一秒的两条数据源指向两个不同的人，
插件取对了那个。这就是路径 1 的价值。同时实测确认 `sub_type=invite`（由群主邀请入群）。

Planner 侧的表现同样值得记录：它读到该成员在 30 秒前刚退出群聊，判断
「这个人已经离开了，再发欢迎消息会显得不合时宜，甚至有点尴尬」，于是**主动保持沉默**。
这正是 `proactive.trigger` 把决定权交给 LLM 的价值 —— 插件只负责把事件送达。

### 端到端验证结果（v1.0.1 部署后实测）

```
17:01:21 [group-welcome] 预热期内命中入群提示，按确定性事件放行：群=1107603097 昵称=狸猫
17:01:21 [group-welcome] 命中入群提示：群=1107603097 昵称=狸猫 mid=napcat-notice-5333c249...
17:01:21 [group-welcome] 已唤醒 Planner：群=1107603097 成员=狸猫 依据=notice
```

Planner 被唤醒后**结合历史记忆**产出了非模板化的欢迎语：

> 哟狸猫，好久不见，之前聊的那首歌我还记着呢，欢迎回来呀

欢迎链路完整跑通 ✅

### ⚠️ 已知上游问题：`<reject>` 文本泄漏

同一轮日志里出现过一次异常发送：

```
message=坠入深空 追逐自由，...@鸣澜bot 这是什么歌 <reject>该消息已在此前聊天流中回复过歌名，规
```

成因**不在本插件**，但与本插件的唤醒方式有关：

1. Planner 收到唤醒后，挑了一条**历史消息**（用户 4 小时前问歌的旧消息）去调 `reply`；
2. replyer 发现该消息已回复过 → 按机制输出 `<reject>…</reject>` 要求驳回；
3. **Host 未正确处理该标记**，把 `<reject>` 前缀和被截断的文本当作消息正文发出；
4. 该消息随后被 `repeater-recall` 插件的自评撤回功能撤回，用户侧未受影响。

v1.0.2 的应对：在 intent 中显式声明「这是一条群事件通知，不是等你回复的聊天消息，
不要引用或回复任何历史消息」，引导 Planner 直接生成新发言，从源头避开该路径。

> 若后续在其他插件里也触发 `proactive.trigger`，建议同样在 intent 里写清
> 「事件通知 ≠ 待回复消息」，否则容易命中这条上游链路。

### 深挖：入群通知的「发送者」被填成了操作者（2026-09-19 tori 事故，v1.0.6 修正）

> ⚠️ **本节曾给出错误结论**（"通知未进入 hook 链"），后被新事实推翻；此处保留完整推理链，
> 因为推翻过程本身就是这个案例最有价值的部分。

**现象**：同群同日，「狸猫」入群被识别并欢迎，「tori」入群"毫无反应"。

**两个关键事实**（由用户提供，直接推翻初判）：

1. `1264805399` 就是 **tori 的 QQ 号**（依据它出现在 `1264805399 离开了群聊` 中）
2. **群主「狸猫」从来没有退出过本群**

若狸猫从未退群，这两条通知就无法解释：

```
[17:24:35] [已识别入群] '狸猫 加入了群聊'
[17:31:06] [已识别入群] '狸猫 加入了群聊'
```

而它们的时间与 tori 的两次入群**完全吻合**（17:24 tori 自己申请、17:31 由狸猫邀请）。

**结论：这两条通知其实就是 tori 的入群事件，被标注成了「狸猫 加入了群聊」。**

#### 根因：notice 没有"发送者"，适配器取了 operator_id

`group_increase` notice 在 QQ 协议中的原始字段：

```json
{
  "post_type": "notice",
  "notice_type": "group_increase",
  "sub_type": "approve",
  "group_id": 1107603097,
  "user_id": 1264805399,
  "operator_id": 3816023959
}
```

- `user_id` = 真正入群的人（tori）
- `operator_id` = 操作者（群主狸猫：同意申请 / 发出邀请）

notice 事件本身**没有"消息发送者"**。适配器要把它转成一条 MaiBot 消息就必须指派发送者，
**它取了 `operator_id`**。后果：

- 文本被生成为 `狸猫 加入了群聊`（≠ QQ 客户端的 `tori加入了群聊。点击修改TA的群昵称`）
- `message_info.user_info.user_id = 3816023959`（狸猫）
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

```
napcat_notice_payload = { "group_id": ..., "user_id": "1264805399",      # ← tori
                          "operator_id": "3816023959", "self_id": ... }  # ← 群主狸猫
```

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

```
【诊断】最近收到的系统通知（最多 20 条）：
[17:17:36] 群=1107603097 [已识别入群] '狸猫 加入了群聊'
[17:16:02] 群=1107603097 [未识别为入群] '某人 修改了群名称'
入群提示正则：4 条
```

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
  若要连潜水新人也覆盖，需引入 napcat-adapter 依赖并轮询 `get_group_member_list` 做差集
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

---

## 9. 变更日志

### v1.3.0（2026-09-23）— 新增 Planner 注入：欢迎当轮强制引导「不引用」

**需求**（用户提出）：**按场景控制** —— 欢迎新成员时不引用（避免挂错人），其他场合照常引用。

v1.2.3 把引用策略写在 `intent` 里，但 `intent` 属于「任务描述」，模型未必当硬约束。
本版改在**更靠近决策的位置**注入：往 Planner 请求的 `items` 追加一条系统级发言规则。

| 项 | 说明 |
|---|---|
| Hook | `maisaka.planner.before_request`（`BLOCKING` / `EARLY` / `SKIP`） |
| 注入位置 | Planner 请求的 `items`（当前 Host 版本真正生效的路径） |
| 生效范围 | **仅该会话的「欢迎窗口」内**（默认 90 秒），其他场合引用行为**完全不受影响** |
| 幂等 | 用 `[welcome-no-quote]` 标记做重复检查，同一请求不会注入两次 |
| 配置 | `no_quote_hint_seconds`（默认 90，0 = 关闭） |

注入文本：

```
[welcome-no-quote] 本次发言规则：你正在**欢迎一位新成员**。
请调用 reply 生成欢迎语，并**必须把 set_quote 设为 false**（不引用任何消息），
让这条发言独立出现 —— 引用无关消息会让群友误以为你在回复他。
```

实现参考 `character-recognizer` 的 `inject.py`（同作者维护，MIT）——
它验证过 `items` 是当前版本生效的注入路径（`messages` / `prompt` 属旧版本兜底）。
本插件只走 `items`，不做多形态猜测；形态不匹配时记 warning 并放弃（不猜、不猜错）。

**⚠️ 仍是引导而非强制**：模型输出（工具参数）无法被插件干预。
本版的作用是把"选对"的概率显著提上去，而不是保证。详见「已知边界」。

新增 7 条测试：窗口内注入、窗口外跳过、开关关闭、幂等、畸形 items 安全返回、
唤醒后开窗、关闭时不建窗。

### v1.2.3（2026-09-23）— 承认 `set_quote` 无法强制，改为「引导 + 兜底」双保险

v1.2.2 我把主策略定为「请把 `set_quote` 设为 false」。但用户追问：
**它真的会照做吗？** 查证后必须承认：**插件无法强制**。

| 环节 | 事实 | 能否干预 |
|---|---|---|
| `set_quote` 来源 | `reply.py:305` 从 Planner 的 `invocation.arguments` 取，**默认 `True`** | — |
| `reply_tool_args` 在 replyer | 只读，仅用于拼 prompt 说明 | ❌ |
| Hook 时机 | `before_model_request` 在**请求前**，模型输出尚未产生 | ❌ |
| `proactive.trigger` 参数面 | 只有 `stream_id` / `intent` / `reason` / `priority` | ❌ |

**所以那句话是祈使句，不是保证。** 又一次把方案可靠性押在了"模型会照做"上。

| 修复 | 说明 |
|---|---|
| **加兜底指引** | 「若确实需要指定回复对象，请选择**你自己最近发出的一条消息**，切勿引用他人的消息」—— 万一模型没关引用，也让它把引用挂在自己身上，而不是群友头上 |
| **移除通知编号** | `notice_mid` 不再写进 intent（可回复性未获确证）；参数保留在签名中但显式 `del`，并注释说明原因 |
| 测试改向 | 两条端到端用例从"mid 应进入 intent"改为**反向断言**：未验证的编号**不得**泄漏进 prompt |

**教训**：**当方案依赖"模型会服从"时，要么找到强制手段，要么准备兜底。**
`proactive.trigger` 只给 intent、不给工具参数，这是它的设计边界 ——
凡是需要"确定性行为"的地方，都不该指望 prompt 本身。

### v1.2.2（2026-09-23）— 改用「关闭引用显示」为主策略，不再押注通知编号

v1.2.1 我让 intent 指定「回复本条通知（消息编号 `napcat-notice-xxx`）」，
但**这个编号能否作为 reply 目标，我没有验证过**。据本机 MaiBot 源码核查：

```python
# src/maisaka/runtime.py:1121
def find_source_message_by_id(self, message_id):
    for history_message in reversed(self._chat_history):
        if history_message.message_id != message_id:
            continue
        original_message = getattr(history_message, "original_message", None)
        if original_message is None:
            continue                      # ← 第二个条件
        return original_message
```

必须同时满足：**id 精确匹配** + **`original_message` 非空**。
而通知的 `message_id` 是适配器自造的
（`f"napcat-notice-{uuid4().hex}"`，见
`MaiBot-Napcat-Adapter/codecs/notice/message_codec.py:76`），
且 Host 在写 Planner 上下文时对通知显式**关闭了 id 展示**：

```python
# src/maisaka/runtime.py:517
include_message_id=not message.is_notify and bool(message.message_id),
```

**结论**：通知编号的可回复性**未获确证**，不应作为主路径。

| 修复 | 说明 |
|---|---|
| **主策略改为 `set_quote=false`** | 不依赖任何 id 的存在性 —— 群友看到的就是一条干净的独立欢迎语 |
| 通知编号降级为「可选项」 | 附在末尾（「若要指定回复对象，可回复本条通知（编号 xxx）」），不再是唯一路径 |

**教训**：**不要在 prompt 里放进自己没验证过的值。**
v1.2.1 我刚说完"给模型的输入要完整"，转头就往 intent 里塞了个未经验证的 id ——
与前面几轮的错误本质相同，只是这次错在"以为自己验证过了"。

### v1.2.1（2026-09-23）— 指定引用锚点，避免欢迎语"挂"在无关消息上

真机现象（群里的实际画面）：

```
占空比依加入了群聊。
鸣澜bot ┃ [引用 风卷恋苍穹 的「不」+ 表情包]
        ┃ 欢迎欢迎，群里又有新频率接入啦
风卷恋苍穹：?          ← 被引用的人表示困惑
```

**成因是 v1.1.2 的措辞**：当时为避免 Planner 乱试 `msg_id`，我写了
「若发言必须回复某条消息，**选群内最近的一条即可，不必纠结回复对象**」。
它忠实执行了 —— 选了最近的一条（别人的表情包）当锚点，
而 replyer 默认 `set_quote=true`，于是群里显示成「引用那个表情包说欢迎」。
被引用的群友当然看不懂。

| 修复 | 说明 |
|---|---|
| **指定引用锚点** | intent 带上**入群通知自身的消息 ID**：「如需回复，请回复本条通知（消息编号 xxx）；不要引用其他群成员的消息」 |
| 兜底关闭引用 | 无 mid 时要求 `set_quote=false`，避免引用无关消息 |
| 三条路径都传 mid | 权威 / 文本 / 首次发言路径分别传入各自的通知或消息 ID |

新增 4 条测试覆盖：引用锚点写入 intent、无 mid 时的降级指引、两条路径的 mid 透传。

> **「入群通知自身有 msg_id 吗？」—— 已核实（2026-09-23，宿主与适配器源码）**
>
> - adapter 在 `codecs/notice/message_codec.py:76` 生成
>   `"message_id": f"napcat-notice-{uuid4().hex}"`
> - Host 在 `mai_message_data_model.py:129` 用 `msg_id = str(msg_info.message_id)`
>   **直接沿用该值**，不重新分配
> - `message_repository.py:81` 的聊天记录过滤条件是 `Messages.message_id != "notice"`
>   （**只排除字面量 `"notice"`**），`napcat-notice-<hex>` 不受影响
>
> ⇒ **通知会入库、可被引用，本方案成立。**
> 唯一失效场景：将来 adapter 改用 `message_id = "notice"` 对齐宿主的字面量约定 ——
> 那时引用会落空，但 `set_quote=false` 的兜底仍然有效。

**教训**：把"选择权"下放给模型时，**要给出明确的候选**。
只说"随便挑一条"，它就会挑最省事的那条（最近的），而不是最合适的那条。
（与 §60.6 同源：约束要给可行路径 —— 这次是"路径给了，但没给目标"。）

### v1.2.0（2026-09-19）— 补查昵称；并据 adapter 源码确认 `operator_id` 属设计

**问题**：权威载荷只含 QQ 号不含昵称，intent 写成「新成员入群：2336884608（QQ 2336884608）」
—— 重复且没有称呼。

**修复**：主动查昵称。接口与字段名取自**已核实的 adapter 源码**（不再靠猜）：

| 来源 | 内容 |
|---|---|
| `apis/group.py:173` | `adapter.napcat.group.get_group_member_info` |
| `apis/account.py:41` | `adapter.napcat.account.get_stranger_info` |
| `codecs/notice/enricher.py:52-55` | 返回字段为 `nickname` 与 `card`（**群名片优先**） |

入群时成员已在群内，主路径可用；查询失败静默退回「仅 QQ 号」，不影响欢迎流程。
新增配置 `resolve_nickname`（默认开）可关闭该查询。

#### 据源码确认：`operator_id` 不是 bug，是 adapter 的设计

```python
# codecs/notice/helpers.py:69
def resolve_actor_user_id(payload):
    """解析通知事件中的操作者用户号。"""
    actor_user_id = str(payload.get("operator_id") or payload.get("user_id") or "")
```

adapter **有意**把 notice 的"行为人"定义为**操作者**，消息文本与 `user_info` 都用它渲染。
所以本插件读 `additional_config.napcat_notice_payload.user_id` 取真实入群者
是**唯一正确路径**，**无需也不应向 adapter 提 issue**。

`additional_config` 的完整构成（`codecs/notice/message_codec.py:56`）：

```python
{
  "self_id": ..., "napcat_notice_type": ..., "napcat_notice_sub_type": ...,
  "napcat_notice_payload": dict(payload),   # 完整原始载荷，含 user_id / operator_id
}
```

新增 7 条测试覆盖昵称解析：群名片优先、昵称回退、结构异常降级、非数字 id 不发查询、
昵称进入 intent、无昵称时不重复表述、开关可关闭。

### v1.1.3（2026-09-19）— 提示 Planner「历史里的入群通知文本不可信」

真机反馈（19:39）：插件自己的识别是对的（`2336884608`），**但 Planner 仍被带偏**。
它的 `reply_reference` 原文：

> …但那时狸猫尚未正式入群。**现在狸猫正式加入群聊**，鸣澜可以简短回应一下。

Planner 同时看到两条互相矛盾的信息 —— intent 说新人是 `2336884608`，
而聊天历史里有条 `狸猫 加入了群聊` —— **它信了历史消息那条**。

| 修复 | 说明 |
|---|---|
| intent 增加信息源优先级声明 | 「群内聊天记录里那条「…加入了群聊」的通知可能把入群者标成了邀请人，**那一条不可信**；请一律以本通知给出的 QQ 号为准」 |

**教训**：光把正确的数据交给模型不够，**当环境里同时存在错误的同源信息时，
必须显式声明哪个为准**，否则模型可能选中错的那条。

### v1.1.2（2026-09-19）— 修正唤醒措辞：约束必须留出可行路径

真机反馈（19:14）：权威路径识别完全正确（新人 `2336884608`），但 Planner **连续两次 reply 失败**：

```
19:14:20 reply [失败]: 未找到要回复的目标消息，msg_id=1789816438111   ← proactive 任务编号
19:14:50 reply [失败]: 未找到要回复的目标消息，msg_id=19:13:57       ← 时间串
19:15:06 reply [成功]: msg_id='1631687912'
```

**成因在 v1.0.2 的措辞**：当时为避免"引用 4 小时前的旧消息"，intent 写了
「不要引用或回复任何历史消息」—— 但 **Planner 唯一可用的发言工具就是 `reply`，
而 `reply` 要求 `msg_id`**。指令与工具箱冲突，它只能拿手边像 ID 的东西去试。

| 修复 | 说明 |
|---|---|
| 改为「不要专门回复很久以前的历史消息；若发言必须回复某条消息，选群内最近的一条即可」 | 保留"别翻旧账"的意图，同时给出可执行路径 |

**通用教训**：给 LLM 下约束时，必须同时给出**在该约束下仍然可执行**的路径，
否则模型只会用错误的方式去满足约束。

### v1.1.1（2026-09-19）— 上线前全检修复

按 `maibot-plugin-audit` 三阶段流程全检，发现并修复 2 处缺陷，新增 30 条审计用例。

| 缺陷 | 现象 | 修复 |
|---|---|---|
| **权威载荷缺 `user_id` 时降级到文本路径**（中危） | 载荷是 `group_increase` 但 `payload.user_id` 为空时，改用文本 + `user_info.user_id` 触发 → 欢迎的是**操作者**（群主），不是入群者 | 改为 **fail-closed**：拿不到事件主体时记 warning 并跳过，**不降级** |
| **后台任务异常静默丢失**（低危） | `_spawn` 只做 discard，不取 `task.exception()`，唤醒失败时日志一片空白 | `_on_done` 检查 `cancelled()` / `exception()`，异常以 ERROR 记录 |

**安全面结论**：插件不发起网络请求、不持有凭据，故 skill 高危清单中的
凭据外发 / SSRF / 反序列化三类**整体不适用**（零依赖设计的直接收益）。

新增 `tests/test_audit.py`：安全（AST 判定）/ 注入面 / 副作用守卫 / 边界输入 /
性能（事件循环不阻塞）/ 生命周期 六组，已固化进门禁 pytest 步骤。

### v1.1.0（2026-09-19）— 改用原始 notice 载荷，身份判定由「猜」变「读」

**根因解决。** 此前几轮排查都建立在"只能用文本 / `user_info` 判断身份"这个前提上，
而该前提是错的：napcat-adapter 会把原始 OneBot notice 放进
`message_info.additional_config`，其中的 `user_id` 才是真正的入群者。

线索来自 [ji-or-ji/group-awareness-plugin](https://github.com/ji-or-ji/group-awareness-plugin)（MIT）。

| 改进 | 说明 |
|---|---|
| 新增**路径 1（权威）** | 优先读 `additional_config.napcat_notice_payload`：`user_id` = 真实新人、`operator_id` = 操作者、`self_id` = bot 自己（用于排除自己入群） |
| 文本匹配降级为**路径 2（兜底）** | 适配器不提供 `additional_config` 时仍可用 |
| 首次发言保持为**路径 3** | 覆盖"开口说话"的新人 |
| `user_id` 检查后置 | 避免 `user_info.user_id` 为空时挡住权威路径 |

新增 7 条测试，其中 `test_authoritative_path_welcomes_the_real_newcomer`
直接复刻真机场景：文本写着「狸猫」（操作者），而 intent 里必须出现 tori 的 QQ。

### v1.0.6（2026-09-19）— 推翻初判：通知身份被标错，新增「已知成员过滤」

用户提供两个关键事实（`1264805399` = tori 的 QQ、**狸猫从未退群**），推翻了 v1.0.5 的结论：

- ❌ 旧结论：入群通知没进 hook 链（适配器未转发）
- ✅ 新结论：**通知一直进来了，但发送者被填成了 `operator_id`（操作者狸猫）**，
  真正的入群者 tori 在文本与 `user_id` 里都看不到

| 改进 | 说明 |
|---|---|
| 新增 `skip_known_member_notice`（默认开） | 入群通知的 user_id 若已是档案中的老成员 → 判定为误标事件，跳过欢迎 |
| 诊断标记新增第三态 | `/欢迎 diag` 输出 `[疑似误标·已跳过]` |
| 修正 v1.0.5 的错误注释 | 明确该 user_id 可能是操作者，不能无条件当作入群者 |

新增 4 条测试覆盖：已知成员被跳过、陌生成员不受影响、防御可关闭、诊断第三态。

### v1.0.5（2026-09-19）— notice 载荷回填 QQ 号

真机实测确认入群通知载荷的 `user_info.user_id` 是**入群者本人**
（`载荷user_id='3816023959'` = 狸猫），不是系统号。据此：

| 改进 | 说明 |
|---|---|
| 路径 A 的 intent 带上 QQ 号 | 由「仅有昵称」升级为「昵称（QQ xxx）」，便于 Planner 做身份关联 |
| 路径 A 登记入群者档案 | 该成员入群后第一次发言不会被「首次发言」路径重复欢迎 |
| 新增 `_looks_like_user_id` 兜底 | 载荷异常（非纯数字 / 长度不合理）时退回「仅昵称」，不污染档案 |

新增 4 条测试覆盖 QQ 校验、intent 携带、档案登记与异常回退。

### v1.0.4（2026-09-19）— v1.0.3 真机复盘的四处修正

部署 v1.0.3 后实测日志暴露出的问题：

| 问题 | 说明 | 修复 |
|---|---|---|
| **诊断缓存漏记命中项** | 原设计只缓存"未命中"的通知，于是「刚有人入群」时 `/欢迎 diag` 反而显示「暂无记录」——最该有数据的场景看不见东西 | 命中与未命中**都入缓存**，并用 `[已识别入群]` / `[未识别为入群]` 标注 |
| **命令消息被当成新人候选** | 发 `/欢迎 diag` 时，因该 QQ 不在 seen 档案中，被「首次发言」路径判定为新人——恰在预热期才只登记未欢迎，否则**发一条管理命令就会触发一次欢迎** | 新增 `_is_command_message`，优先用载荷的 `is_command`，缺失时按 `/` `／` 文本形态兜底 |
| 命中日志缺少载荷 user_id | 无法确认适配器把 notice 的 `user_info` 填成了谁 | 命中日志增加 `载荷user_id=`，用于判断能否从"仅昵称"升级到"昵称 + QQ 号" |
| — | — | 新增 5 条测试覆盖命令隔离与缓存命中 |

### v1.0.3（2026-09-19）— 未命中通知的可观测性

真机事故：**同日同群，「狸猫」入群被正确识别，而「tori」入群时插件毫无反应。**
排查时发现无从下手——未识别为入群提示的系统通知只记 `debug` 级，
真机日志里根本看不到收到了什么，因此无法区分「适配器没转发」与「正则没匹配」。

| 改进 | 说明 |
|---|---|
| 未命中的系统通知改记 **info** 级 | 输出 `mid` 与通知原文，日志里直接可见 |
| 新增内存诊断缓存（最近 20 条） | 配合 `/欢迎 diag` 可在群内直接查看，无需翻日志 |
| 新增 `/欢迎 diag` 命令 + 中文动作别名 | `状态` `开` `关` `重置` `测试` `诊断` |
| 新增配置 `log_unmatched_notices` | 通知过于频繁时可关闭日志输出（缓存仍保留） |

新增 7 条测试覆盖缓存上限、诊断文本、命令动作、中文别名与命令正则。

### v1.0.2（2026-09-19）— 唤醒措辞修正

v1.0.1 部署后真机日志显示：链路已通、欢迎语也生成了，但 Planner 挑了一条**历史消息**
去调 `reply`，使欢迎变成"引用回复"，并触发 replyer 的重复回复驳回路径
（`<reject>` 文本一度被当作消息发出，随后被 repeater-recall 撤回）。

| 修复 | 说明 |
|---|---|
| intent 显式声明「事件通知 ≠ 待回复消息」 | 并明确要求「不要引用或回复任何历史消息」，引导 Planner 直接生成新发言 |

新增回归断言 `test_intent_declares_event_and_forbids_history_reply` 锁死该措辞。

### v1.0.1（2026-09-19）— 真机日志驱动修复

依据真机日志（`mid=napcat-notice-...`、`raw_message` 为 list、`is_notify` 字段）：

| 修复 | 说明 |
|---|---|
| **预热期不再吞掉入群提示** | 预热期本意是让"首次发言"路径先建基线；入群提示是确定性事件，抑制它会让明确的新人欢迎失败。这是本轮真机无反应的根因 |
| 新增 `is_notify` / `message_id` 前缀识别 | 系统通知判定不再只靠文本，可靠性大幅提升 |
| `raw_message` 分段 list 兼容 | `processed_plain_text` 优先，list 形式兜底 |
| **补齐诊断日志** | 预热期抑制、正则命中、冷却、配额、聊天流解析失败全部有日志（原先在关键分支上静默） |
| 冷却与配额抑制改用 info 级 | 原先 debug 级，用户看不到 |

### v1.0.0（2026-09-19）

首个版本：双路信号 + `proactive.trigger` 唤醒 Planner，落盘去重、冷却、配额、管理命令。

---

## 10. 开发

```bash
python check_plugin.py --plugin .   # 结构自检（零依赖）
python tests/smoke_test.py          # 冒烟（需 maibot-plugin-sdk，缺失则 SKIP）
python -m pytest -q tests           # 离线单测
python run_gates.py --plugin .      # 交付门禁
```

测试覆盖：入群提示正则（含顺序敏感用例）、载荷多路径兜底、**真机 notice 载荷回归**
（list 形式的 `raw_message`、`is_notify`、`napcat-notice-` 前缀）、
去重/冷却/配额/预热、状态落盘与损坏容错、管理命令与鉴权前缀、组件注册落点、manifest 能力反查。
