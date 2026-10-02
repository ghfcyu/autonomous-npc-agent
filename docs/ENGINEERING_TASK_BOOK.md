# 基于LLM的世界架构模型工程实施任务书 (Engineering Master Task Book)
## —— 理论白皮书落地实施规范、模块分解结构 (WBS)、算力分流矩阵与测试规范

---

### 一、 任务书编制说明与工程北极星

本任务书依据顶层技术白皮书《基于LLM的世界架构模型研究报告》编制，旨在将全域大一统理论体系无缝分解并落地至 `autonomous-npc-agent` 引擎代码库。

*   **北极星目标**：构建一个在微观具身、主观心智、中观调度与宏观文明演化四层上完全自洽运转的活世界，初期以完整运转的 25 人小村庄及周围旷野生态为交付实体。
*   **工程硬约束 (Zero-Spike & Extreme Latency Reduction)**：
    1.  **零强制第三方依赖**：`engine/` 核心逻辑纯原生 Python 3.10+ 实现，数值矩阵、微积分与状态转移保持极简确定性，单测毫秒级全绿。
    2.  **极度压缩 LLM 依赖（算力分流红线）**：
        - 宏观环境（气候/温度/湿度/灾害）**绝对由纯代码数学模型运行，0 Token，0 LLM**；
        - 次等 NPC（野生动物、家畜）**绝对由纯代码行为树与效用 AI 运行，0 Token，0 LLM**；
        - 常规高频判断由**预训练小神经网络与自动化脚本**在端侧消化（<5ms）；
        - 云端 LLM 仅在关键剧情拐点、深度长考或突发剧变时按需异步介入，单 NPC 单游戏日 Token 消耗降低 90% 以上。
    3.  **确定性事件溯源**：状态流转遵循 `State(t+1) = Transition(State(t), Event)`，所有动作与生理突变均挂载于全局 `EventBus`。

---

### 二、 四级算力分流与分工矩阵 (Offloading Architecture)

明确规定在哪些模块、哪些角度使用纯代码、脚本、小模型与 LLM：

| 计算层级 | 载体形态 | 典型延迟 | Token 消耗 | 负责场景与具体角度 (Where & How) |
|---|---|---|---|---|
| **Tier 0: 纯代码与数学微分方程** | 偏微分方程 / 元胞自动机 / 几何碰撞 / 状态机 | **< 0.1ms** | **0** | **全局客观环境与物理反馈**：<br>1. 全局气候/温湿度/风场对流与天气演变 (`macro_world_model.py`)<br>2. 空间声学反平方光线追踪、视锥 FoV 剔除、断崖急停加速度 (`spatial_affordance.py`)<br>3. 生理内稳态（体温/氧气/水）微分步进、异位稳态负荷计算 (`homeostasis.py`)<br>4. 宏观 PID 央行调控、Tarjan 循环死锁打破 (`cybernetic_economy.py`)<br>5. 生态 Lotka-Volterra 食物网、GDD 作物积温 (`biosphere.py`)<br>6. **动物等次等 NPC 的分层行为树 (HBT) 与雷诺兹集群算法 (Boids)** (`sub_npc_animal.py`) |
| **Tier 1: 确定性自动化脚本** | 规则引擎 / 图数据库查询 / 调度器 | **< 0.5ms** | **0** | **高频逻辑闭环与契约结算**：<br>1. 物品栏增减、金币交易结算、借贷记账<br>2. 成文法三元组 `(Condition, Action, Penalty)` 快速知识图谱模式匹配<br>3. 婚姻 Gale-Shapley 稳定匹配算法、遗产继承分配规则<br>4. 通缉令与谣言在社交网络邻接图上的 Gossip 广播协议 |
| **Tier 2: 端侧预训练小神经网络 (SLM/NN)** | ONNX / INT4 量化模型 (0.1B~0.5B 或专用微网络) | **< 2~5ms** | **0** | **低延迟直觉反应、模式识别与非言语输出**：<br>1. **Jev 决策微模型**：输入环境+内稳态，输出 NPC 动作概率分布（搭话/离开/干活）<br>2. **面部微表情生成器**：根据情绪效价映射 FACS 动作单元（AU14轻蔑、AU12假笑）<br>3. **声调特征提取**：推断对话文本中的副语言颤抖率与语速<br>4. **情绪分类与测谎**：输入多模态特征，计算通道不一致性 $C_{disc}$ 与贝叶斯怀疑度<br>5. **视觉显著性卷积网络**：从环境多物体中快速锁定最吸睛的刺激目标 |
| **Tier 3: 云端大语言模型 (Cloud LLM)** | 现代前沿 LLM (如 Gemini 3.8 / 旗舰大模型) | **500ms~2s** | **按需** | **仅用于高级不可预测心智与叙事拐点**：<br>1. 极具深度的多轮哲学探讨、外交谈判与欺骗周旋<br>2. 伦理困境下的道德自我合理化与辩白生成<br>3. 面对重大灾难/断桥震惊后的深度长逻辑反思<br>4. 材料匮乏寻找替代品时的偶发创新（Serendipity 因果重组）<br>5. 宗教神话与复杂禁忌的语义生成 |

