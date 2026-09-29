---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '04be73d8-5e6f-432d-a357-1169d49eb7b7'
  PropagateID: '04be73d8-5e6f-432d-a357-1169d49eb7b7'
  ReservedCode1: '437f0647-9db2-4e97-848c-e91c7a079a18'
  ReservedCode2: '437f0647-9db2-4e97-848c-e91c7a079a18'
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

关键事件类型：`player_entered` / `player_spoke` / `item_given` / `npc_action` / `weather_changed` / `time_passed` / `env_event`（G3 世界活性：事件槽满时自动触发）

- **EventSlot**（G3）：世界随机事件槽，每次交互/时间推进累积 +5%，满 100% 触发一个环境事件并归零。事件体现"NPC 有自己生活"（如铁匠陈想起该去收矿石了），通过 `_publish_env_event()` 确定性选择（基于 trigger_count 取模）并发布 `env_event` 类型事件，同地点 NPC 感知并写入短期记忆

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
InnerState（arousal/mood/energy/stress/trust，值域 [0,1]）
        │
        ├─→ to_prompt_text() → 注入决策上下文【此刻内心】
        └─→ decide() 硬约束检查（stress>0.8 拒绝 / energy<0.15 收摊）
```

- **InnerState**：5 维心理参数向量（兴奋度/心情/精力/压力/信任），`apply_delta` 自动 clamp 到 [0,1]
- **StateUpdater**：作为 EventBus 的第二个 catch-all 订阅者（第一个是 NPC._on_event 写记忆），按确定性规则更新参数：
  - `item_given` → trust+0.1, mood+0.05, stress-0.05（守财标签额外加成 trust）
  - `player_spoke` → arousal+0.05, stress+0.05（较为自负标签对赞美/批评放大效应）
  - `npc_action` → energy-0.02, arousal-0.01
  - `time_passed` → SLEEPING 时 energy+0.05/stress-0.02；否则 energy-0.01
- **标签系统**：Persona.tags（Dict[str, float]），如 {"较为自负": 0.7}。标签在 StateUpdater 中可推断地影响参数增量幅度，并在 system prompt 中注入供 LLM 柔性调整说话风格
- **硬约束**：decide() 在 LLM 调用前检查 stress/energy 阈值——压力过高自动 REFUSE（不接单），精力过低自动 REFUSE（提前收摊），与 SLEEPING 硬规则同级

### 3. 决策层（`engine/decision.py`）

**混合决策**是本项目最重要的设计决策：

| 组件 | 职责 | 为什么 |
|---|---|---|
| `StateMachine` | 状态迁移硬约束（IDLE/WORKING/TALKING/SLEEPING） | 确定性、可测试、防 LLM 越权 |
| `InnerState` | 内状态硬约束（stress>0.8 拒绝、energy<0.15 收摊） | 心理参数驱动行为边界 |
| `DecisionEngine` | 组装上下文（含内状态）→ 调 LLM → 解析动作 | 柔性、个性化、自然语言 |

决策流程：

1. 硬规则前置：当前状态是否允许对话？（SLEEPING → 直接产出"睡梦中嘟囔"的回退动作）
2. 内状态硬约束：压力 > 0.8 → 拒绝接单；精力 < 0.15 → 提前收摊
3. 上下文组装：人格卡 + 性格标签 + 相关长期记忆 + 短期记忆 + 世界快照 + **此刻内心状态** + 玩家输入 + 输出 JSON 契约
4. LLM 生成结构化动作
5. 校验失败/解析异常 → 人格自带的 `fallback_bank` 安全回退

### 4. 行动层（`engine/actions.py`）

- 动作白名单：`speak / move / give_item / emote / refuse`
- `ActionValidator`：状态机一致性（睡觉不能聊天）、世界一致性（没铁矿石不能送武器）
- `ActionExecutor`：执行后产出新 `WorldEvent` 回流事件总线 → 其他 NPC 也能感知到（多智能体的地基）

### 5. LLM Provider（`engine/llm/`）

- `BaseLLMProvider.chat(messages) -> str`：唯一抽象
- `OpenAICompatProvider`：urllib 实现，兼容任意 OpenAI 协议端点（含本地模型），环境变量 `NPC_LLM_BASE_URL / NPC_LLM_API_KEY / NPC_LLM_MODEL`
- `MockLLMProvider`：基于人格对话库 + 关键词规则的确定性实现，用于离线开发与测试

### 6. 编排层（`engine/engine.py` + `engine/npc.py`）

- `Persona`：JSON 配置 → 人格对象（性格、背景、语气、对话库、作息表、**标签**）
- `NPC`：人格 + 记忆系统 + 状态机 + 决策引擎 + **内状态** 的聚合根，订阅事件总线（记忆写入 + 状态更新双订阅）
- `NPCEngine`：世界 + NPC 集合的编排入口，暴露 `player_says / player_gives / tick / status` 四个核心 API（`player_gives`：玩家送礼，库存转移或宽松发布事件，NPC 经事件链路记入短期+长期记忆）

## 数据流（一次对话）

```
玩家输入 "能帮我打一把剑吗？"
  → NPCEngine.player_says()
  → World 发布 player_spoke 事件
      → NPC._on_event 写入短期记忆
      → StateUpdater.on_event 更新内状态（arousal+0.05, stress+0.05）
  → NPC.handle_player_input()
      → StateMachine 检查：WORKING 状态允许 TALKING ✓
      → InnerState 硬约束检查：stress ≤ 0.8 且 energy ≥ 0.15 ✓
      → MemorySystem.context_for("打剑") 检索相关长期记忆
      → DecisionEngine：组装 prompt（含【此刻内心】状态行）→ LLM → {"action":"speak","text":"..."}
      → ActionValidator：白名单 + 一致性校验 ✓
  → ActionExecutor 执行 → 世界事件 npc_action → StateUpdater 更新内状态（energy-0.02）
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

## 工程约定

- 类型注解全覆盖，`from __future__ import annotations`
- 核心引擎**仅标准库**（unittest 测试，无需 pytest）
- 所有 LLM 交互必须可降级：任何异常都不能中断游戏循环
- 配置与代码分离：新 NPC = 新增一个 JSON 文件

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