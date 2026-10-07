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
- **Location.category**（G6）：地点分类（`shop` 店铺 / `public` 公共空间 / `residence` 居所），支撑村庄可探索结构。`World` 默认从 `configs/locations.json` 加载地点清单（文件缺失/损坏/空列表回退内置默认 17 地点：5 店铺 + 2 公共 + 10 居所），测试可显式传 `locations=` 覆盖。`World.snapshot` 仍是局部感知（仅同地点实体），`NPCEngine.status` 的 `world.locations` 遍历全村庄用于总览展示

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
        ├─→ to_prompt_text() → 注入决策上下文【此刻内心】
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
- **RelationshipNetwork**：存储与查询关系，`to_prompt_text()` 生成注入 LLM 决策上下文的关系描述（好感 >0.3 显示"好感"，< -0.3 显示"敌意"），无关系时返回空串不注入
- **配置驱动**：`configs/relationships.json`（JSON 数组），NPCEngine 初始化时自动加载

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
```

- **BackgroundNPC**：轻量级 NPC，不持有 LLM 引用（架构上不可能调用 LLM），不维护记忆/内状态/状态机
- **配置驱动**：`configs/background_npcs/*.json`（id/name/role/location_id/summary/reactions/inventory/residence/schedule），NPCEngine 全默认构造时自动加载；`residence` 为居所地点 id（缺省回退初始位置），`schedule` 为作息表（G6-B 已实现作息驱动：`apply_schedule` 按 tick 时间在工作地与居所间移动 NPC，WORKING→工作地、SLEEPING→居所、IDLE→保持原位）
- **规则反应**：对配置了模板的事件类型产生确定性反应——`env_event` 只反应同地点、`entity_moved` 只反应有人来到自己地点，模板按计数器取模轮转，`{npc_name}` 替换为 NPC 名
- **零 LLM 断言**：MockLLMProvider 记录 `call_count`/`call_log` 与 `total_tokens_used`，测试可断言背景 NPC 的 id 从未出现在调用日志中、且 `total_tokens_used == 0`（以 token 数非调用次数证明零 LLM）

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
3. 上下文组装：人格卡 + 性格标签 + **人际关系** + 相关长期记忆 + 短期记忆（**`RECENT_CONTEXT_WINDOW=4`** 上下文裁剪，削减 prompt token）+ 世界快照（含**【周围的人】**同地点实体及外观，G5）+ **此刻内心状态** + 玩家输入 + 输出 JSON 契约
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
- `NPCEngine`：世界 + NPC 集合的编排入口，暴露 `player_says / player_gives / player_changes_appearance / tick / status` 五个核心 API（`player_gives`：玩家送礼，库存转移或宽松发布事件，NPC 经事件链路记入短期+长期记忆；`player_changes_appearance`：玩家换装，同地点 NPC 感知；`tick`：推进世界时间并对核心+背景 NPC 应用作息驱动位置移动）。全默认构造时自动扫描 `configs/`（npcs + background_npcs + relationships + locations），加载小村庄：2 核心 NPC（chen/lily 跑完整决策链）+ 8 背景 NPC（零 LLM 规则反应）+ 17 地点（三类可探索）。`player_says` 后按 NPC 聚合 token 统计（`token_stats`），`status()` 返回 `token_stats` 字段（`by_npc` 按 NPC 聚合 prompt/completion/total/calls，`total_tokens` 为 LLM 累计，`llm_calls` 为调用次数）

## 数据流（一次对话）

```
玩家输入 "能帮我打一把剑吗？"
  → NPCEngine.player_says()
  → World 发布 player_spoke 事件
      → NPC._on_event 写入短期记忆
      → StateUpdater.on_event 更新内状态（e_A+0.05, S_stress+0.05）
  → FillerEngine.generate(反应性 8D 状态) → 垫话占位（慢脑前同步返回，0-token）
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

- **首批已落地（2026-10-07，`engine/tag_genesis.py`）**：6D 先天正态属性生成器（截断正态 [1,10]，`alcohol_tol` σ=1.5 依规范 4.3 节）+ 财富对数正态（按 2.1 节分位语义参数化：P5≈10/P95≈50 铜板）+ 金字塔四层采样（70/20/8/2）+ 齐普夫采样器（s=1.15）+ 尧氏 8 风格×4 阶骨架 + 美貌六档/属性四档离散称号分桶；分布断言与规范契约锁见 `tests/test_tag_genesis.py`（32 用例）；三处规范偏离（σ=1.5/财富参数化/软截断=拒绝采样）书面记录于模块 docstring。**待接入**：NPC 配置挂载（chen/lily 重配）、林传鼎 8 脾气掩码（待 PAD 值域裁定）、缺陷/把柄强制挂载、四时态与互斥锁
- **6D 先天正态属性**：美貌/力量/悟性/勇气/道德/酒量 ~ N(5.0, 1.25²)，财富对数正态——普通人占 70% 的世界基石
- **林传鼎 8 显性脾气掩码**（安静/喜悦/暴躁/哀戚/惊恐/恭顺/傲慢/惭辱）：驱动垫话与微动作
- **尧氏 8 风格原型 × 4 阶执念深度**（萌芽 70%/显性 20%/执念 8%/病态 2%）
- **缺陷+把柄强制挂载**：非背景 NPC ≥1 显性缺陷 + ≥1 绝密把柄（勒索/解谜玩法源）
- **四时态生命周期**（先天印记/后天经历/瞬态 TTL/关系羁绊）+ 互斥锁
- **分布纪律**：连续属性正态、离散特质金字塔长尾（70/20/8/2）、职业齐普夫

### 对话层 v2：快慢脑分流（T3）

```
玩家输入 ──▶ 快脑分流门（意图规则匹配，<20ms，0 token）
              ├─ 命中（约70%日常：问路/查价/招呼）──▶ 秒回
              └─ 未命中 ──▶ 脾气掩码垫话（0ms 动作抢跑 + 10ms 垫话）──▶ 慢脑（LLM 决策链）
```

- **垫话引擎原型已落地（2026-10-06，主人裁定 17 提前先行）**：`engine/filler.py` 脾气掩码粗粒度 3 掩码（irritable/cheerful/aloof）× 8D 状态带规则，`player_says` 结果含 `filler` 字段；T2 落地林传鼎 8 脾气掩码后替换为标准掩码表
- **<35 token 极简 Prompt 组装**：进 prompt 的只有 3-4 个高显著度离散中文标签，严禁浮点向量（当前过渡态注入 8 参数"名: 值"文本 71 字符，真实 LLM 冒烟已实证其 token 占用，T3 改造输入）
- 四级算力分流：Tier 0 纯数学物理（<0.1ms）/ Tier 1 规则与哈希（<0.5ms）/ Tier 2 端侧小模型 / Tier 3 云端 LLM（仅深度叙事）——环境 NPC 封印在 Tier 0/1

### 世界层 v2：人口学村庄（T4）

- 25 NPC 按 6D 正态属性 + 齐普夫职业分布 + 标签金字塔生成；关键 NPC（1-5%，8D 全量+把柄）vs 环境 NPC（4D 压缩稳态，零 LLM）
- 行为经济学离散事件化（T5）：EWMA 财富基准、2.5 倍禀赋效应、损失域豪赌；杜希格习惯回路 0-token 截断

### 架构图重画硬清单（T3 执行，2026-10-07 固化）

> 审查指令（10-06 第六次审查指令 3）要求：`docs/architecture.png` 重画必须覆盖以下组件，缺项判文档失同步。

1. **FillerEngine 旁路组件**：`engine/filler.py` 脾气掩码×8D 状态 → 同步垫话，`player_says` 链路中慢脑调用前的 0-token 旁路（10-06 落地，图未收录）；
2. **快慢脑分流结构**：快脑意图规则匹配（0-token 秒回）与慢脑 LLM 决策链的双通道分流门（T3 主体）；
3. **标签库层（tag_genesis）**：`engine/tag_genesis.py` 先天属性创生模块（6D 正态+金字塔/齐普夫分布+尧氏风格，10-07 落地）作为决策上游的离散标签供给层；
4. T2 主体（8 脾气掩码接入决策链）与 T4 人口学生成器若有架构级数据流新增，一并入图。

重画时机：T3 快慢脑分流落地引发架构实质变化时统一执行（避免阶段性反复重画）；期间以本清单与正文描述维持文档-代码一致性。

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