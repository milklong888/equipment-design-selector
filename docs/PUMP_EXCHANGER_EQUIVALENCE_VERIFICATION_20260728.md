# 泵与换热器完整保底及等价方案验证（2026-07-28）

## 1. 验证结论

本轮把“缺条件也必须给出完整程序候选，但逐项标注缺口”落实到泵和换热器输出链，并修正了两类不合理行为：

1. 普通泵工况不再为了贴近某个 GB/T 5662 参考点而人为拆成多台串联。
2. `4000 m³/h、60 m` 不再仅凭大流量判断为轴流泵，改为“立式导叶式混流泵”程序工程路线。

换热器和泵现在都会同时输出：

- 主推荐的完整程序工程规格；
- 可比较的替代方案；
- 走过的型式、材料、密封、承压和串并联分支自然文字；
- 算术/总量守恒项；
- 尚未证明的热工水力或系统曲线等价项；
- J 类、`provisional`、`TYPE_SCREENING` 警告和正式证据门。

本验证使用确定性算法夹具，不冒充 Aspen BKP 或厂家复核。Aspen BKP 的实用抽取与重运行验证另见 `ASPEN_SMOKE_SUITE_VERIFICATION_20260728.md`。

## 2. 实测用例

| 用例 | 输入摘要 | 程序主推荐 | 结论 |
|---|---|---|---|
| `E-MISSING` | 只声明 `HEATX` 和液相 | 1 台固定管板式管壳流程换热器；`STHE-FT-1S2T-A19.6-D25-L3000-N84-Q345R-10` | 完整候选已形成；100 kW、U、LMTD、材料和结构等缺项逐字段标为保底 |
| `E-LARGE` | 50,000 kW，U=450 W/(m²·K)，LMTD=20 K | 4 列并联×每列 4 台串联，共 16 台；单台 3,125 kW、408.497 m²；`STHE-FT-1S2T-A408.5-D25-L3000-N1734-Q345R-10` | 同时给出 14 台全并联和 14 台全串联方案；三方案均保持总面积与总负荷守恒 |
| `P-MISSING` | 只声明泵和液相 | 1 用 1 备轴向吸入离心泵；`PES-END-SUCTION-1ST-Q10.000-H30.000-P1S1`；PN16 | Q=10、H=30、密度、温度和吸入口压力均有明确保底/警告；泵壳、叶轮、轴、密封和垫片不留空 |
| `P-NORMAL` | 水，120 m³/h，60 m | 1 用 1 备轴向吸入离心泵；`PES-END-SUCTION-1ST-Q120.000-H60.000-P1S1`；PN16 | 保持单泵，修复了旧逻辑可能给出 3 台串联的问题 |
| `P-HIGH-Q` | 4,000 m³/h，60 m | 2 台立式导叶式混流泵并联运行、1 台备用；单台 2,000/60；`PMF-VERTICAL-DIFFUSER-1ST-Q2000.000-H60.000-P2S1`；PN16 | 型式和数量具体；另给 1 台 4,000/60 的大机组比较方案 |
| `P-HIGH-H` | 120 m³/h，800 m，ρ=850 kg/m³ | 1 用 1 备卧式双壳体多级离心泵（BB5 类工程型式）；内部 10 级估算；`PMS-BB5-DOUBLE-CASING-10ST-Q120.000-H800.000-P1S1`；PN100 | 不再写“厂家型号待定”；厂家曲线、转子动力学、末级承压和密封仍为正式门 |

## 3. 等价方案合理性判断

### 3.1 泵

程序只把并联流量分配和串联扬程相加视为算术闭合，不把它当成真实系统曲线等价。美国能源部泵系统资料明确指出，并联泵实际工作点取决于系统曲线，两个相同泵并联并不会在一般系统中自动得到两倍流量；并联机组还应匹配型号并核对 BEP、控制和全曲线。因此当前输出保留厂家 `Q-H-η-功率-NPSHr`、系统曲线及全部运行组合复核门是必要的：

- https://www1.eere.energy.gov/manufacturing/tech_assistance/pdfs/pump.pdf
- https://www1.eere.energy.gov/manufacturing/tech_assistance/pdfs/38945.pdf

