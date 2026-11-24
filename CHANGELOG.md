# 更新日志

本文件记录 `org.orge-8.group-welcome` 的行为变更。
README 只保留「怎么工作 / 安装 / 配置 / 命令 / 真机结论 / 故障排查」等使用文档；
**版本细节一律记在这里** —— 新版本先改这里，再改 `_manifest.json` 的 `version`。

## 版本速查

| 版本 | 日期 | 主题 |
|---|---|---|
| v1.5.0 | 2026-11-21 | 适配 snowluma-adapter（napcat 已并入）：qq-notice- 前缀 + 新渲染格式正则 |
| v1.4.0 | 2026-10-05 | 新增只读跨插件 API `get_recent_newcomers` |
| v1.3.0 | 2026-09-23 | 新增 Planner 注入：欢迎当轮强制引导「不引用」 |
| v1.2.3 | 2026-09-23 | 承认 `set_quote` 无法强制，改为「引导 + 兜底」双保险 |
| v1.2.2 | 2026-09-23 | 改用「关闭引用显示」为主策略，不再押注通知编号 |
| v1.2.1 | 2026-09-23 | 指定引用锚点，避免欢迎语"挂"在无关消息上 |
| v1.2.0 | 2026-09-19 | 补查昵称；并据 adapter 源码确认 `operator_id` 属设计 |
| v1.1.3 | 2026-09-19 | 提示 Planner「历史里的入群通知文本不可信」 |
| v1.1.2 | 2026-09-19 | 修正唤醒措辞：约束必须留出可行路径 |
| v1.1.1 | 2026-09-19 | 上线前全检修复 |
| v1.1.0 | 2026-09-19 | 改用原始 notice 载荷，身份判定由「猜」变「读」 |
| v1.0.6 | 2026-09-19 | 推翻初判：通知身份被标错，新增「已知成员过滤」 |
| v1.0.5 | 2026-09-19 | notice 载荷回填 QQ 号 |
| v1.0.4 | 2026-09-19 | v1.0.3 真机复盘的四处修正 |
| v1.0.3 | 2026-09-19 | 未命中通知的可观测性 |
| v1.0.2 | 2026-09-19 | 唤醒措辞修正 |
| v1.0.1 | 2026-09-19 | 真机日志驱动修复 |

---

## v1.0.1（2026-09-19）— 真机日志驱动修复


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

## v1.0.2（2026-09-19）— 唤醒措辞修正


v1.0.1 部署后真机日志显示：链路已通、欢迎语也生成了，但 Planner 挑了一条**历史消息**
去调 `reply`，使欢迎变成"引用回复"，并触发 replyer 的重复回复驳回路径
（`<reject>` 文本一度被当作消息发出，随后被 repeater-recall 撤回）。

| 修复 | 说明 |
|---|---|
| intent 显式声明「事件通知 ≠ 待回复消息」 | 并明确要求「不要引用或回复任何历史消息」，引导 Planner 直接生成新发言 |

新增回归断言 `test_intent_declares_event_and_forbids_history_reply` 锁死该措辞。

## v1.0.3（2026-09-19）— 未命中通知的可观测性


真机事故：**同日同群，「示例用户」入群被正确识别，而「newbie」入群时插件毫无反应。**
排查时发现无从下手——未识别为入群提示的系统通知只记 `debug` 级，
真机日志里根本看不到收到了什么，因此无法区分「适配器没转发」与「正则没匹配」。

| 改进 | 说明 |
|---|---|
| 未命中的系统通知改记 **info** 级 | 输出 `mid` 与通知原文，日志里直接可见 |
| 新增内存诊断缓存（最近 20 条） | 配合 `/欢迎 diag` 可在群内直接查看，无需翻日志 |
| 新增 `/欢迎 diag` 命令 + 中文动作别名 | `状态` `开` `关` `重置` `测试` `诊断` |
| 新增配置 `log_unmatched_notices` | 通知过于频繁时可关闭日志输出（缓存仍保留） |

