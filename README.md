---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '20791bc5-03aa-4565-9398-cf17a99a56b5'
  PropagateID: '20791bc5-03aa-4565-9398-cf17a99a56b5'
  ReservedCode1: '869de5de-8c60-4078-8e15-2acd3c1f681e'
  ReservedCode2: '869de5de-8c60-4078-8e15-2acd3c1f681e'
---

# autonomous-npc-agent

一个**可配置、可复用的游戏 AI NPC 引擎**：让游戏中的 NPC 拥有感知、分层记忆、目标约束下的自主行为与对话能力，而不是只会机械回复的聊天挂件。

> 核心理念：**状态机管硬规则，LLM 管柔性表达** —— 用确定性约束防止大模型破坏游戏世界规则，用大模型赋予 NPC 自然的语言与个性化反应。

## 特性

- **分层架构**：感知/世界层 → 记忆层 → 决策层 → 行动层，职责单一、可独立替换
- **可插拔 LLM**：抽象 Provider 接口，兼容任意 OpenAI 协议端点；无 API Key 时自动降级到内置 Mock（离线可开发、可测试）
- **配置驱动人格**：NPC 人格、说话风格、作息日程全部由 JSON 配置定义，支持热加载，新增 NPC 零代码
- **分层记忆**：短期记忆（当前会话）+ 长期记忆（持久化 JSON），带基于标签/关键词/重要度/时间衰减的检索评分
- **记忆巩固**：短期记忆溢出时自动压缩沉淀为长期记忆（"记得你上次来过"的能力基础）
- **事件驱动记忆**：送礼等高价值事件（重要度 ≥ 0.7）实时直写长期记忆，无需等待有损巩固；提供 `player_gives` 送礼入口，NPC 会永远记得你给过他什么
- **量化内状态**：每个 NPC 拥有实时心理参数向量（兴奋度/心情/精力/压力/信任），事件驱动规则引擎实时更新——送礼涨信任、催单涨压力、干活耗精力、睡觉回血
- **可推断标签系统**：人格配置支持心理学标签（如"较为自负 0.7""守财 0.8"），标签可推断地影响言行——被夸时兴奋度涨幅高于常人、收到礼物时信任涨幅更大
- **内状态硬约束**：压力过高自动拒绝接单、精力过低提前收摊，NPC 的内心状态直接决定行为边界，而非仅靠 LLM 自由发挥
- **世界活性（随机事件槽）**：世界维护事件槽，每次交互/时间推进累积 +5%，满 100% 自发触发环境事件（"铁匠陈想起该去收矿石了""莉莉盘算新货报价"），同地点 NPC 感知并写入记忆——世界不因玩家离线而静止
- **分层 NPC（轻量背景 NPC）**：核心 NPC 跑完整决策链（人格+记忆+LLM），背景 NPC 只用"身份+一句概括+关系"纯规则反应——零 LLM 调用、零记忆开销，让村庄有人气但不烧 token
- **关系网络**：NPC 之间结构化存储社会关系（父子/熟人/宿敌，好感/敌意值），关系数据注入核心 NPC 决策上下文——提到其父时语气变化、提及熟人时态度不同
- **外观与环境状态**：实体拥有可变外观（穿着/姿势），换装作为 `appearance_change` 事件发布，同地点 NPC 感知写入记忆、异地无感知；决策上下文注入【周围的人】块——NPC 对话能"看见"并引用对方穿着（"你今天穿着围裙"）
- **可靠性护栏**：动作白名单 + 状态机校验 + 内状态硬约束，LLM 输出经过验证器过滤，异常时安全回退
- **零依赖内核**：engine 核心仅使用 Python 标准库；FastAPI 服务为可选层
- **可视化 Demo**：自带 2D 俯视小地图 + 聊天界面的 Web 演示端

## 快速开始

### 1. CLI 演示（零依赖，开箱即跑）

```bash
python3 scripts/run_demo.py
```

进入 REPL 后：

```
> look                      # 查看世界状态
> talk chen 你好，能帮我打一把剑吗？   # 和铁匠陈对话
> talk lily 今天有什么新货？           # 和商人莉莉对话
> tick                      # 推进世界时间
> quit
```

默认使用 Mock LLM（规则回退），**无需任何 API Key**。

### 2. 接入真实大模型

```bash
export NPC_LLM_BASE_URL="https://api.example.com/v1"   # 任意 OpenAI 兼容端点
export NPC_LLM_API_KEY="sk-..."
export NPC_LLM_MODEL="your-model"

python3 scripts/run_demo.py --llm openai
```

也可以复制 `.env.example` 为 `.env` 填入真实值（`.env` 已被 gitignore，不会提交）。
接入后可用冒烟测试验证全链路：