---

### 三、 模块分解结构 (Work Breakdown Structure, WBS)

```mermaid
graph TD
    Root[World Engine Core] --> Macro[宏观世界母体层 (Tier 0 代码)]
    Root --> Body[微观具身与物理层 (Tier 0 代码)]
    Root --> SubNPC[次等NPC与动物生态 (Tier 0 行为树)]
    Root --> MicroBrain[NPC 端侧微脑 (Tier 1 脚本 + Tier 2 小模型)]
    Root --> CloudMind[NPC 云端叙事心智 (Tier 3 LLM)]
    Root --> Civil[宏观经济与文明演化 (Tier 0 + Tier 1)]

    Macro --> M_World[engine/macro_world_model.py<br>气候/温湿度/风场/灾害/外部事件]
    Body --> M1[engine/spatial_affordance.py<br>负向可供性/断崖急停/空间遮挡]
    Body --> M2[engine/homeostasis.py<br>内稳态微分方程/躯体标记/相变拦截]
    SubNPC --> M_Animal[engine/sub_npc_animal.py<br>动物行为树/气味追踪/Boids集群]
    MicroBrain --> M_Micro[engine/npc_micro_brain.py<br>Jev直觉分类/FACS表情/副语言测谎]
    MicroBrain --> M8[engine/cascading_router.py<br>L0-L1-L2级联/信息熵/掩护动作]
    CloudMind --> M_Minimal[engine/minimalist_mind.py<br>D-T-S极简心智/抗谄媚对抗指令]
    Civil --> M9[engine/cybernetic_economy.py<br>离散PID央行/Tarjan破死锁]
    Civil --> M10[engine/biosphere.py<br>Lotka-Volterra/GDD农耕/SEIR疫病]
    Civil --> M11[engine/demographics.py<br>皮亚杰ZPD/Gompertz衰老/Gale-Shapley匹配]
    Civil --> M12[engine/politics_jurisprudence.py<br>暴力垄断/法律图谱/贝叶斯审判]
```

---

### 四、 核心工程模块设计与接口契约

#### 模块 1：全局宏观世界模型 (`engine/macro_world_model.py`) `[纯代码 Tier 0]`
*   **核心类**：`MacroWorldModel`, `ClimateGrid`, `DisasterEngine`
*   **设计原则**：完全由纯代码数学公式驱动，负责全球热力学与自然现象，绝对不调用 LLM。
*   **方法契约**：
    *   `tick_climate(delta_t)`:
        - 劳伦兹吸引子平滑步进：更新全局气压与风向向量 $\vec{v}_{wind}$；
        - 热力扩散方程：$\frac{\partial T_{env}}{\partial t} = \kappa \nabla^2 T_{env} - \vec{v}_{wind} \cdot \nabla T_{env} + Q_{solar} - Q_{rad}$；
        - 湿度对流方程：$\frac{\partial H_{env}}{\partial t} = D \nabla^2 H_{env} - \vec{v}_{wind} \cdot \nabla H_{env} + E_{evap} - P_{rain}$。
    *   `evaluate_disasters() -> List[DisasterEvent]`:
        - 干旱：湿度持续低于阈值导致土壤肥力失效；
        - 暴雨山洪：强降雨积分超标触发 NavMesh 道路阻断；
        - 雷火连锁：雷暴天气结合植被干燥度自动激活元胞火灾蔓延。
    *   `poll_external_events() -> List[WorldEvent]`: 活性槽积分结合气候生成宏观事件包（如“商队受困暴雪”、“蝗灾警报”），广播至 `EventBus`。

#### 模块 2：次等 NPC 与动物生态系统 (`engine/sub_npc_animal.py`) `[纯代码 Tier 0]`
*   **核心类**：`AnimalAgent`, `HerbivoreAgent`, `CarnivoreAgent`, `BoidsFlock`
*   **设计原则**：野生食草动物、食肉动物与家畜拥有完整的生物本能（饥饿、口渴、交配、恐惧），完全由分层行为树 (HBT) 与效用 AI (Utility AI) 驱动，零 Token。
*   **方法契约**：
    *   `HerbivoreAgent.tick(macro_world, hazards, threats)`:
        - 具有 $300^\circ$ 广角视觉与周期性抬头警戒；
        - 效用权衡：饥饿度（吃草）、口渴度（饮水）、求偶度（寻找同类）；
        - 逃窜机制：听到/看到捕食者即刻触发沿斥力场 $\mathbf{F}_{flee}$ 狂奔。
    *   `CarnivoreAgent.tick(macro_world, prey_smells, prey_entities)`:
        - 下风向嗅觉气味追踪：在风场下游感知食草动物粒子；
        - 人工势场半月形围捕：多只狼相互排斥但共同向猎物产生向心引力；
        - 生态逆向攻击：野外食草动物不足导致饥饿过高时，攻击目标自动切换为村落羊圈或落单村民。
    *   `BoidsFlock.step(obstacles)`:
        - 飞鸟与家畜集群：遵循对齐 (Alignment)、分离 (Separation)、内聚 (Cohesion)；遇扰动瞬间炸开。

