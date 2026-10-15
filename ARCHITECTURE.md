---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '27ed8f56-21d5-4691-991c-4e1bafd48e87'
  PropagateID: '27ed8f56-21d5-4691-991c-4e1bafd48e87'
  ReservedCode1: 'ea7da04c-5768-4769-8b5e-18ea3884cdd7'
  ReservedCode2: 'ea7da04c-5768-4769-8b5e-18ea3884cdd7'
---

# 架构设计

## 设计目标

1. **可复用**：引擎与具体游戏解耦，通过世界快照/事件/动作三个接口对接任意游戏端
2. **可靠**：LLM 输出永远不直接生效，必须通过确定性校验层
3. **可测试**：核心零依赖、Mock LLM 可注入，全链路可离线回归
4. **可演进**：每一层都有明确的替换点（记忆检索、LLM Provider、动作类型）

## 分层详解

### 1. 感知/世界层（`engine/world.py`）

- `World`：维护游戏时间（tick → game_minute）、天气、地点、实体（玩家/NPC）与物品
- `EventBus`：发布/订阅 + 事件历史环形缓冲。事件不可变（`frozen dataclass`），便于回放与测试
- `World.snapshot(npc)`：生成"某个 NPC 眼中"的局部世界视图，只喂给他位置可见的信息——这是后续做视野/ stealth 玩法的挂载点

关键事件类型：`player_entered` / `player_spoke` / `item_given` / `npc_action` / `weather_changed` / `time_passed` / `env_event`（G3 世界活性：事件槽满时自动触发）/ `appearance_change`（G5 外观变化）

- **EventSlot**（G3）：世界随机事件槽，每次交互/时间推进累积 +5%，满 100% 触发一个环境事件并归零。事件体现"NPC 有自己生活"（如铁匠陈想起该去收矿石了），通过 `_publish_env_event()` 确定性选择（基于 trigger_count 取模）并发布 `env_event` 类型事件，同地点 NPC 感知并写入短期记忆
- **Entity.appearance**（G5）：实体可变外观（`outfit` 穿着 / `posture` 姿势 / `expression` 神情），`World.set_appearance()` 按键合并更新并发布 `appearance_change` 事件；快照 `nearby` 携带各实体外观副本。设计语义是"眼睛不是记忆"：同地点可见、异地不可见（既有感知过滤天然实现），importance 0.3 不直写长期记忆
- **Location.category**（G6/T4-a）：地点分类（`shop` 店铺 / `public` 公共空间 / `residence` 居所 / `work` 作业场所——T4-a 新类别：农田/河畔/牧场/磨坊/木匠铺），支撑村庄可探索结构与 25 NPC 工作地映射。`World` 默认从 `configs/locations.json` 加载地点清单（文件缺失/损坏/空列表回退内置默认 33 地点：5 店铺 + 4 公共 + 19 居所 + 5 作业场所），测试可显式传 `locations=` 覆盖。`World.snapshot` 仍是局部感知（仅同地点实体），`NPCEngine.status` 的 `world.locations` 遍历全村庄用于总览展示

### 2. 记忆层（`engine/memory.py`）

```
WorldEvent（重要度 ≥ 0.7 的高价值事件）
        │  observe() 双写：短期记忆 + 直接沉淀长期记忆（保留明细）
        ▼
ShortTermMemory (deque, 容量 30)
        │  溢出时 consolidate()
        ▼
LongTermMemory (JSON 持久化, 检索评分)
```

- **记忆条目** `MemoryRecord`：内容 + tick + 重要度(0~1) + 标签 + 类型
- **事件驱动直写**：`MemorySystem.observe` 中，转写重要度 ≥ `LONG_TERM_IMPORTANCE_THRESHOLD`(0.7) 的事件（知 `item_given`）在写短期的同时直写一条带标签的长期记忆——送礼这类关键互动不依赖有损巩固，NPC 会记得每一次赠予
- **检索评分**：`score = 2×标签重合 + 关键词重合 + 重要度 + 时间衰减`，纯标准库实现；接口已抽象，L2 可整体替换为向量检索
- **巩固（consolidate）**：短期记忆满时，把最旧的一批按行为主题压缩成摘要条目写入长期记忆——这是 NPC"记得你上次来过"的机制基础

### 2.5 内状态层（`engine/inner_state.py`）

```
WorldEvent（事件总线发布）
        │  StateUpdater.on_event()（catch-all 订阅者）
        ▼
规则引擎（按事件 kind + 标签 加成 计算确定性增量）
        │
        ▼
InnerState（8D 正交基底：p_fatigue/p_hunger/p_pain/p_drive + e_P/e_A/e_D + S_stress，值域 [0,1]）
        │
        ├─→ to_discrete_tags()（8D→离散标签，≤4 个）→ 注入决策上下文【此刻内心】
        └─→ decide() 硬约束检查（S_stress>0.8 拒绝 / p_fatigue>0.85 收摊）
```

- **InnerState**：8D 正交心智基底（唯一心智变量系统，无双轨）——4D 生理稳态（疲劳/饥饿/痛感/驱力）+ 3D PAD 心境（愉悦/唤醒/支配）+ 1D 压力负荷，`apply_delta` 自动 clamp 到 [0,1]
- **荀子六情映射器**：`XUNZI_EMOTION_VECTORS`（好/恶/喜/怒/哀/乐 → PAD 增量矢量），是"情→心境"的确定性通道，替代逐参数硬编码
- **亲缘度迁移**：trust 不在心智基底中，送礼改写 `RelationshipNetwork.update_affinity`（NPC↔player 亲缘度），NPC 初始化自动注入 player 边（客人/0.5）
- **StateUpdater**：作为 EventBus 的第二个 catch-all 订阅者（第一个是 NPC._on_event 写记忆），按确定性规则更新基底：
  - `item_given` → 六情"好"矢量（e_P+0.05/e_A+0.03/e_D+0.02）+ S_stress-0.05 + 亲缘度+0.1（守财标签额外加成亲缘度）
  - `player_spoke` → 基础 e_A+0.05/S_stress+0.05；赞美→六情"喜"、批评→六情"怒"（较为自负标签放大 e_A/e_P）
  - `npc_action` → p_fatigue+0.02, e_A-0.01
  - `time_passed` → SLEEPING 时 p_fatigue-0.05/S_stress-0.02；否则 p_fatigue+0.01/p_hunger+0.01
- **标签系统**：Persona.tags（Dict[str, float]），如 {"较为自负": 0.7}。标签在 StateUpdater 中可推断地影响参数增量幅度，并在 system prompt 中注入供 LLM 柔性调整说话风格
- **硬约束**：decide() 在 LLM 调用前检查 S_stress/p_fatigue 阈值——压力负荷过高自动 REFUSE（不接单），疲劳过高自动 REFUSE（提前收摊），与 SLEEPING 硬规则同级

### 2.7 关系网络层（`engine/relationships.py`）

```
RelationshipNetwork（存储 List[Relationship]）
    │  query(source_id) / query_by_relation / get_relation_to
    │  to_prompt_text(source_id)
    ▼
注入 DecisionEngine._system_prompt() 的【人际关系】块
    │
    ▼
核心 NPC 决策时感知社会关系（如"你的父亲是老张（好感 0.9）"）
```

- **Relationship**：有向社会关系（source → target），含关系类型（父亲/熟人/宿敌）与好感值（-1.0~1.0）
- **RelationshipNetwork**：存储与查询关系，`to_prompt_text()` 生成注入 LLM 决策上下文的关系描述（好感 >0.3 显示"好感"，< -0.3 显示"敌意"，按 |affinity| 稳定排序保底桶优先取 top-3），无关系时返回空串不注入
- **配置驱动**：`configs/relationships.json`（JSON 数组），NPCEngine 初始化时自动加载
- **T4-b 村庄社会图（2026-10-12）**：配置扩容至 44 条覆盖 25 NPC 全员出边——四层结构（血缘赵家四口+陈氏父子/师徒两组/邻里/职业往来），枢纽长尾（酒馆老板/大夫/守卫各 3 出边 > 全村出边中位数），孤寡/学童/学徒恰 1 条，敌意对（磨坊主↔渔夫冤家，affinity<0，T6 把柄玩法埋点）；top-3 截断护栏随密度补齐首次真实激活（chen 运行时 4 出边截断、敌意边凭 |affinity| 竞争进 top-3），密度带/结构断言见 `tests/test_village_social_graph.py`