`4000 m³/h、60 m` 改走混流泵而不是轴流泵更合理。KSB 的泵技术资料把混流泵描述为径流与轴流之间的过渡型式，并指出单级混流泵扬程可达约 60 m；因此本工况适合进入混流泵询价/厂家复核路线，但这仍不是厂家型号结论：

- https://www.ksb.com/en-global/centrifugal-pump-lexicon/article/mixed-flow-pump-1117182

`120 m³/h、800 m` 用一台内部多级 BB5 类工程型式优先于多台独立泵外部串联，程序给出的 10 级只是按 80 m/级登记值估算。实际级数、关死扬程、轴向力、转子动力学、壳体和末级承压必须由厂家闭合。

### 3.2 换热器

50 MW 用例的 4×4 主方案解决了“14 条并联总管过多”和“14 台全串联压降过大”之间的结构性冲突，可作为模块化工程评审起点；它不是热工最优解。程序同时保留：

- 4 并×4 串：受限并联列数的主比较方案；
- 14 并×1 串：降低单列串联压降，但总管和流量分配风险最高；
- 1 并×14 串：温度程序分段能力强，但全流量串联压降和控制耦合最高。

Alfa Laval 的换热器资料说明，换热器大小不仅由热负荷决定，可允许压降同样影响选型；并联板式换热器还可能出现流量分配不均。虽然本程序的主对象是管壳式换热器，这两项基本约束同样说明“面积守恒”不能代替温度程序、两侧压降和总管分配核算：

- https://www.alfalaval.com/industries/hvac/hvac-consultant-portal/faq/
- https://www.alfalaval.com/service-and-support/product-services/plate-heat-exchanger-services/troubleshooting-for-plate-heat-exchangers/

因此程序将所有三个方案标为 `thermal_hydraulic_equivalence_proven: false`，要求用同工况 EDR 或等效热工软件复核 LMTD/F、污垢热阻、相变分区、两侧压降、总管分配和 GB/T 151 机械设计。

## 4. 自动验证

- 泵/换热器定向测试：43 项通过。
- 展示链、分支自然文字和等价方案输出合并测试：53 项通过。
- 非窗口全量回归：413 项执行，其中 411 项通过、2 项按环境条件跳过。
- 无界面 Agent 自检：`PASS`。
- 23 个核心源码 SHA-256 清单：`PASS`。

本轮另外对实际目录版成品而非仅对源码做了复验：

- 成品目录：`D:\equipment-selector-release-20260728-v2`；
- 打包时源码快照校验：23/23 文件通过；工作区与打包快照的核心源码集合 SHA-256 均为 `0B17DFF6D3D9D12A0A6ADD3BC2D971C4F65F7DB8DC1A491183C2643745FCF3B9`；
- 打包后 Agent/CLI 自检：退出码 0，17/17 项通过，`status=PASS`；
- 使用打包后的 Agent/CLI 重算本报告 6 个泵/换热器案例：退出码 0，`operation=manual_batch`，`count=6`，全部形成完整程序候选；
- 目录版 GUI 隐藏冷启动 12 秒后进程仍存活，测试完成后只终止本次测试进程。

当前便携源码解释器仍缺完整 Tcl/Pillow，因此没有强行把源码解释器的窗口依赖问题包装成算法问题；实际交付包已包含可启动运行时，并已通过上述成品启动冒烟。

## 5. 可复核位置

- 核心算法：`scripts/equipment_design_match.py`
- 泵型终选规则：`knowledge_graph/equipment_model_recommendation_rules.json`
- 完整性与等价守恒测试：`app/tests/test_engineering_adjustment_workbench.py`
- 用户可见分支输出：`app/result_presentation.py`
- 展示链测试：`app/tests/test_result_presentation.py`
- 核心源码清单：`app/source_code_manifest.json`
- 成品六案请求：`tmp/packaged_pump_exchanger_equivalence_request_20260728.json`
- 成品六案完整响应：`tmp/packaged_pump_exchanger_equivalence_response_20260728.json`
- 成品 Agent 自检响应：`tmp/packaged_agent_v2_selftest_20260728.json`
