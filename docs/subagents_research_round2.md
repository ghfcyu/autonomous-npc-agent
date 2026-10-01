# 第二轮深度攻关子代理原始研究成果汇总

本文档保存了针对 V2 缺陷攻坚派出的 4 位顶级专家子代理的深度算法模型与设计细节。

---

## 1. 具身物理感官引擎与感知过滤系统 (Sensory Physics)
- **声学光线追踪 (Acoustic Raycasting)**：
  - 基础方程：$I_r = \frac{I_0}{4 \pi d^2}$
  - 介质与墙体衰减：$I_{final} = I_r \cdot \prod_{i=1}^{n} \tau_i$（空气 $\tau=1.0$，混凝土墙 $\tau=0.1$）
  - 窃听半径：耳语 2m (30dB)，常态 10m (60dB)，呼喊 50m (90dB)。
- **视觉物理学**：
  - 视锥模型：$\frac{\vec{F} \cdot \vec{D}}{|\vec{F}||\vec{D}|} \ge \cos(\frac{\theta_{FOV}}{2})$
  - 光照与遮挡：$V_{target} = (1 - O) \cdot (L_{ambient} + \sum L_{lights})$
  - 注意力显著性图 (Saliency Map)：$S = w_1 \cdot \text{Motion} + w_2 \cdot \text{Contrast} + w_3 \cdot \text{TaskRelevance}$。
- **感知数据包 (Perceptual Packet)**：离散化 Token + 战争迷雾防作弊（State Fog-of-War），杜绝全图透视。
- **空间语义可供性 (Spatial Affordance Engine)**：NavMesh $D_{path}$ 连通性校验，IK 抓取包围盒重叠检验，阻断不可行指令。

---

## 2. 端云协同工程架构与级联决策路由 (Edge-Cloud & Cascading Router)
- **三级级联流水线**：
  - L0：规则脚本与物理状态机（0-1ms，肌肉记忆、避障、反射）
  - L1：本地微型判别模型/SLM（<5ms，0.5B-1.5B Qwen/TinyLlama，ONNX/CoreML/GGUF Q4）
  - L2：云端高阶大模型（异步，HTN长线规划、深度反思、ToM）
- **升级触发量化指标**：
  - 信息熵：$H(P) = - \sum P(y_i) \log P(y_i) > \theta_{entropy}$
  - 心理状态突变 Delta：$\Delta S = ||S_t - S_{t-1}||_2 > \theta_{psycho}$
  - 物理事件严重度：$W(E) \ge W_{critical}$
- **资源与内存控制**：
  - GGUF 4_K_M 量化（0.5B 约 350MB，1.1B 约 700MB，总占用 < 1.5GB RAM）。
  - 权重共享 (Weight Sharing) 与多 NPC 共享推理，KV Cache 锁定在 256-512 tokens。
- **异步解耦协议**：
  - Protobuf + 双向 gRPC 流式数据契约。
  - 掩护动作 (Cover Actions)：等待云端响应的 1-2 秒内，L1 生成“挠头、发呆、抽烟、环顾四周”掩护动作。
  - 目标导向注入：云端返回 HTN 抽象节点而非物理摇杆指令。

---

## 3. 复杂系统经济控制、抗死锁与确定性评测 (Economic Control & Benchmarks)
- **中央银行 PID 控制器**：
  - $U(t) = K_p e(t) + K_i \sum e(\tau)\Delta t + K_d \frac{e(t)-e(t-1)}{\Delta t}$
  - 水龙头（降税、注资）与水槽（印花税、回收）自平衡物价。
  - 资源刷新 Sigmoid 阻尼：$R_{spawn}(t) = R_{base} \cdot [1 - \frac{1}{1 + e^{-k(M(t) - M_{threshold})}}]$。
- **资源配置死锁检测 (RAG & Tarjan)**：
  - 有向二分图 $G = (N \cup R, E)$，Tarjan 环检测。
  - 天降神迹机制：计算打断成本，随机在死锁链中刷新关键工具，强行解开循环等待。
- **确定性仿真保障**：
  - Temperature=0, Top_p=1, Seeded RNG 固定。
  - `SHA-256(Prompt + SystemContext)` 结果持久化缓存。
  - 事件溯源 (Event Sourcing Replay)：只存不可变事件元组，回放速度达实时千倍。
- **社会智能度量基准**：
  - 行为一致性得分 (BCS)：CosineSimilarity(Persona, Action) 随时间衰减。
  - 社会熵 (Social Entropy)：$H(S) = - \sum p_i \log_2 p_i$，防止社会阶层绝对板结。
  - 社会智能商 (SIQ)：心智理论准确率 + 帕累托最优协同率。

---

## 4. 文化演化、模因突变与宗教涌现 (Cultural Evolution & Memetics)
- **模因 (Meme) 数据结构**：
  - 属性：virality（传播力）, mutation_rate（变异率）, persuasiveness（信服度）, behavior_penalty（行为惩罚/消耗）。
  - 社交传播与变异：选择性关注复制，Prompt 上下文截断引发脑补（Confabulation）变异。
  - 自然选择：根据行为惩罚与 NPC 生存资源计算适应度（Fitness）。
- **宗教与迷信涌现 (HADD 模型)**：
  - 施动性过度探测 (Hyperactive Agency Detection Device)：罕见灾难导致预测误差飙升，因果推断过度拟合（Causal Overfitting）生成神秘学神话假说。
  - 昂贵信号理论 (Costly Signaling)：高耗能仪式（祭祀、禁食）成为群体忠诚标签。
- **语言与俚语演化**：
  - 最省力原则与词向量局部漂移（Local Word Embedding Drift）。
- **信念动力学模型**：
  - 微观贝叶斯更新：$P(Belief | Evidence) \propto P(Evidence | Belief) \times P(Belief)$。
  - 宏观 DeGroot 共识模型：$X(t+1) = W \times X(t)$。
  - 有界置信度 (Bounded Confidence)：当理念差异过大时信任度降为 0，数学上自然分裂为极端对立教派，涌现宗教冲突。