### 2.8 背景 NPC 层（`engine/background_npc.py`）

```
WorldEvent（事件总线发布）
    │  BackgroundNPC._on_event()（catch-all 订阅者）
    ▼
规则反应引擎：事件类型过滤 + 位置过滤 + 确定性模板轮转
    │
    ▼
发布 npc_action 事件回流总线（actor 为背景 NPC id）
    │
    ▼
同地点核心 NPC 感知 → 写入短期记忆

EnvMind 4D 行为消费数据流（T4-b 第二小步，2026-10-12）：
    village_genesis.env_mind_for(npc_id) 引擎构造时注入确定性初值
        → BackgroundNPC.apply_schedule(hour) 作息段内每小时演化一步
          （状态延续：无表项小时沿用 _current_state）
        → _on_event 发布前 _variant_suffix() 按状态带拼接变体后缀

敌意边行为出口数据流（T4-c，2026-10-13）：
    RelationshipNetwork 引擎构造时注入（第四参，None 向后兼容）
        → _on_event 文案层 _hostile_prefix(actor) 查敌意边
          （self→actor 且 affinity<0，actor 为玩家/空不触发）
        → 命中 → 反应文案前拼确定性轮转敌意前缀（冷哼一声/
          把脸别过去/头也不抬，与模板轮换同构取模）
        → 优先级：敌意前缀（社会层）> EnvMind 变体后缀（生理层），
          同时命中至多取敌意一条（文案克制纪律）
        → 冤家关系（渔夫↔磨坊主 -0.55/-0.6 双向）玩家可感知，
          零 LLM 零 prompt，T6 把柄勒索的反应层基础设施
```

- **BackgroundNPC**：轻量级 NPC，不持有 LLM 引用（架构上不可能调用 LLM），不维护记忆/完整内状态（InnerState 8D）/状态机；T4-b 起可选持有 4D 压缩稳态 EnvMind（非 InnerState 8D，env_mind=None 时行为与旧版完全一致）
- **配置驱动**：`configs/background_npcs/*.json`（id/name/role/location_id/summary/reactions/inventory/residence/schedule），NPCEngine 全默认构造时自动加载；`residence` 为居所地点 id（缺省回退初始位置），`schedule` 为作息表（G6-B 已实现作息驱动：`apply_schedule` 按 tick 时间在工作地与居所间移动 NPC，WORKING→工作地、SLEEPING→居所、IDLE→保持原位）
- **规则反应**：对配置了模板的事件类型产生确定性反应——`env_event` 只反应同地点、`entity_moved` 只反应有人来到自己地点，模板按计数器取模轮转，`{npc_name}` 替换为 NPC 名；**反应同 hour 判重（2026-10-13，第十二次审查落地）**：同一小时内对同一事件类型至多发布一条 npc_action（判重键 `(hour, event.kind)`，位置过滤通过才计配额、跨 hour 自动恢复），与 EnvMind 演化层 `_last_evolved_hour` 同 hour 口径——消灭"farmer_zhao 同小时对同类事件三连复读"的复读机风险，NPC 反应符合"活人不会一小时反复念叨同一件事"的叙事
- **EnvMind 4D 行为消费（T4-b 第二小步，2026-10-12）**：`ENV_MIND_VARIANTS`（字段名/阈值 0.65/纯中文后缀，生理疲劳>饥饿优先于压力与情绪，至多一条）+ `MIND_EVOLUTION_RATES`（WORKING 劳作累积/IDLE 歇脚恢复/SLEEPING 睡眠归平，同 hour 多 tick 靠 hour 变化判重只演化一步，frozen dataclass 经 `dataclasses.replace` 整体替换、clamp [0,1]；**T4 收官修正 2026-10-14**：e_P 维为 `None` 哨兵——不走单调步长，改走 `E_P_EQUILIBRIUM_TARGETS` 段内均衡收敛（WORKING 劳作平淡 0.45/IDLE 闲适带笑 0.68/SLEEPING 归平 0.50，`E_P_CONVERGENCE`=0.5 每小时收敛一半差距；运转期分布对账发现原 SLEEPING -0.03/h 单调下移使多日运转全村情绪向 0 塌陷——17/22 低于生成带，修正后 1/22 且三日连转分布逐日恒定（周期稳态），运转期对账断言见 `tests/test_runtime_distribution_audit.py`））；`to_dict()` 末尾附 `env_mind` 快照键（None 时为 None，status 观测出口）。**封印语义精确化**：Tier 0/1 封印 = 不进任何 LLM prompt、不调任何 LLM——EnvMind 数值与英文字段名（p_fatigue 等）永不进 prompt，行为出口为纯中文文案变体拼接，只出现在 npc_action 事件文本中（核心 NPC 感知的是带变体的文案而非 4D 数值）
- **零 LLM 断言**：MockLLMProvider 记录 `call_count`/`call_log` 与 `total_tokens_used`，测试可断言背景 NPC 的 id 从未出现在调用日志中、且 `total_tokens_used == 0`（以 token 数非调用次数证明零 LLM）
- **敌意边行为出口（T4-c，2026-10-13）**：`BackgroundNPC` 第四参可选注入 `RelationshipNetwork` 引用（None 时行为与旧版逐字一致——向后兼容）；反应文案层消费敌意关系边：事件 actor 命中敌意边（`get_relation_to(self.id, actor)` 存在且 affinity<0）时文案前拼 `HOSTILE_PREFIXES` 确定性轮转前缀，社会层优先于 EnvMind 生理层后缀（同时命中至多一条）；文案层位于判重之后、发布之前，不改变判重行为；零 LLM 零 prompt（Tier 0/1 封印不破），验收见 `tests/test_hostile_edge_behavior.py`（8 用例：敌意分叉/双向敌意对/零 LLM/None 向后兼容/判重兼容/敌意优先于 EnvMind/亲缘边不触发/默认村庄端到端）

### 3. 决策层（`engine/decision.py`）

**混合决策**是本项目最重要的设计决策：

| 组件 | 职责 | 为什么 |
|---|---|---|
| `StateMachine` | 状态迁移硬约束（IDLE/WORKING/TALKING/SLEEPING） | 确定性、可测试、防 LLM 越权 |
| `InnerState` | 内状态硬约束（S_stress>0.8 拒绝、p_fatigue>0.85 收摊） | 8D 心智基底驱动行为边界 |
| `FillerEngine`（`engine/filler.py`） | 慢脑 LLM 调用前同步生成反应性垫话占位：脾气掩码 × 8D 当下状态双驱动（S_stress>0.6 带烦躁语气、e_P>0.7 带愉悦语气），每模板 ≤15 token | 玩家等待 LLM 期间即感知到"NPC 的第一反应"（0-token 本地计算，不进 LLM prompt） |
| `DecisionEngine` | 组装上下文（含内状态）→ 调 LLM → 解析动作 | 柔性、个性化、自然语言 |

决策流程：

1. 硬规则前置：当前状态是否允许对话？（SLEEPING → 直接产出"睡梦中嘟囔"的回退动作）
2. 内状态硬约束：压力负荷 S_stress > 0.8 → 拒绝接单；疲劳 p_fatigue > 0.85 → 提前收摊
3. 上下文组装：人格卡 + 性格标签 + **人际关系** + 相关长期记忆 + 短期记忆（**`RECENT_CONTEXT_WINDOW=4`** 上下文裁剪，削减 prompt token）+ 世界快照（含**【周围的人】**同地点实体及外观，G5）+ **此刻内心状态（8D 状态带离散标签，≤4 个高显著度中文词，无浮点）** + 玩家输入 + 输出 JSON 契约
4. LLM 生成结构化动作
5. 校验失败/解析异常 → 人格自带的 `fallback_bank` 安全回退