新增 7 条测试覆盖缓存上限、诊断文本、命令动作、中文别名与命令正则。

## v1.0.4（2026-09-19）— v1.0.3 真机复盘的四处修正


部署 v1.0.3 后实测日志暴露出的问题：

| 问题 | 说明 | 修复 |
|---|---|---|
| **诊断缓存漏记命中项** | 原设计只缓存"未命中"的通知，于是「刚有人入群」时 `/欢迎 diag` 反而显示「暂无记录」——最该有数据的场景看不见东西 | 命中与未命中**都入缓存**，并用 `[已识别入群]` / `[未识别为入群]` 标注 |
| **命令消息被当成新人候选** | 发 `/欢迎 diag` 时，因该 QQ 不在 seen 档案中，被「首次发言」路径判定为新人——恰在预热期才只登记未欢迎，否则**发一条管理命令就会触发一次欢迎** | 新增 `_is_command_message`，优先用载荷的 `is_command`，缺失时按 `/` `／` 文本形态兜底 |
| 命中日志缺少载荷 user_id | 无法确认适配器把 notice 的 `user_info` 填成了谁 | 命中日志增加 `载荷user_id=`，用于判断能否从"仅昵称"升级到"昵称 + QQ 号" |
| — | — | 新增 5 条测试覆盖命令隔离与缓存命中 |

## v1.0.5（2026-09-19）— notice 载荷回填 QQ 号


真机实测确认入群通知载荷的 `user_info.user_id` 是**入群者本人**
（`载荷user_id='1000000001'` = 示例用户），不是系统号。据此：

| 改进 | 说明 |
|---|---|
| 路径 A 的 intent 带上 QQ 号 | 由「仅有昵称」升级为「昵称（QQ xxx）」，便于 Planner 做身份关联 |
| 路径 A 登记入群者档案 | 该成员入群后第一次发言不会被「首次发言」路径重复欢迎 |
| 新增 `_looks_like_user_id` 兜底 | 载荷异常（非纯数字 / 长度不合理）时退回「仅昵称」，不污染档案 |

新增 4 条测试覆盖 QQ 校验、intent 携带、档案登记与异常回退。

## v1.0.6（2026-09-19）— 推翻初判：通知身份被标错，新增「已知成员过滤」


用户提供两个关键事实（`1000000002` = newbie 的 QQ、**示例用户从未退群**），推翻了 v1.0.5 的结论：

- ❌ 旧结论：入群通知没进 hook 链（适配器未转发）
- ✅ 新结论：**通知一直进来了，但发送者被填成了 `operator_id`（操作者示例用户）**，
  真正的入群者 newbie 在文本与 `user_id` 里都看不到

| 改进 | 说明 |
|---|---|
| 新增 `skip_known_member_notice`（默认开） | 入群通知的 user_id 若已是档案中的老成员 → 判定为误标事件，跳过欢迎 |
| 诊断标记新增第三态 | `/欢迎 diag` 输出 `[疑似误标·已跳过]` |
| 修正 v1.0.5 的错误注释 | 明确该 user_id 可能是操作者，不能无条件当作入群者 |

新增 4 条测试覆盖：已知成员被跳过、陌生成员不受影响、防御可关闭、诊断第三态。

## v1.1.0（2026-09-19）— 改用原始 notice 载荷，身份判定由「猜」变「读」


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
直接复刻真机场景：文本写着「示例用户」（操作者），而 intent 里必须出现 newbie 的 QQ。

## v1.1.1（2026-09-19）— 上线前全检修复


按 `maibot-plugin-audit` 三阶段流程全检，发现并修复 2 处缺陷，新增 30 条审计用例。