#### 模块 3：NPC 端侧微脑与子模型系统 (`engine/npc_micro_brain.py`) `[Tier 1 + Tier 2]`
*   **核心类**：`NPCMicroBrain`, `JevIntuitionClassifier`, `NonVerbalSynthesizer`
*   **设计原则**：每个 NPC 标配端侧小模型/脚本子模块，处理高频直觉微反应，只有无法确定的复杂情况才上抛云端。
*   **方法契约**：
    *   `predict_micro_reaction(inner_state, nearby_entities) -> ActionProbDist`:
        - Jev 式 System 1 微分类器（基于极小预训练权重）：输入自身内稳态与环境感知，直接输出高置信度动作（打铁、点头、转身、走开），执行延迟 < 2ms。
    *   `synthesize_nonverbal(emotional_valence, inner_stress) -> NonVerbalPacket`:
        - 根据效价与压力直接计算 FACS 动作单元代码（AU14、AU12、AU6）与声音颤抖参数，提供给客户端渲染。
    *   `detect_deception(multimodal_signal) -> float`:
        - 运行通道不一致性计算公式，判定对方是否在假笑或言行不一。

#### 模块 4：具身空间危险与负向可供性 (`engine/spatial_affordance.py`) `[纯代码 Tier 0]`
*   **核心类**：`NegativeAffordanceField`, `SpatialHazardDetector`
*   **契约**：断崖落差急停减速度计算 $a_{req} = \frac{v^2}{2(d - d_{safe})}$，近体学警惕度计算，声学反平方衰减。

#### 模块 5：生理内稳态与躯体相变 (`engine/homeostasis.py`) `[纯代码 Tier 0]`
*   **核心类**：`HomeostasisState`, `HomeostasisEngine`
*   **契约**：体温对流散失微分步进，异位稳态负荷计算，相变状态机（理性 $\to$ 警戒 $\to$ 恐慌 $\to$ 绝望 $\to$ 崩溃），Prompt 约束生成。

#### 模块 6：端云级联路由器 (`engine/cascading_router.py`) `[调度层 Tier 1-3]`
*   **核心类**：`CascadingDecisionRouter`
*   **契约**：依据信息熵 $H(P) > \theta$ 与心理突变 $\Delta S > \delta$ 决定留在本地 Tier 1/2 还是上抛云端 Tier 3；长考期间输出掩护动作。

---

### 五、 纯步骤式工程实施路线 (Sequential Implementation Steps)

本路线严格依循计算复杂系统的单向因果依赖推进，不预设任何时间阶段或研发周期。每一步骤必须在前序步骤所有单元测试完全通过后方可启动：

*   **步骤 1：具身物理感知与空间负向可供性底座**
    *   *交付模块*：`engine/spatial_affordance.py`
    *   *验证断言*：`tests/test_spatial_affordance.py` 全绿（断崖探地减速度急停、近体学对数警惕度、隔墙声学介质衰减）
*   **步骤 2：生理内稳态微分方程与躯体标记偏置**
    *   *交付模块*：`engine/homeostasis.py`
    *   *验证断言*：`tests/test_homeostasis.py` 全绿（体温传导微分步进、剧痛动作效用扣减、极寒打颤 Prompt 拦截）
*   **步骤 3：全局宏观世界模型与动态天气灾害引擎**
    *   *交付模块*：`engine/macro_world_model.py`
    *   *验证断言*：`tests/test_macro_world_model.py` 全绿（纯代码劳伦兹混沌天气平滑流转、温湿度网格能量水分守恒、雷火与洪涝客观触发）
*   **步骤 4：次等 NPC 与动物生态子系统**
    *   *交付模块*：`engine/sub_npc_animal.py`
    *   *验证断言*：`tests/test_sub_npc_animal.py` 全绿（草食动物 300° 警戒与避险、肉食动物下风向气味追踪与半月围捕、Boids 集群惊散算法，0 LLM 调用）
*   **步骤 5：NPC 端侧微脑与小模型直觉反应系统**
    *   *交付模块*：`engine/npc_micro_brain.py`
    *   *验证断言*：`tests/test_npc_micro_brain.py` 全绿（Jev 微模型 <5ms 动作分布输出、FACS 微表情代码生成、多模态通道不一致性测谎）