### 4. 行动层（`engine/actions.py`）

- 动作白名单：`speak / move / give_item / emote / refuse`
- `ActionValidator`：状态机一致性（睡觉不能聊天）、世界一致性（没铁矿石不能送武器）
- `ActionExecutor`：执行后产出新 `WorldEvent` 回流事件总线 → 其他 NPC 也能感知到（多智能体的地基）

### 5. LLM Provider（`engine/llm/`）

- `BaseLLMProvider.chat(messages) -> str`：唯一抽象；`last_usage`（prompt/completion/total_tokens）与 `total_tokens_used` 为 token 度量默认值，子类在 chat 中更新
- `OpenAICompatProvider`：urllib 实现，兼容任意 OpenAI 协议端点（含本地模型），环境变量 `NPC_LLM_BASE_URL / NPC_LLM_API_KEY / NPC_LLM_MODEL`；从响应 `body["usage"]` 读取真实 token 用量
- `MockLLMProvider`：基于人格对话库 + 关键词规则的确定性实现，用于离线开发与测试；按字符数估算 token（`prompt=len(system)+len(user)`、`completion=len(response)`），话题匹配仅扫描【玩家说】段避免地点名/外观文本误触发

### 6. 编排层（`engine/engine.py` + `engine/npc.py`）

- `Persona`：JSON 配置 → 人格对象（性格、背景、语气、对话库、作息表、**标签**、**外观**、**居所**、**脾气掩码 temperament**——垫话引擎的掩码标识，T2 落地 8 掩码后由标签库生成）
- `NPC`：人格 + 记忆系统 + 状态机 + 决策引擎 + **内状态** 的聚合根，订阅事件总线（记忆写入 + 状态更新双订阅）
- `NPCEngine`：世界 + NPC 集合的编排入口，暴露 `player_says / player_gives / player_changes_appearance / tick / status` 五个核心 API（`player_gives`：玩家送礼，库存转移或宽松发布事件，NPC 经事件链路记入短期+长期记忆；`player_changes_appearance`：玩家换装，同地点 NPC 感知；`tick`：推进世界时间并对核心+背景 NPC 应用作息驱动位置移动）。全默认构造时自动扫描 `configs/`（npcs + background_npcs + village_roster + relationships + locations），加载小村庄：3 核心 NPC（chen/lily/tavern_sun 跑完整决策链，T4-b 收尾段 10-13 升级酒馆掌柜为第 3 核心）+ 22 背景 NPC（7 既有 + 15 新住民，零 LLM 规则反应，T4-a 正式接入运转）+ 33 地点（四类可探索结构，含 work 作业场所新类别）。`player_says` 后按 NPC 聚合 token 统计（`token_stats`），`status()` 返回 `token_stats` 字段（`by_npc` 按 NPC 聚合 prompt/completion/total/calls，`total_tokens` 为 LLM 累计，`llm_calls` 为调用次数）

## 数据流（一次对话）

```
玩家输入 "能帮我打一把剑吗？"
  → NPCEngine.player_says()
  → World 发布 player_spoke 事件
      → NPC._on_event 写入短期记忆
      → StateUpdater.on_event 更新内状态（e_A+0.05, S_stress+0.05）
  → FillerEngine.generate(反应性 8D 状态) → 垫话占位（慢脑前同步返回，0-token）
  → FastBrain.respond()（T3 快脑判定，0-token）："能帮我打一把剑吗？"
      未命中 5 意图规则 → 落回慢脑（若命中如"铁匠铺怎么走"则直接产出
      SPEAK Action 秒回，跳过下方整个慢脑链，返回 brain="fast"）
  → NPC.handle_player_input()
      → StateMachine 检查：WORKING 状态允许 TALKING ✓
      → InnerState 硬约束检查：S_stress ≤ 0.8 且 p_fatigue ≤ 0.85 ✓
      → MemorySystem.context_for("打剑") 检索相关长期记忆
      → DecisionEngine：组装 prompt（含【此刻内心】状态行）→ LLM → {"action":"speak","text":"..."}
      → ActionValidator：白名单 + 一致性校验 ✓
  → ActionExecutor 执行 → 世界事件 npc_action → StateUpdater 更新内状态（p_fatigue+0.02）
  → 记忆层沉淀本次交互 → 短期记忆超限则触发巩固
  → 事件槽累积 +5%（G3：player_says 末尾调用 accumulate_event_slot）
```

## 数据流（世界自发事件，G3）

```
EventSlot 累积满 100%（tick 或 player_says 推进）
  → World._publish_env_event() 确定性选择环境事件
  → EventBus 发布 env_event 事件（如 actor=chen, summary="铁匠陈想起该去收矿石了"）
  → NPC._on_event 命中：actor==自身 或 同地点 → MemorySystem.observe
      → importance 0.5 < 0.7 阈值 → 仅写短期记忆（环境事件不直写长期）
  → NPC 短期记忆中新增环境事件记忆 → 下次对话时进入【最近的经历】上下文
```

## 数据流（作息驱动位置移动，G6-B）

```
NPCEngine.tick(minutes)
  → World.tick() 推进 game_minute，发布 time_passed 事件
  → 核心 NPC：NPC.apply_schedule(hour)
      → Persona.schedule_state(hour) 查作息表
      → 状态变化 → StateMachine.force(state) + 移动实体
          → SLEEPING → move_entity(persona.id, persona.residence) → 发布 entity_moved
          → WORKING  → move_entity(persona.id, persona.location_id) → 发布 entity_moved
          → IDLE     → 保持当前位置不动
  → 背景 NPC：BackgroundNPC.apply_schedule(hour)
      → schedule[str(hour)] 查作息表
      → 状态变化 → 移动实体（同核心 NPC 逻辑，但不维护状态机）
      → 仅当目标位置 ≠ 当前位置时才 move_entity（避免无意义事件）
  → entity_moved 事件 → 同地点背景 NPC 规则反应 → 发布 npc_action → 事件回流总线
  → 返回 schedule_applied（含核心 NPC 中文状态名 + 背景 NPC 英文状态名）
```

## 数据流（一次送礼）

```
玩家送铁矿石给铁匠陈
  → NPCEngine.player_gives("chen", "铁矿石")
  → 玩家背包有则 transfer_item（自动发事件）；无则宽松发布 item_given 事件
  → NPC._on_event 命中自己 → MemorySystem.observe
      → importance 0.8 ≥ 阈值 0.7 → 短期记忆 + 长期记忆双写（tags 含 item_given/玩家）
  → 下次对话时 context_for("铁矿石") 命中该长期记忆 → 进入决策上下文
     → "上次你给我的那块铁矿石，打成好钢了"这类回应有了记忆地基
```

## 数据流（背景 NPC 规则反应，G4）

```
环境事件 env_event 发布（如 actor=lily, summary="莉莉盘算新货报价"）
  → BackgroundNPC._on_event 命中：reactions 配置了 env_event 模板
  → 位置过滤：actor lily 在 market，背景 NPC old_zhang 也在 market → 同地点 ✓
  → 确定性选择模板（计数器取模轮转）→ 格式化（{npc_name}→老张, {summary}→莉莉盘算...）
  → 发布 npc_action 事件（actor=old_zhang, summary="老张听见了莉莉盘算新货报价"）
  → 同地点核心 NPC lily 感知该 npc_action → 写入短期记忆
  → 全程零 LLM 调用（BackgroundNPC 不持有 LLM 引用）
```

## 数据流（外观变化与对话引用，G5）

```
玩家换上围裙（NPCEngine.player_changes_appearance({"outfit": "皮围裙"})）
  → World.set_appearance 按键合并 Entity.appearance + 发布 appearance_change 事件
  → NPC._on_event 位置过滤：同地点 NPC observe → "看到 player穿着变为皮围裙" 入短期记忆
      （importance 0.3 < 0.7 不直写长期；异地 NPC 无感知；NPC 自身换装必可闻）
  → 下次对话：World.snapshot 的 nearby 携带玩家外观
      → DecisionEngine._user_prompt 注入【周围的人】"旅行者（穿着皮围裙）"
      → LLM 收到外观描述 → 对话可引用对方穿着（nearby 为空则整块不注入，控 token）
```