| 缺陷 | 现象 | 修复 |
|---|---|---|
| **权威载荷缺 `user_id` 时降级到文本路径**（中危） | 载荷是 `group_increase` 但 `payload.user_id` 为空时，改用文本 + `user_info.user_id` 触发 → 欢迎的是**操作者**（群主），不是入群者 | 改为 **fail-closed**：拿不到事件主体时记 warning 并跳过，**不降级** |
| **后台任务异常静默丢失**（低危） | `_spawn` 只做 discard，不取 `task.exception()`，唤醒失败时日志一片空白 | `_on_done` 检查 `cancelled()` / `exception()`，异常以 ERROR 记录 |

**安全面结论**：插件不发起网络请求、不持有凭据，故 skill 高危清单中的
凭据外发 / SSRF / 反序列化三类**整体不适用**（零依赖设计的直接收益）。

新增 `tests/test_audit.py`：安全（AST 判定）/ 注入面 / 副作用守卫 / 边界输入 /
性能（事件循环不阻塞）/ 生命周期 六组，已固化进门禁 pytest 步骤。

## v1.1.2（2026-09-19）— 修正唤醒措辞：约束必须留出可行路径


真机反馈（19:14）：权威路径识别完全正确（新人 `1000000005`），但 Planner **连续两次 reply 失败**：

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

## v1.1.3（2026-09-19）— 提示 Planner「历史里的入群通知文本不可信」


真机反馈（19:39）：插件自己的识别是对的（`1000000005`），**但 Planner 仍被带偏**。
它的 `reply_reference` 原文：

> …但那时示例用户尚未正式入群。**现在示例用户正式加入群聊**，MaiBot可以简短回应一下。

Planner 同时看到两条互相矛盾的信息 —— intent 说新人是 `1000000005`，
而聊天历史里有条 `示例用户 加入了群聊` —— **它信了历史消息那条**。

| 修复 | 说明 |
|---|---|
| intent 增加信息源优先级声明 | 「群内聊天记录里那条「…加入了群聊」的通知可能把入群者标成了邀请人，**那一条不可信**；请一律以本通知给出的 QQ 号为准」 |

**教训**：光把正确的数据交给模型不够，**当环境里同时存在错误的同源信息时，
必须显式声明哪个为准**，否则模型可能选中错的那条。

## v1.2.0（2026-09-19）— 补查昵称；并据 adapter 源码确认 `operator_id` 属设计


**问题**：权威载荷只含 QQ 号不含昵称，intent 写成「新成员入群：1000000005（QQ 1000000005）」
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

## v1.2.1（2026-09-23）— 指定引用锚点，避免欢迎语"挂"在无关消息上


真机现象（群里的实际画面）：

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

## v1.2.2（2026-09-23）— 改用「关闭引用显示」为主策略，不再押注通知编号


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

## v1.2.3（2026-09-23）— 承认 `set_quote` 无法强制，改为「引导 + 兜底」双保险


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

## v1.3.0（2026-09-23）— 新增 Planner 注入：欢迎当轮强制引导「不引用」


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

## v1.4.0（2026-10-05）— 新增只读跨插件 API `get_recent_newcomers`


- `@API(version="1", public=True)`：把 `_seen` 档案里落在时间窗内的成员首次出现记录
  只读暴露给其他插件（首個消费方：life-frequency 的"外面的世界"事件）。
- 纯筛选既有时间戳：零网络、零写盘、不改任何既有行为；坏/空档案降级为
  `groups={}` + `reason`，不抛异常。
- `sdk.min_version` 维持 `2.5.0`（`@API` 自 SDK 2.1.0 起可用，已在 2.5.0 上实测通过）。
- 声明边界：API 只含 QQ 号 + 时间戳，**不含昵称**（昵称从未落盘，见 §4.5）。
- 本版门禁实测（2026-10-05）：devkit `run_gates` **PASS 3 / SKIP 0 / FAIL 0**
  （`check_plugin` PASS 38 / WARN 0 / FAIL 0、冒烟 OK、pytest 137 passed）；
  插件中心自查器 `check_submission.py` PASS 20 / FAIL 0 / WARN 1（`https://` 误报）。