*   **步骤 6：动态环境元胞物理与毫秒级动作栈抢占**
    *   *交付模块*：`engine/cellular_hazard.py`
    *   *验证断言*：火势扩散元胞推进，突发灾害毫秒级压栈挂起当前工作，逃生栈执行完毕后弹出恢复
*   **步骤 7：托尔曼主客观认知脑图与信息滞后差**
    *   *交付模块*：`engine/cognitive_map.py`
    *   *验证断言*：客观地图与主观脑图解耦，网格细胞航位推算高斯漂移，断桥目击触发 100% 预测误差就地重寻路
*   **步骤 8：端云级联决策路由器构建**
    *   *交付模块*：`engine/cascading_router.py`
    *   *验证断言*：`tests/test_cascading_router.py` 全绿（常态事件由 Tier 0-2 消化，心理突变抛出 Tier 3 云端，长考期输出掩护动作）
*   **步骤 9：极简心智 (D-T-S) 与抗谄媚对抗元指令**
    *   *交付模块*：`engine/minimalist_mind.py`
    *   *验证断言*：固化 Drive-Taboo-State 模板，注入对抗性元指令，测试验证诱导话术无法击穿 NPC 核心禁忌
*   **步骤 10：主观时间弹性流动与生化昼夜睡眠节律**
    *   *交付模块*：`engine/chronoception.py`
    *   *验证断言*：`tests/test_chronoception.py` 全绿（恐惧状态主观时钟流速膨胀，熬夜腺苷负荷积累致使失误率单调上升）
*   **步骤 11：REM 潜意识梦境解构与直觉先验反哺**
    *   *交付模块*：`engine/dream_engine.py`
    *   *验证断言*：无监督注意力矩阵聚类白天记忆，完成凝缩与置换，次晨生成直觉启发式先验注入决策偏好
*   **步骤 12：柯尔伯格道德价值张量与麦克白效应状态机**
    *   *交付模块*：`engine/moral_engine.py`
    *   *验证断言*：`tests/test_moral_engine.py` 全绿（道德投影效用计算，违规后失调压力激增，驱动洗手/祈祷/捐赠补偿）
*   **步骤 13：宏观离散 PID 自动化央行与生产死锁打破**
    *   *交付模块*：`engine/cybernetic_economy.py`
    *   *验证断言*：`tests/test_cybernetic_economy.py` 全绿（离散 PID 印花税调控平抑 CPI，Tarjan 算法 100% 检出并解开死锁环）
*   **步骤 14：三级营养级生物圈与农耕逆向反馈**
    *   *交付模块*：`engine/biosphere.py`
    *   *验证断言*：植被-食草-食肉 Lotka-Volterra 季节方程自平衡，草食动物过度被捕自发触发狼群袭村，GDD 积温农耕生长
*   **步骤 15：空间化跨物种 SEIR 疫病传染与隔离规约**
    *   *交付模块*：`engine/epidemics.py`
    *   *验证断言*：野生宿主跨物种传播至 NPC，空间网格人群扩散，村落封控政策使传染率压制 80%
*   **步骤 16：生命历程演化、Gompertz 衰老死亡与宗族传承**
    *   *交付模块*：`engine/demographics.py`
    *   *验证断言*：儿童皮亚杰阶段推进，老年内稳态与记忆加速衰退，Gale-Shapley 阶层稳定匹配，血亲仇恨权重继承
*   **步骤 17：政治暴力垄断度量与贝叶斯司法审判链**
    *   *交付模块*：`engine/politics_jurisprudence.py`
    *   *验证断言*：暴力垄断指数跌破阈值触发治安熵增暴乱，法律三元组检索，贝叶斯证据融合与证词余弦矛盾交叉质询
*   **步骤 18：组合式技术树 DAG 偶发创新与微观 Bass 扩散**
    *   *交付模块*：`engine/combinatorial_tech.py`
    *   *验证断言*：异构 DAG 技术依赖，材料匮乏 Serendipity 替代推断新配方，社交网络拓扑 Bass 方程呈现 S 型采纳曲线
*   **步骤 19：确定性事件溯源沙盒与 SIQ 社会智能度量**
    *   *交付模块*：`engine/deterministic_eval.py`
    *   *验证断言*：全局种子 RNG 与 SHA-256 缓存保证万步事件回放位对齐，自动化输出行为一致性得分 (BCS) 与 SIQ 诊断表
*   **步骤 20：全系统大一统闭环运转与极限压测**
    *   *交付模块*：`engine/world_loop.py`
    *   *验证断言*：具身、心智、调度、生态与文明全链路长周期无干预自洽运转，零死锁崩溃，物价自律，生态平衡

---
*任务书编制完毕。纯粹以因果依赖步骤指导工程推进！*