## 工程约定

- 类型注解全覆盖，`from __future__ import annotations`
- 核心引擎**仅标准库**（unittest 测试，无需 pytest）
- 所有 LLM 交互必须可降级：任何异常都不能中断游戏循环
- 配置与代码分离：新 NPC = 新增一个 JSON 文件

## v2 世界模型蓝图（docs/ 三规范裁定后的目标架构）

> 依据 `docs/01_WORLD_SPECIFICATION_WHAT.md`、`02_ENGINEERING_IMPLEMENTATION_HOW.md`、`NPC_TAG_DATABASE.md` 三份规范，经共线性去重/平庸泥潭破解/纸面哲学砍除后裁定的目标架构。**T1-T6 阶段逐步落地；T1 已于 2026-10-06 落地（8D 基底+荀子六情映射器+trust 迁移关系网），其余为目标态。**

### 心智层 v2：8D 正交基底（T1，✅ 2026-10-06 落地）

```
Ψ(t) = [ p_fatigue, p_hunger, p_pain, p_drive |  e_P, e_A, e_D |  S_stress ]
         └── 生理稳态 4D [0,1] ──┘  └─ PAD 心境 3D [0,1] ─┘  └ 压力 1D ┘
```

> 当前实现 PAD 心境值域为 [0,1]（与 `engine/inner_state.py` 的 `apply_delta` clamp 一致）；是否放宽为规范 [-1,1] 属架构级决策，已提请项目主人定夺（2026-10-07）。裁定前 T2 脾气掩码 PAD 分区触发条件设计须标注此依赖。

- 事件 → 心智的通路：**荀子六情映射器**（`XUNZI_EMOTION_VECTORS`：好/恶/喜/怒/哀/乐 → PAD 增量矢量，如"怒"→[-0.08,+0.10,+0.08]）挂在 StateUpdater `_apply_xunzi`，替代逐参数硬编码 ✅
- 六情矢量缩放与归正记录：代码矢量为规范 `NPC_TAG_DATABASE.md` 2.3 节约 1/6 幅度的 [0,1] 基底小步进缩放版（单事件不跳满格、多事件叠加逼近）；"怒" e_D 与"乐" e_A 两处方向已于 2026-10-07 按审查裁定归正跟随规范（怒 e_D>0 高支配、乐 e_A<0 低唤醒），契约锁测试见 `tests/test_xunzi_emotion.py::TestXunziSpecContractLock`
- trust 不是心智而是关系 → 迁入 RelationshipNetwork（`update_affinity` 主观亲缘度分量，NPC 初始化注入 player 边）✅

### 标签层 v2：大一统标签数据库（T2）

- **首批已落地（2026-10-07，`engine/tag_genesis.py`）**：6D 先天正态属性生成器（截断正态 [1,10]，`alcohol_tol` σ=1.5 依规范 4.3 节）+ 财富对数正态（按 2.1 节分位语义参数化：P5≈10/P95≈50 铜板）+ 金字塔四层采样（70/20/8/2）+ 齐普夫采样器（s=1.15）+ 尧氏 8 风格×4 阶骨架 + 美貌六档/属性四档离散称号分桶；分布断言与规范契约锁见 `tests/test_tag_genesis.py`（32 用例）；三处规范偏离（σ=1.5/财富参数化/软截断=拒绝采样）书面记录于模块 docstring
- **第二批已落地（2026-10-08，`engine/tag_mount.py`）**：缺陷+把柄强制挂载与四时态骨架——每 NPC 一本 `TagLedger`：非背景 NPC 强制 ≥1 显性缺陷（FLAW_POOL 7 条，金字塔 uncommon 起步重归一化）+ ≥1 绝密把柄（SECRET_POOL 5 条，rare 起步重归一化）；四时态分桶存储（先天/后天/瞬态 TTL 独立计数器/关系羁绊 scope）；互斥锁三组（吝啬⇔慷慨、圣人⇔凶手、倾城⇔可怖）acquire 时自动消除对立标签；`visible_tags()` 为决策层唯一消费入口（排除 secret=True，把柄永不进 prompt）；挂载为显式 API `NPCEngine.mount_tags(npc_id)`（crc32 确定性种子，默认未挂载时行为与既有完全一致）；档位权重 import 引用 tag_genesis.PYRAMID_TIERS（挂载层零分布逻辑，T4 复用零改动）；测试 `tests/test_tag_mount.py` 32 用例；四处规范偏离（互斥消除凌驾 INNATE 移除保护等）书面记录于模块 docstring
- **挂载常驻化已落地（2026-10-09 T2 收口先行批）**：`Persona` 新增 `tag_profile` 字段——configs 声明 `"genesis"` 的核心 NPC（chen/lily）在 `NPCEngine.spawn` 时经共享内核 `_build_tag_ledger`（crc32 确定性种子）自动挂载标签账本，"断指的铁匠陈"成为 NPC 默认状态而非显式 API 调用结果，玩家默认可感知标签人格；`mount_tags` 显式 API 签名与行为完全兼容保留，未声明 `tag_profile` 的 NPC 零挂载（向后兼容），背景 NPC 天然零挂载；token 基线 4737→4769（身份标签行结构性合法增量，仍在 4400-4800 硬标准内）；测试 `tests/test_tag_residency.py` 15 用例
- **脾气掩码双分区表已落地（2026-10-08，`engine/temperament_table.py`）**：林传鼎 8 大显性脾气（安静/喜悦/暴躁/哀戚/惊恐/恭顺/傲慢/惭辱）× PAD 双值域分区触发条件——v01（[0,1]，与 inner_state 现实现一致，默认激活）与 v11（[-1,1]，规范 2.4 版）两套表并行，仿射映射 t_11=2*t_01-1 锁语义等价；`ACTIVE_PAD_VERSION` 一处切换即接线（PAD 值域主人裁定后掩码实施从"从头设计"降为"选表接线"）；测试 `tests/test_temperament_table.py` 33 用例。**待接入**：chen/lily 重配、裁定后 8 掩码接线 filler
- **6D 先天正态属性**：美貌/力量/悟性/勇气/道德/酒量 ~ N(5.0, 1.25²)，财富对数正态——普通人占 70% 的世界基石
- **林传鼎 8 显性脾气掩码**（安静/喜悦/暴躁/哀戚/惊恐/恭顺/傲慢/惭辱）：驱动垫话与微动作（触发条件层已落地双分区表，待 PAD 裁定接线）
- **尧氏 8 风格原型 × 4 阶执念深度**（萌芽 70%/显性 20%/执念 8%/病态 2%）
- **缺陷+把柄强制挂载** ✅：非背景 NPC ≥1 显性缺陷 + ≥1 绝密把柄（勒索/解谜玩法源，T6 埋点）；**挂载常驻化** ✅（2026-10-09：configs 声明 `tag_profile` 的核心 NPC spawn 自动挂载，默认状态玩家可感知）
- **四时态生命周期**（先天印记/后天经历/瞬态 TTL/关系羁绊）+ 互斥锁 ✅
- **分布纪律**：连续属性正态、离散特质金字塔长尾（70/20/8/2）、职业齐普夫

### 对话层 v2：快慢脑分流（T3）

```
玩家输入 ──▶ 快脑分流门（意图规则匹配，<20ms，0 token）
              ├─ 命中（约70%日常：问路/查价/招呼）──▶ 秒回
              └─ 未命中 ──▶ 脾气掩码垫话（0ms 动作抢跑 + 10ms 垫话）──▶ 慢脑（LLM 决策链）
```