```bash
python3 scripts/llm_smoke.py    # 真实模型跑 感知→记忆→决策→行动 闭环
```

### 3. Web 可视化演示

```bash
pip install -r requirements.txt   # 仅 fastapi + uvicorn
python3 -m api.server             # 打开 http://127.0.0.1:8000
```

### 4. Docker

```bash
docker build -t autonomous-npc-agent .
docker run -p 8000:8000 autonomous-npc-agent
```

## 项目结构

```
autonomous-npc-agent/
├── engine/                # 核心引擎（纯标准库）
│   ├── world.py           # 感知/世界层：世界状态、事件总线
│   ├── memory.py          # 记忆层：短期/长期记忆、巩固、检索
│   ├── decision.py        # 决策层：状态机约束 + LLM 混合决策
│   ├── actions.py         # 行动层：动作定义、白名单校验、执行器
│   ├── inner_state.py     # 内状态层：量化心理参数 + 事件驱动规则引擎
│   ├── relationships.py   # 关系网络：NPC 社会关系存储与查询
│   ├── background_npc.py  # 背景 NPC：轻量级零 LLM 规则反应
│   ├── npc.py             # NPC 装配：人格配置、感知订阅、内状态
│   ├── engine.py          # 引擎入口：世界与 NPC 的编排
│   └── llm/               # 可插拔 LLM Provider
│       ├── base.py        #   抽象接口
│       ├── openai_compat.py  # OpenAI 协议实现
│       └── mock.py        # 离线 Mock（开发/测试用）
├── configs/npcs/          # 核心 NPC 人格配置（JSON，热加载）
├── configs/background_npcs/  # 背景 NPC 配置（JSON）
├── configs/relationships.json  # NPC 关系网络配置
├── api/server.py          # FastAPI 服务（可选）
├── demo/index.html        # 2D 可视化演示端
├── scripts/run_demo.py    # CLI REPL 演示
├── tests/                 # 单元测试 + 引擎集成测试
├── ARCHITECTURE.md        # 架构设计文档
└── Dockerfile
```

## 架构一图流

![架构图](docs/architecture.png)

- 事件经 EventBus 分发，**记忆层**（短期/长期/事件驱动直写）与**内状态层**（5 维参数 + 规则引擎 + 标签）并行消费
- 决策层混合决策：状态机与内状态管**硬规则**，LLM 管**柔性表达**；轻量 NPC 走纯规则路径（零 LLM 调用）
- 行动层白名单校验后执行，新事件回流总线，形成**世界闭环**

图表源文件 [docs/architecture.drawio](./docs/architecture.drawio) 可在 [app.diagrams.net](https://app.diagrams.net) 打开编辑。详见 [ARCHITECTURE.md](./ARCHITECTURE.md)。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

覆盖：记忆读写与巩固、事件驱动长期写入、玩家送礼闭环、事件总线、状态机迁移约束、动作白名单校验、LLM 异常输出回退、引擎端到端闭环、量化内状态参数变化、标签行为影响、内状态硬约束（压力/精力阈值拒绝）、能量随时间消耗与恢复、随机事件槽累积与触发、环境事件 NPC 感知与确定性测试、背景 NPC 规则反应与零 LLM 调用断言、关系网络 CRUD 与决策上下文注入、外观状态更新与事件发布契约、NPC 感知位置过滤（同地可见/异地不可见）、对话上下文的外观引用与 token 经济性（空 nearby 不注入）。

## 路线图

- [x] **L1** 单 NPC 对话 + 人格配置 + 世界感知 + 分层记忆
- [x] **L2** 事件驱动的记忆写入（高价值事件实时直写长期记忆 + 玩家送礼入口，2026-09-28）
- [x] **内状态** InnerState 参数向量 + 规则引擎状态更新器 + 可推断标签系统 + 内状态硬约束（2026-09-28）
- [x] **世界活性** 随机事件槽 + 环境事件自发触发 + NPC 感知写入记忆（2026-09-29）
- [ ] **L2+** 对玩家的长期记忆强化（"你上周帮过我"）、语义化检索、记忆巩固升级（LLM 摘要）
- [ ] **L3** 目标驱动的自主行为（日程、需求、主动性）
- [x] **L4 前置** 轻量背景 NPC（零 LLM 规则反应）+ 关系网络（结构化存储+注入决策上下文）（2026-09-30）
- [x] **外观与环境状态** Entity 可变外观 + appearance_change 事件 + 感知位置过滤 + 【周围的人】决策上下文注入（2026-10-01）
- [ ] **L4** 多智能体社会（NPC 互聊、关系网深化、信息传播）
- [ ] 行为评测集（给定场景断言行为合理性，自动化回归）

## License

MIT

> AI生成