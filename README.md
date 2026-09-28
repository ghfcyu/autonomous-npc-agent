---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '92ef3cfa-1378-45d4-9120-f550eb8c11f1'
  PropagateID: '92ef3cfa-1378-45d4-9120-f550eb8c11f1'
  ReservedCode1: '286141b8-b496-454a-b5ea-88faa5df0644'
  ReservedCode2: '286141b8-b496-454a-b5ea-88faa5df0644'
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
- **可靠性护栏**：动作白名单 + 状态机校验，LLM 输出经过验证器过滤，异常时安全回退
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
│   ├── npc.py             # NPC 装配：人格配置、感知订阅
│   ├── engine.py          # 引擎入口：世界与 NPC 的编排
│   └── llm/               # 可插拔 LLM Provider
│       ├── base.py        #   抽象接口
│       ├── openai_compat.py  # OpenAI 协议实现
│       └── mock.py        # 离线 Mock（开发/测试用）
├── configs/npcs/          # NPC 人格配置（JSON，热加载）
├── api/server.py          # FastAPI 服务（可选）
├── demo/index.html        # 2D 可视化演示端
├── scripts/run_demo.py    # CLI REPL 演示
├── tests/                 # 单元测试 + 引擎集成测试
├── ARCHITECTURE.md        # 架构设计文档
└── Dockerfile
```

## 架构一图流

```
                    ┌────────────────────────────────┐
   玩家/游戏事件 ──▶ │  感知/世界层 World + EventBus   │
                    └───────┬────────────────────────┘
                            │ world.snapshot() / events
                            ▼
                    ┌────────────────────────────────┐
                    │  记忆层 MemorySystem            │
                    │  短期(会话) + 长期(持久化+检索) │
                    └───────┬────────────────────────┘
                            │ context_for(query)
                            ▼
                    ┌────────────────────────────────┐
                    │  决策层 DecisionEngine          │
                    │  状态机(硬规则) + LLM(柔性表达) │
                    └───────┬────────────────────────┘
                            │ Action(JSON, 白名单校验)
                            ▼
                    ┌────────────────────────────────┐
                    │  行动层 ActionExecutor          │──▶ 世界状态变更/新事件
                    └────────────────────────────────┘
```

详见 [ARCHITECTURE.md](./ARCHITECTURE.md)。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

覆盖：记忆读写与巩固、事件驱动长期写入、玩家送礼闭环、事件总线、状态机迁移约束、动作白名单校验、LLM 异常输出回退、引擎端到端闭环。

## 路线图

- [x] **L1** 单 NPC 对话 + 人格配置 + 世界感知 + 分层记忆
- [x] **L2** 事件驱动的记忆写入（高价值事件实时直写长期记忆 + 玩家送礼入口，2026-09-28）
- [ ] **L2+** 对玩家的长期记忆强化（"你上周帮过我"）、语义化检索、记忆巩固升级（LLM 摘要）
- [ ] **内状态** InnerState 参数向量 + 规则引擎状态更新器 + 可推断标签系统（进行中）
- [ ] **L3** 目标驱动的自主行为（日程、需求、主动性）
- [ ] **L4** 多智能体社会（NPC 互聊、关系网、信息传播）
- [ ] 行为评测集（给定场景断言行为合理性，自动化回归）

## License

MIT

> AI生成