- **垫话引擎原型已落地（2026-10-06，主人裁定 17 提前先行）**：`engine/filler.py` 脾气掩码粗粒度 3 掩码（irritable/cheerful/aloof）× 8D 状态带规则，`player_says` 结果含 `filler` 字段；T2 落地林传鼎 8 脾气掩码后替换为标准掩码表
- **快脑 FastBrain 落地（2026-10-09 22:00 原型 + 2026-10-10 presence 扩充，`engine/fast_brain.py`）**：greet/presence/ask_direction/ask_price/farewell/ask_time 六意图规则匹配（presence 为非固化采样数据驱动扩充：「在吗/老板在吗/有人吗/请问有人在吗」类高频封闭句式原先每条白付约 1200 prompt token 的 LLM 调用只为回一个“在”字），命中直接产出 SPEAK Action 0-token 秒回（跳过慢脑 LLM 决策链）；`player_says` 返回 `"brain": "fast"|"slow"` 分流路径字段，引擎 `fast_brain_stats` 接管率统计（`status()` 含 `fast_brain` 字段）；实测双口径：`scripts/fast_brain_takeover.py` 固化 12 条混合对话 8/12=66.7%（验收线 ≥50%）+ `scripts/fast_brain_takeover_open.py` 非固化二次采样 13 条四类分布 8/13=61.5%（GAP 候选 4/4 拦截，审查指令 2 口径）；接线设计与掩码 id 统一方案见下节
- **<35 token 极简 Prompt 组装已落地（2026-10-10）**：`InnerState.to_discrete_tags()` 把 8D 映射为 ≤4 个高显著度离散中文标签（阈值常量 `BAND_*` 全仓单一来源，filler.py 掩码同源引用），按偏离幅度降序取 top4、全常带返回"心境平稳"；【此刻内心】行从 8 参数"名: 值"浮点文本（71 字符≈32 token）改为离散标签，旧 `to_prompt_text()` 按无双轨纪律删除，token 基线 4769→4501（-268，4 次慢脑调用实测）
- 四级算力分流：Tier 0 纯数学物理（<0.1ms）/ Tier 1 规则与哈希（<0.5ms）/ Tier 2 端侧小模型 / Tier 3 云端 LLM（仅深度叙事）——环境 NPC 封印在 Tier 0/1

### 快慢脑分流与掩码 id 统一接线设计（T3，2026-10-09 书面先行）

> 审查指令 2 要求"filler 与 temperament_table 掩码 id 体系统一的接线设计先行书面记录"+"快脑接管率实测"。本节为 T3 快慢脑批的设计裁定记录：快脑分流结构当日原型落地；掩码 id 统一为 PAD 值域裁定后执行的接线预案（书面先行，未实施）。

**1. 快慢脑分流结构**

`player_says` 分流顺序：**感知 → 垫话 → 快脑判定 →（未命中）慢脑**：

```
玩家输入
  → 感知：player_spoke 事件 → 记忆写入 + 8D 状态更新（既有链路不变）
  → 垫话：FillerEngine 反应性开场白（0-token 本地计算，先行返回——
          无论快慢脑命中与否，玩家先拿到反应性占位。此为实施口径，
          与上方 v2 目标态图"未命中才垫话"的先后差异：实施以先行
          返回的即时反馈优先，垫话语义是"听到玩家说话的第一反应"）
  → 快脑判定：FastBrain 意图规则匹配（engine/fast_brain.py，<20ms，0 token）
      ├─ 命中 5 意图（greet/ask_direction/ask_price/farewell/ask_time）
      │    → SPEAK Action 直接执行秒回——跳过慢脑 LLM 决策链，零 LLM 调用
      └─ 未命中 → 慢脑：记忆检索 → 上下文组装 → LLM → Action 校验执行（既有链路）
```

- **FastBrain（快脑）**：6 意图规则匹配——greet（招呼）/presence（确认在场：在吗/老板在吗/有人吗等高频封闭句式）/ask_direction（问路）/ask_price（问价）/farewell（道别）/ask_time（问时）六类高频日常输入，命中即产出 SPEAK Action 0-token 秒回；规则路径为 Tier 1 级（规则与哈希），不进 LLM
- **FillerEngine（垫话）**：既有 `engine/filler.py` 反应性开场白，位于快脑判定之前（分流顺序见上）
- **慢脑**：既有 LLM 决策链——记忆检索 → 上下文组装 → LLM → Action 校验执行
- **接管率统计口径**：引擎属性 `fast_brain_stats`（快脑命中数/对话总数），`status()` 含 `fast_brain` 字段；实测双口径——`scripts/fast_brain_takeover.py` 固化 12 条混合对话（8 条高频日常 + 4 条复杂话题，seed=42 确定性复现）接管率 8/12=66.7%；`scripts/fast_brain_takeover_open.py` 非固化二次采样（13 条四类分布：冒烟复杂 3 + 已覆盖口语 4 + GAP 候选 4 + 复杂寒暄 2）接管率 8/13=61.5%，GAP 候选 4/4 拦截（审查指令 2：固化样本接管率不单独作达标证据）；另 `scripts/tone_continuity.py` 为垫话-回复语气连续性真实 LLM 采样脚本（高压垫话后慢脑不接续样本 ≤20% 验收）

**2. filler 与 temperament_table 掩码 id 体系统一方案（PAD 裁定后接线）**

现状（两套掩码 id 并存的过渡态——触发条件层已规范化、模板层仍为先行版）：

- `engine/filler.py`（垫话模板层）：粗粒度 3 掩码 id（irritable/cheerful/aloof）——主人裁定 17 允许的先行版（2026-10-06）；configs 的 `temperament` 字段当前即用此 id（chen="irritable"、lily="cheerful"）
- `engine/temperament_table.py`（触发条件层）：规范 7.2 节 8 脾气 id（TEMP_SERENE/TEMP_JOYFUL/TEMP_WRATHFUL/TEMP_MELANCHOLIC/TEMP_FEARFUL/TEMP_REVERENT/TEMP_ARROGANT/TEMP_ASHAMED）——双值域分区表已落地（2026-10-08），`ACTIVE_PAD_VERSION` 一处切换（当前 "v01" 即 [0,1] 分区），待主人 PAD 值域裁定激活

统一方案（主人 PAD 裁定后执行，当前书面先行）：

- **id 映射**：filler 与 fast_brain 的掩码 id 迁移到规范 8 脾气 id 体系——irritable→TEMP_WRATHFUL（暴躁）、cheerful→TEMP_JOYFUL（喜悦）、aloof→TEMP_SERENE（安静）；configs 的 `temperament` 字段值随之更新
- **兼容窗口**：旧 id 保留别名兼容一个迁移窗口（掩码表同时接受新旧 key），窗口期后移除别名
- **单一掩码源纪律**（防共线性死穴——同一脾气现象严禁两套变量）：垫话模板与快脑语气变体共用同一掩码表，脾气掩码的定义、状态带、模板文案一处维护
- **触发条件对齐**：掩码激活态（当前 [0,1] 分区）由 temperament_table predicate 判定（8 判定函数阈值全部引用 `active_partition()`，随 `ACTIVE_PAD_VERSION` 切换）；filler/fast_brain 的状态带与 predicate 阈值对齐——核对项：掩码表内不得出现 predicate 阈值之外的第二套阈值数字（正交纪律）
- **接线步骤**（PAD 裁定后按序执行）：
  ① `ACTIVE_PAD_VERSION` 切换激活裁定值域；
  ② configs `temperament` 值换新 id（irritable→TEMP_WRATHFUL 等）；
  ③ filler/fast_brain 掩码表 key 换新 id（别名窗口开启）；
  ④ 回归测试（filler/fast_brain/temperament_table 契约锁 + `scripts/fast_brain_takeover.py` 复跑），全绿后关闭别名窗口

**3. token 结构性下降口径**

- **结构性削减 vs 单点裁剪**：快脑 0-token 秒回是反内卷裁定 16 认可的结构性削减手段——意图命中的对话整条跳过 LLM（prompt+completion 双侧归零），区别于上下文裁剪（`RECENT_CONTEXT_WINDOW` 6→4）这类单点裁剪（只削单次 prompt 体量）
- **接管率验收线**：快脑接管率 ≥50%（`fast_brain_stats.hits / total`）为对抗升级验收线，实测口径见 `scripts/fast_brain_takeover.py`
- **<35 token 极简组装（已落地 2026-10-10）**：慢脑【此刻内心】行从 8 参数浮点文本（"名: 值"×8，71 字符）改为 3-4 个高显著度离散中文标签（`engine/inner_state.py` 的 `to_discrete_tags()` + `BAND_*` 阈值常量单一来源，filler.py 掩码同源）；红线预防测试已固化（`TestPromptTokenRedline`：该行浮点零匹配/标签 ≤4/全常带单标签）

