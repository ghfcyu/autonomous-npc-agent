---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '568d83cf-0d72-4cb3-bbf3-4420ee70d105'
  PropagateID: '568d83cf-0d72-4cb3-bbf3-4420ee70d105'
  ReservedCode1: '7436c74d-99b4-4862-9956-b5b51474ebd0'
  ReservedCode2: '7436c74d-99b4-4862-9956-b5b51474ebd0'
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

关键事件类型：`player_entered` / `player_spoke` / `item_given` / `npc_action` / `weather_changed` / `time_passed`

### 2. 记忆层（`engine/memory.py`）

```
ShortTermMemory (deque, 容量 30)
        │  溢出时 consolidate()
        ▼
LongTermMemory (JSON 持久化, 检索评分)
```

- **记忆条目** `MemoryRecord`：内容 + tick + 重要度(0~1) + 标签 + 类型
- **检索评分**：`score = 2×标签重合 + 关键词重合 + 重要度 + 时间衰减`，纯标准库实现；接口已抽象，L2 可整体替换为向量检索
- **巩固（consolidate）**：短期记忆满时，把最旧的一批按行为主题压缩成摘要条目写入长期记忆——这是 NPC"记得你上次来过"的机制基础

### 3. 决策层（`engine/decision.py`）

**混合决策**是本项目最重要的设计决策：

| 组件 | 职责 | 为什么 |
|---|---|---|
| `StateMachine` | 状态迁移硬约束（IDLE/WORKING/TALKING/SLEEPING） | 确定性、可测试、防 LLM 越权 |
| `DecisionEngine` | 组装上下文 → 调 LLM → 解析动作 | 柔性、个性化、自然语言 |

决策流程：

1. 硬规则前置：当前状态是否允许对话？（SLEEPING → 直接产出"睡梦中嘟囔"的回退动作）
2. 上下文组装：人格卡 + 相关长期记忆 + 短期记忆 + 世界快照 + 玩家输入 + **输出 JSON 契约**
3. LLM 生成结构化动作
4. 校验失败/解析异常 → 人格自带的 `fallback_bank` 安全回退

### 4. 行动层（`engine/actions.py`）

- 动作白名单：`speak / move / give_item / emote / refuse`
- `ActionValidator`：状态机一致性（睡觉不能聊天）、世界一致性（没铁矿石不能送武器）
- `ActionExecutor`：执行后产出新 `WorldEvent` 回流事件总线 → 其他 NPC 也能感知到（多智能体的地基）

### 5. LLM Provider（`engine/llm/`）

- `BaseLLMProvider.chat(messages) -> str`：唯一抽象
- `OpenAICompatProvider`：urllib 实现，兼容任意 OpenAI 协议端点（含本地模型），环境变量 `NPC_LLM_BASE_URL / NPC_LLM_API_KEY / NPC_LLM_MODEL`
- `MockLLMProvider`：基于人格对话库 + 关键词规则的确定性实现，用于离线开发与测试

### 6. 编排层（`engine/engine.py` + `engine/npc.py`）

- `Persona`：JSON 配置 → 人格对象（性格、背景、语气、对话库、作息表）
- `NPC`：人格 + 记忆系统 + 状态机 + 决策引擎的聚合根，订阅事件总线
- `NPCEngine`：世界 + NPC 集合的编排入口，暴露 `player_says / tick / status` 三个核心 API

## 数据流（一次对话）

```
玩家输入 "能帮我打一把剑吗？"
  → NPCEngine.player_says()
  → World 发布 player_spoke 事件 → NPC.perceive() 写入短期记忆
  → NPC.handle_player_input()
      → StateMachine 检查：WORKING 状态允许 TALKING ✓
      → MemorySystem.context_for("打剑") 检索相关长期记忆
      → DecisionEngine：组装 prompt → LLM → {"action":"speak","text":"..."}
      → ActionValidator：白名单 + 一致性校验 ✓
  → ActionExecutor 执行 → 世界事件 npc_action → 玩家收到回复
  → 记忆层沉淀本次交互 → 短期记忆超限则触发巩固
```

## 工程约定

- 类型注解全覆盖，`from __future__ import annotations`
- 核心引擎**仅标准库**（unittest 测试，无需 pytest）
- 所有 LLM 交互必须可降级：任何异常都不能中断游戏循环
- 配置与代码分离：新 NPC = 新增一个 JSON 文件

## 已知边界（当前 MVP 的取舍）

- 记忆检索是关键词评分而非语义向量（接口已留好替换点）
- 巩固摘要是规则压缩而非 LLM 总结（避免离线不可测）
- 单进程内存态，多实例/分布式不在 L1 范围

> AI生成