## v1.5.0（2026-11-21）— 适配 snowluma-adapter（napcat-adapter 已并入）

**背景**：官方 napcat-adapter 已合并进 snowluma-adapter（Host 1.3.0 起；旧
napcat-adapter v1.4.1 host max 1.3.99，在 Host ≥1.4.0 不可加载）。本版按
**MaiBot-SnowLuma-Adapter v1.3.2**（2026-11-20 Release）源码逐文件核实契约后适配。

### 源码核实结论（改了什么、没改什么）

| 契约点 | v1.3.2 源码依据 | 结论 |
|---|---|---|
| `additional_config.napcat_notice_type / _sub_type / _payload` | `codecs/notice/message_codec.py:63-67` | **键名保留**，权威路径 1 零改动 |
| 通知 `message_id` | `message_codec.py:93`：`f"qq-notice-{uuid4().hex}"` | 前缀 `napcat-notice-` → **`qq-notice-`**，旧前缀表兜不住 |
| 入群文本 | `renderer.py:174-186`：`[事件-群成员增加] {昵称} 加入了群聊` / `… 被邀请入群` | 「被邀请入群」是旧 4 条正则的**盲区** |
| 发送者映射 | `helpers.py:69-83`：仍 `operator_id` 优先（新增 `is_natural_lift` / `"0"` 归空分支） | 操作者陷阱依旧，已知成员过滤等防御全部保留 |
| `user_info` 结构 | `enricher.py:58-92`：`user_id / user_nickname / user_cardname` | `_extract` 的键序已覆盖，零改动 |
| 昵称 API | `apis/group.py:173`、`apis/account.py:64`：同名保留、public=True | `adapter.napcat.*` 调用不变（另有 `adapter.snowluma.*` / `adapter.onebot.*` 等价别名，未采用） |
| `session_id` | `message_codec.py:104`：恒空串 | 群号反查兜底路径继续生效 |

### 代码变更

| 变更 | 说明 |
|---|---|
| `NOTICE_ID_PREFIXES` 新增 `qq-notice-` | 排在首位；`napcat-notice-` / `notice-` / `system-notice-` 保留兼容历史载荷 |
| `DEFAULT_NOTICE_PATTERNS` 4 → 5 条 | 新增首条锚定 `\[事件-群成员增加\]\s+(?P<nick>\S+)\s+(?:加入了群聊\|被邀请入群)`；第 3 条「被…邀请」尾部扩为 `(?:.{0,8}?加入\|入群)` 兼容旧式修饰 |
| `SUPPORTED_CONFIG_VERSION` 1.3.0 → **1.5.0** | 顺带修复与 manifest 的版本错位（原落后两版）；本版无新增配置键 |
| 文案/注释同步 | plugin.py / README 中 napcat-adapter 表述更新为合并后事实；历史实测记录保留并标注时代 |

### 行为影响与部署注意

- **权威路径（路径 1）行为不变**：键名未改，真实入群者判定逻辑零改动。
- **文本兜底（路径 2）增强**：SnowLuma 新渲染格式两种措辞均可命中；
  非入群事件文本（退群/踢人/改名/撤回）经用例验证不误判。
- ⚠️ **存量实例的 `notice_patterns` 不自动升级**：宿主按 config_version 只补缺键、
  不改已有值。已部署实例要在 WebUI 清空 `notice_patterns` 回落内置默认（或手动补新形态）。
  权威路径不依赖正则，不受影响。
- manifest 的 host/sdk 区间占位（`1.99.99` / `2.99.99`）**本次未动**（用户裁决另行处理）。

### 测试

新增 6 条 SnowLuma 契约回归（`tests/test_group_welcome.py` 末段，载荷按 v1.3.2
真实出站结构重建）：`qq-notice-` 前缀识别（is_notify 缺失时）、新格式两种措辞取昵称、
5 类非入群事件文本拒判、权威路径端到端（intent 用真实新人排除操作者）、
「被邀请入群」文本兜底端到端、bot 自身入群排除。