### 世界层 v2：人口学村庄（T4）

- 25 NPC 按 6D 正态属性 + 齐普夫职业分布 + 标签金字塔生成；关键 NPC（8D 全量+把柄）vs 环境 NPC（4D 压缩稳态，零 LLM）
- 行为经济学离散事件化（T5）：EWMA 财富基准、2.5 倍禀赋效应、损失域豪赌；杜希格习惯回路 0-token 截断
- **第一小步已落地（2026-10-11）**：`engine/village_genesis.py` 离线人口学生成器——25 人齐普夫职业阶层花名册（farming 7 / craft 5 / 特殊职业各 ≤2）+ `generate_village()` 确定性人口学档案（crc32(npc_id) 种子）；关键 NPC 2 人（chen/lily，mind=None，8D 全量由运行时 InnerState 承载）vs 环境 NPC 23 人（`EnvMind` 4D 压缩稳态：p_fatigue/p_hunger/e_P/S_stress 字段名与 8D 严格同名——共线性纪律，常人平稳带初值，Tier 0/1 封印不进任何 prompt）；**单一挂载内核纪律**：原 `NPCEngine._build_tag_ledger` 主体提取为 `tag_mount.build_genesis_ledger`（engine 薄壳委托、签名行为零变化），引擎常驻化 / mount_tags 显式 API / village_genesis 三方复用同一函数（chen 缺陷/把柄挂载双源全等有测试锁）；15 位新住民配置落位 `configs/village_roster/`（共居消化居所增速：赵宅 3 口/药童寄宿秦家/帮工寄宿孙家/学童寄宿私塾宅；更夫 20:00-03:00 夜班作息反转、牧人 04:00 早出），生产路径暂不自动加载——待 T4-a 地点扩容（17→33，NEW_LOCATION_IDS 14 个引用清单）后正式接入运转；正态/齐普夫/金字塔三形状分布断言+环境零 LLM 双口径见 `tests/test_village_genesis.py`（40 用例）；生成器为纯离线数据层，token 基线 4501 零影响
- **第二小步（T4-a）已落地（2026-10-11 22:00）**：地点 17→**33** 四类扩容——`work` 作业场所新类别 5 处（pasture 牧场/farm 农田/river 河畔/mill 磨坊/carpentry 木匠铺，修复"农夫在广场、渔夫在井边工作"叙事缺陷：farmer_zhao→farm、fisher_zhou→river）+ public 扩至 4（school 私塾/shrine 祠堂）+ residence 扩至 19（9 新居所共居环带，实际唯一居所 19 与提案预估 21 的偏差源：长工住东家 farmhand_sun→house_zhao、帮工住老板家 tavern_aman→house_sun 的额外共居消化，更真实的村落设定）；**15 新住民正式接入运转**：`NPCEngine` 生产/演示路径自动 `load_newcomer_configs()`（复用 village_genesis 单一来源，共 23 背景 NPC），白天农田/河畔/牧场各有其人、21:00 更夫巡村+赵家四口归宅、03:00 更夫收工回家的昼夜分布图成形；**事件池 46 条 25/25 全员覆盖**（15 新住民每人 2 条职业生活文案，尾部追加保轮播首项不变）；**两项 T4-a token 裁剪**：①【周围的人】nearby 按 viewer 亲缘度 |affinity| 降序稳定排序取前 5（`decision.py NEARBY_PROMPT_LIMIT`）——25 NPC 同地点密度护栏；②【人际关系】top-3 截断+类型权重保底（`relationships.py _PRIORITY_RELATION_TYPES`：父子/师徒不因 |affinity| 低被挤出，低好感父亲依然是父亲）；快脑商品词品类后缀边界收紧（`fast_brain.py _GOODS_SUFFIX_CHARS`：「铁剑鞘」不得提取"铁剑"答非所问）；token 基线 4501→4523（运转噪声合法波动 +22，容忍带内，PINNED 同步）；接入运转验收见 `tests/test_village_integration.py`（12 测试类 30 用例：规模/工作地映射/共居/夜班/全天运转/零 LLM 双口径/事件池覆盖/两项裁剪/商品边界/新地点问路）；架构图已同步重画（village_genesis 创生层+接入箭头入图，视觉 QA 8 项全 PASS）；**T4-b 两小步已落地（2026-10-12）**：第一小步关系网 8→44 全员覆盖（见 2.7 节），第二小步 EnvMind 行为消费出口（见 2.8 节，延续语义下变体带真实可达：基线场景 11/22 NPC 日内抵达 0.65+ 带、全天 6 条 npc_action 带后缀，均未进入对话时刻核心 NPC 最近经历窗口，token 基线 4519 零影响）；**T4-b 收尾段已落地（2026-10-13 22:00）**：tavern_sun 升级第 3 核心 NPC（配置 `configs/npcs/tavern_sun.json`，cheerful 复用掩码+genesis 标签账本常驻化+快脑经营范围米酒/酱肉，记忆/8D 心智/快慢脑/LLM 对话全套；分层 2+23→3+22，token 基线 4519 零变化——其旧背景规则反应事件本就不进核心对话记忆窗口，实测三次严格相等）+ **T4-c 敌意边行为出口**（见 2.8 节：背景反应文案层消费敌意关系边，社会层前缀优先于 EnvMind 生理层后缀，T6 把柄勒索的反应层基础设施）；验收见 `tests/test_tavern_core_npc.py`（9 用例）+ `tests/test_hostile_edge_behavior.py`（8 用例）；余项：分布形状运转期验证

### 行为经济学层 v2：杜希格习惯回路离散截断（T5，第一小步已落地）

> v2 蓝图"行为经济学离散事件化"三件套（EWMA 财富基准 / 禀赋效应 / 损失域豪赌）中的 0-token 基础设施先行批：杜希格《习惯的力量》三段回路（线索→惯例→奖赏）在背景 NPC 层的离散截断实现。`engine/habit.py` `HabitLoop`（2026-10-14 落地），黄昏线索→酒馆聚集，纯规则 Tier 0/1 封印层。

```
社会关系边（configs/relationships.json，44 条既有边，零新增配置）
    │  HabitLoop 构造时遍历一次：与 tavern_sun 任一方向的
    │  "酒友/老主顾"边（PUB_RELATION_TYPES 精确匹配）→ 习惯 id 集合
    ▼
NPCEngine.tick() 背景 NPC apply_schedule 循环之后
    │  HabitLoop.apply(world.hour)，四层闸门全过才行动：
    │  ① 黄昏窗口 hour ∈ DUSK_HOURS={17,18}（杜希格"线索"）
    │  ② 同 hour 判重（至多一条习惯 npc_action，沿 _last_react_hour 模式）
    │  ③ 状态 IDLE（BackgroundNPC.state_str 只读 property；作息未应用
    │     None 保守不行动；WORKING/SLEEPING 打烊时段不扰）
    │  ④ 位置 ≠ tavern（已在地不重复触发）
    ▼
world.move_entity(npc_id, "tavern") + npc_action 确定性中文文案
    （HABIT_TEMPLATES 3 条按触发计数取模轮转，与反应模板轮换同构；
     杜希格"惯例"=移步酒馆，"奖赏"=文案即叙事层"喝一杯"的玩家可感知投影）
    ▼
熄灯交接（非 HabitLoop 职责）：19/20/21 点各 NPC apply_schedule
    SLEEPING 状态变化自然移居所——既有 G6-B 作息移动接管，
    习惯层只"拉人进酒馆"，熄灯交给作息层
```

