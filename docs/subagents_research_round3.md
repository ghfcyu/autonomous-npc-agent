# 第三轮专项攻坚子代理原始研究成果汇总

本文档保存了关于【NPC物理空间危险感知、负向可供性、躯体标记反馈、内稳态及认知地图】的深度算法建模。

---

## 1. 空间危险实体与负向可供性场 (Spatial Hazards & Negative Affordances)
- **负向可供性张量场 (Negative Affordance Tensor Field)**：
  $\mathcal{H}(\mathbf{p}, t) = [c(\mathbf{p}), v(\mathbf{p}), d(\mathbf{p}), \mu(\mathbf{p})]^T$
  - 墙壁：拓扑连通代价 $c(\mathbf{p}) \to \infty$。
  - 断崖/深坑：重力高程突变 $\nabla h(\mathbf{p}) \ll -h_{fatal}$。
  - 沼泽/深水：速度阻尼 $v(\mathbf{p}) = e^{-k \cdot \eta}$ 与窒息积分 $S = \int \sigma(depth) d\tau$。
  - 火海/尖刺：伤害密度分布 $\mu(\mathbf{p})$。
- **动态 NavMesh 切割与代价注入**：
  $M_{walkable}(t) = M_{base} \setminus \bigcup Projection(O_i(t))$。软危险通过修改 Cost Field 注入。
- **人工势场与 L0 级刹车反射**：
  $\mathbf{F}_{rep}(\mathbf{p}) = \eta (\frac{1}{d} - \frac{1}{d_0})\frac{1}{d^2}\nabla d$。
  制动动力学：$a_{req} = \frac{v_0^2}{2(d - d_{safe})}$，超限则发生滑落表现。
- **PhysX/Havok 碰撞层**：`LAYER_HAZARD_SOLID`, `LAYER_HAZARD_VOLUME`, `LAYER_HAZARD_ABYSS` 与 Sphere Sweep 查询。

---

## 2. 躯体标记假说与内稳态动力学 (Somatic Markers & Homeostasis)
- **躯体感觉向量**：$S_t = [s_{pain}, s_{cold}, s_{hypoxia}, s_{gforce}, s_{claustro}]^T$。
- **决策偏置惩罚 (Somatic Valuation Modulation)**：
  $V_{actual}(a) = V_{logical}(a) - \sum w_i \cdot s_i \cdot Cost_i(a)$，本能绕过纯逻辑接管控制。
- **核心生理内稳态向量**：$H_t = [h_{temp}, h_{energy}, h_{hydro}, h_{integ}, h_{oxy}]^T$。
- **环境致失衡微分方程**：体温热对流散失 $\frac{dh_{temp}}{dt} = C_{meta}\dot{Q} - \kappa(h_{temp} - E_{temp})$。
- **异位稳态负荷 (Allostatic Load)**：$L_t = \sum \omega_i (\frac{H^*_i - H_{t,i}}{H^*_i})^n$。
- **心理状态三元突变方程**：
  - $\frac{d(Stress)}{dt} = \alpha_1 L_t + \alpha_2 \max(0, \frac{dL_t}{dt}) - \beta_1(Stress - Stress_{base})$
  - $\frac{d(Arousal)}{dt} = \gamma_1 s_{pain} - \gamma_2 (100 - h_{energy}) Arousal$
  - $\frac{d(Mood)}{dt} = -\delta_1 L_t - \delta_2 \int s_{claustro} d\tau$
- **相变临界状态机 (Phase Transition)**：
  `理性态 (Rational)` -> `警戒态 (Stressed)` -> `恐慌求生 (Panic)` -> `绝望耗竭 (Despair)` -> `机制崩坏 (Collapse)`。
- **极端生理 Prompt 动态注入**：极寒下肢体僵硬与语言打颤拦截；枯井幽闭时前额叶抑制与绝望呼救模式。

---

## 3. 动态环境演化与毫秒级抢占 (Dynamic Environment & Preemptive Interrupts)
- **元胞自动机 (CA) 物理模拟**：
  - 火灾反应-扩散方程：$\frac{\partial T}{\partial t} = \alpha \nabla^2 T - \vec{v}_{wind}\cdot \nabla T + \dot{q}_{comb} - h(T - T_\infty)$。
  - 水淹与烟雾对流扩散：$\frac{\partial C}{\partial t} = \nabla\cdot(D\nabla C) - \nabla\cdot(\vec{v}C) - S_{sink}$。
  - 结构承重破坏依赖图与多米诺骨牌级联坍塌。
- **L0-L1-L2 抢占架构**：
  - 物理事件触发多播 -> L1 评估威胁度 -> 产生 `SYS_INTERRUPT` 信号。
- **任务丢弃与动作栈压栈**：
  - 当前目标序列化为 `SuspendedGoal` 压栈，打断当前不可逆动画，解绑并丢弃手持物（如铁锤）。
  - 压入紧急逃生目标（Sprint + Flee），危机解除后评估原工作台完好度，决定恢复或重规划。
- **元素表面反应矩阵 (类似《博德之门3》)**：火 + 水 -> 蒸汽云（视线遮蔽），毒 + 火 -> 大爆炸（气浪击退）。

---

## 4. 认知空间地图与主客观信息差 (Cognitive Map & Information Gap)
- **主客观解耦**：客观物理图 $G_{obj}$ 与主观认知图 $G_{subj}$ 严格隔离。
- **海马体计算抽象**：
  - 位置细胞 (Place Cells)：语义拓扑顶点（如客栈、怪物巢穴），带情感效价 $Valence \in [-1, 1]$ 与置信度 $Confidence$。
  - 网格细胞 (Grid Cells)：度量坐标系，带航位推算高斯漂移 $N(0, \sigma^2 \Delta t)$（产生“路痴/迷路”机制）。
- **寻路综合代价**：$C = w_1 \cdot Distance - w_2 \cdot Valence + w_3 \cdot (1 - Confidence)$。
- **三阶滞后性更新**：
  - 零阶：目击者直接视线更新（置信度 1.0）。
  - 一阶+：传闻二阶滞后，根据信任度进行贝叶斯更新。
  - 未知者：按旧图导航，到达断桥现场时**预测误差激增 (Surge PE)**：$PE = ||S_{obs} - S_{exp}|| \times Importance$，触发震惊、愤怒抓狂与即时重规划。
- **恐慌性寻路 (Panic Pathfinding)**：遭遇致命危机时关闭全局 A*，降级为局部贪心势场法（隧道视觉），极易被逼入死胡同。