- **离散截断设计**：线索=时钟离散窗口 `hour∈{17,18}`（黄昏）；惯例=移步酒馆+确定性文案；奖赏=叙事层"喝一杯"（文案本身即奖赏的玩家可感知投影）——**连续奖赏函数/渴望强度曲线按纸面哲学砍除**（三段回路全部可在纯规则层离散表达，无需数值化）。习惯推导口径：社会关系→习惯的数据流（常客黄昏自然要去喝一杯的叙事依据），默认村庄命中恰三人 guard_wang（酒友）/fisher_zhou（老主顾）/doc_qin（老主顾），17-18 点三人恰处 IDLE 段；herder_ma→tavern_sun 的"主顾"为近义类型不命中（精确匹配收紧常客语义）
- **token 成本账**：纯规则 0-token——`HabitLoop` 与 EnvMind 同属 Tier 0/1 封印层（不持有任何 LLM 引用、不组装任何 prompt、习惯状态不进任何 LLM 上下文，行为出口仅为位移+npc_action 确定性中文文案）；背景 NPC 决策链零触碰；token 基线 4519 不变（习惯事件未进入 chen/lily 对话时刻记忆窗口，与 tavern_sun 升级时同口径实测）
- **后续小步展望**（每项一行）：2.5 倍禀赋效应（对已持有物品的估价放大）；损失域豪赌（亏损状态下的风险偏好反转）——EWMA 财富基准已于第二小步落地（见下）
- **验收标准**（玩家可感知）：黄昏时点走进酒馆场景可见常客聚集（三常客+老板+帮工同场）且习惯文案可见（"（收了工，秦大夫熟门熟路拐进酒馆）"类）；`relationships=None` 零习惯零行为向后兼容；零 LLM 双口径；验收见 `tests/test_habit.py`（4 测试类 19 用例：习惯推导分叉/四层触发闸门/文案轮转/17 点聚集端到端/熄灯交接/非黄昏不动/同 hour 判重/零 LLM/空网兼容）

#### 第二小步已落地（2026-10-15）：EWMA 财富基准 `WealthLedger`

> 三件套之"EWMA 财富基准"落地：`engine/economics.py`（规范裁定 docs/NPC_TAG_DATABASE.md 1.2 Refactor 行——废除享乐适应 ALT-ODE 连续微分方程，改为收获事件触发的指数平滑更新；Tier 0 纯代码 0 token，docs/02 四级算力分工明列）。

```
创生档案 generate_village()（单一来源，25 人 innate.wealth 对数正态值）
    │  NPCEngine.__init__：WealthLedger.ensure_account 逐人建账
    │  （W=创生财富，R=W₀ 创生即基准），幂等
    ▼
EventBus 订阅（kinds=["item_given"] 按类型，收获域唯一事件源）
    │  WealthLedger.on_event：payload["to"] 有账才入账
    │  物品估值 _estimate_value（与快脑报价同口径：铁剑 10="铁剑十两"；
    │  品类后缀边界"铁剑鞘"≠铁剑，独立实现零 import）
    ▼
apply_gain：W += value；暖态账 R = 0.5·W + 0.5·R（字面 EWMA，
冷启动首笔收获充当统计学种子：基准不动、收获全额入感）
    ▼
fortune_tag(npc_id)：gain_ratio=(W−R)/max(R,1) ≥ 0.25
→ "最近发了笔小财"（唯一离散标签，替换 8D 全常带"心境平稳"
防语义矛盾）；不显著/无账返回 "" → prompt 逐字节不变
```

- **EWMA 机制与享乐适应语义**：NPC 对财富得失的感知是相对心理参考基准点 R 的（前景理论参考点依赖），而非绝对加总——铁匠陈（创生财富 30）收一把铁剑（估值 10）：gain_ratio = 10/30 ≈ 0.333 → 触发"最近发了笔小财"进【此刻内心】行；连送第二把 0.250、第三把 0.200 → 标签熄灭（**常送礼就不稀罕了**，逐笔严格递减的享乐适应）。α=0.5 校准：一礼半适应的中庸锚点（过小则常送礼仍稀罕、过大则当刻即吞没永不触发），兼为 2 的负幂浮点精确可复现。冷启动种子豁免为契约内自由裁量（书面论证见 economics.py docstring）：字面公式无豁免时首笔收获当刻被半吸收，chen 收铁剑仅 0.143 < 0.25 显著带永不触发，且可证任何 R₀ 初始化均无解（首笔触发需 G₀≥6.67 与静默对照需 G₀<6 矛盾）；首笔种子 + 第二笔起字面 EWMA 是验收探针可满足的唯一口径
- **六情边界（共线性纪律）**：item_given 的情绪语义已由荀子六情映射独占（StateUpdater → 六情"好"矢量 → e_P 增量 + 亲缘度入关系网）；WealthLedger 是独立经济变量层，**严禁再改任何 8D 心智参数**（同一事件双份情绪增量=两套情绪系统漂移死穴）；行为出口只有 fortune_tag 离散中文标签，浮点 W/R/gain_ratio 永不进 prompt（<35token 规范）
- **成本账 0-token**：纯规则 Tier 0——不持有 LLM 引用、不组装 prompt；按类型订阅只消费 item_given；核心 NPC 才走决策组装路径（快脑命中/背景 NPC 天然零影响）；无财富事件时 fortune 全空、prompt 逐字节全等——token 基线 4519 不变（实测严格相等，基线不变性测试锁）
- **损失域诚实登记**：本小步只实现收获域（gain），W 只增不减；损失域（支出/被夺/卖传家宝）、2.5 倍禀赋效应、损失域豪赌留待交易 API 小步——引擎落地交易系统前不做纸面模拟（诚实登记非承诺债）→ **已于第三小步清偿（见下）**

#### 第三小步已落地（2026-10-16）：交易 API 最小闭环 + 损失域 + 禀赋效应（审查指令 1）

> 三件套收口关键路径：交易事件源落地终结"W 只增不减"现状。`NPCEngine.player_buys` / `player_sells` 双入口 + `WealthLedger` 扩支出侧 + 快脑禀赋溢价报价，全部 Tier 0 纯规则 0-token。

```
玩家 ──player_buys(npc, item)──▶ 核心 NPC（有库存才卖：out_of_stock 拒绝）
    price = endowment_price(estimate_value) = 2.5×标准价（WTA 卖出要价）
    手动库存转移（不经 transfer_item——防 item_given 双事件双记账）
    发布 item_bought {seller, item, price} ─▶ WealthLedger.apply_gain(price)
                                              （NPC 收钱，收获域既有语义）
玩家 ──player_sells(npc, item)──▶ 核心 NPC（宽松成交：与 player_gives 同构）
    price = estimate_value（WTP 收购出价=标准价，不压价）
    发布 item_sold {buyer, item, price} ─▶ WealthLedger.apply_loss(price)
                                           （NPC 付钱，W-=price，R 纹丝不动）
    NPC 记忆：item_bought/item_sold 模板（importance 0.6 日常档），
    seller/buyer 点名感知无视距离（与 player_spoke 的 to 键同构）

快脑查价联动（玩家可感知通道）：
    _content_price 命中经营范围商品词 → 查该 NPC 实体 inventory 持有现货
    → 持有：禀赋溢价句「{goods}{cn_amount(endowment_price)}两，看对眼才卖。」
    → 未持有：既有标准句逐字节不变（零回归）
```

- **损失域基准粘性（本步灵魂语义）**：收获侧基准**半吸收**（暖态 R=αW+(1-α)R，享乐适应第三把剑 tag 熄灭）vs 损失侧基准**零吸收**（R 恒定，痛感不钝化）——同量纲对照断言（tests/test_trade.py）：chen W₀=30 连卖三把铁剑（每把 10 两），loss_ratio 0.333→0.667→1.000，"最近手头紧"标签**逐笔持续不熄**；对照组连收三把第三笔"小财"熄灭。收获钝化 vs 损失全保留的不对称，正是损失厌恶（人对损失的痛感约为等量收获快感的 2.5 倍）的离散工程根源；W 允许减到负数（账面赤字=赊账事实，账本如实登记，max(R,1) 分母防退化）
- **禀赋效应 WTA/WTP 实验结构**：同一把铁剑，玩家 10 两卖给铁匠（estimate_value 标准价收购）、铁匠 25 两卖回给玩家（endowment_price 2.5×四舍五入溢价）——WTA(卖出要价) > WTP(买入出价) 的经典不对称；快脑查价联动让玩家**默认场景即可感知**（走进铁匠铺问价，chen 对自己打的剑开口就是二十五两——Kahneman 杯子实验的"拥有即惜售"）。计价口径单一来源：economics.endowment_price（engine 交易 API 与 fast_brain 报价共用，报价句数字运行期生成不手写常量）
- **六情边界延续（共线性纪律）**：item_bought/item_sold 是纯经济事件——六情映射不认识它们是设计内边界（交易的情绪表达由 fortune_tag 双向离散标签独占：损失走"最近手头紧"标签，不产生 8D 增量）；tests/test_trade.py 有交易前后 8D 逐项不变断言 + economics.py 零 inner_state import 的架构边界断言（延续 test_economics.py ast 先例）
- **背景 NPC 交易入口评估（审查指令 1④，PM 书面裁定：本步不开放）**：player_buys/player_sells 与 player_gives 同口径只认核心 3 NPC（self.npcs）；背景 NPC 账本**事件层可达**（第 14 次审查亲测 herb_boy 直发 item_given 可入账）。不开放玩家入口的理由：①交易体验的核心是议价对话与惜售话术（慢脑 LLM 能力），背景 NPC 无慢脑，开放只产生"沉默成交"的低体验交互；②玩家在村庄的交易对象天然是铁匠/杂货/酒馆——3 核心恰好覆盖三类店铺经营者，体验完备；③成本面：规则成交文案需另写一套非本步余力。未来开放路径已就绪：入口一行注册（self.background_npcs 校验）+ Tier 0 规则成交文案，登记 T6/远期窗口再评估
- **成本账 0-token**：交易 API 纯规则确定性（计价/库存转移/事件发布/文案拼接全部零 LLM）；快脑溢价报价 0-token 秒回（LLM call_count==0 断言）；无交易时 fortune 空路径 prompt 逐字节全等——token 基线 4519 实测不变（容忍带内零影响，tests/test_token_baseline_deterministic.py 绿）
- **验收标准（玩家可感知口径，审查"交易心理可感知"执法）**：①端到端买卖账面真实变动+库存真实转移；②损失标签与 GAIN_TAG 对称（含享乐适应损失侧对照——痛感持续不熄的灵魂断言）；③禀赋估值上浮 2.5 倍有测试（无库存拒绝/有库存溢价）；④对话情境分叉 prompt 内容级断言（卖出后【此刻内心】行含"最近手头紧"）；⑤快脑默认场景溢价报价可见；验收见 tests/test_trade.py（22 用例）+ tests/test_fast_brain_endowment.py（11 用例）
- **余项（损失域豪赌）**：亏损状态下的风险偏好反转（豪赌）依赖"亏损状态"感知出口——损失标签已就位，留待 T6 窗口按余力评估（依赖交易频率真实数据先行观察）

#### 附：核心 NPC（chen）习惯回路接入评估（2026-10-15，清偿 10-14 遗留 1）

> HabitLoop 第一小步仅覆盖背景层；chen 与 tavern_sun 有酒友边（0.6），是否接入核心层评估如下。**PM 裁定：本周期不接入，维持现状。**

- **事实基础**：chen 作息 6:00 WORKING / 12:00 IDLE / 13:00 WORKING / 20:00 SLEEPING——17-18 点黄昏窗 chen 处于 **WORKING（黄昏仍在打铁）**，HabitLoop 的 IDLE 状态闸门正确地不触发。要接入必须改 chen 作息（如 17:00 IDLE）。
- **成本**：①核心 NPC 习惯移动产生 entity_moved 事件写入自己短期记忆，`RECENT_CONTEXT_WINDOW=4` 窗口被位移记忆挤占——下次对话的【最近的经历】上下文质量真实受损（token 与叙事双重成本）；②"铁匠开炉到黄昏"的敬业人设被牺牲——改作息换聚集，人格一致性受损。
- **收益边际**：黄昏酒馆已有 3 常客（guard_wang/fisher_zhou/doc_qin）+老板+帮工 5 人聚集（第一小步验收场景），村庄黄昏生活气息已足；且"黄昏去 forge 能找到打铁的 chen、去 tavern 能遇到喝酒的大夫"——**两种生活节律并存（敬业 vs 消遣）本身就是人口多样性的真实表达**，铁匠不需要也喝酒。
- **远期展望（本周期不实施）**：若未来接入，可走"收工酒"变体——chen 19:00 IDLE 短暂移步酒馆、20:00 作息接管归家（需 `NPC.apply_schedule` 扩展非居所中转点语义，记忆成本须先做窗口影响实测），仅记录于文档远期章节。


### 架构图重画硬清单（T3 执行，2026-10-07 固化；2026-10-10 22:00 已重画完成）

> 审查指令（10-06 第六次审查指令 3 + 10-09 第九次指令 3）要求：`docs/architecture.png` 重画必须覆盖以下组件，缺项判文档失同步。

1. **FillerEngine 旁路组件** ✅入图：`engine/filler.py` 脾气掩码×8D 状态 → 同步垫话，`player_says` 链路中慢脑调用前的 0-token 旁路（10-06 落地，图已收录）；
2. **快慢脑分流结构** ✅入图：快脑意图规则匹配（`engine/fast_brain.py` FastBrain 六意图，0-token 秒回 + fast_brain_stats 接管率统计）与慢脑 LLM 决策链的双通道分流门，fast 绕行路径直达行动层（图已收录）；
3. **标签库层（tag_genesis + tag_mount）** ✅入图：`engine/tag_genesis.py` 先天属性创生模块与 `engine/tag_mount.py` 标签挂载账本（生成→挂载→visible_tags→身份标签行数据流，图已收录）；
4. **极简组装后决策上下文结构** ✅入图：决策上下文注入节点含【此刻内心】to_discrete_tags() ≤4 离散标签（替换浮点文本）四块结构（图已收录）。

重画执行记录：2026-10-10 22:00 统一重画（diagram-drawing 技能 + 视觉 QA 12 项全 PASS + PM 亲读四组件与代码数据流一致复核），`docs/architecture.drawio` 源文件同步更新；旧版 5D 参数向量表述已随重画失效。

### 远期研究方向（本周期禁止实施）

CfC 连续神经算子、万级 NPC 宏观 PDE、波函数坍缩、兰彻斯特战争、文明演化、REM 梦境、连续语义漂移、普罗普衣钵继承完整协议——保留在规范文档中，待村庄里程碑稳固后评估。

## 自主迭代系统（多 Agent 协作）

项目由一套定时驱动的自主迭代系统持续演进（GAN 式对抗 + PM 协作）：

- **每日 22:00 迭代（PM 模式）**：主会话作为产品经理，通过 Task 工具派子代理协作——explore 型调研代码影响面，general 型实现功能/写测试（可并行）。PM 不亲自写实现代码，负责任务分解、自包含任务书撰写、验收（亲自跑测试、读 diff）、整合提交。红线：子代理产出必须 PM 亲自验证后才可入库；Task 不可用时降级为单兵模式。
- **每三天 23:00 审查（判别器）**：以"默认不合格"立场审查，可派 explore 子代理并行收集证据，但评级与批判必须主审者亲自出。含主人指令核对、数据一致性核查、对抗升级机制。
- **共享状态**：PROGRESS.md（北极星+目标状态机+主人指令）、iteration-log/（逐日日志）、reviews/（批判报告）构成跨会话共享内存；桌面 log/ 生成面向项目主人的监督简报。

## 已知边界（当前 MVP 的取舍）

- 记忆检索是关键词评分而非语义向量（接口已留好替换点）
- 巩固摘要是规则压缩而非 LLM 总结（避免离线不可测）
- 单进程内存态，多实例/分布式不在 L1 范围

> AI生成