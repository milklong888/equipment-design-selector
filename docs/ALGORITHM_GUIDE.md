# 算法源码导航：从输入到具体设备结果

这份文档回答两个问题：

1. 真正执行计算和选型的算法在哪个文件；
2. 文件开头大量字段、常量和规则定义，最终怎样变成一个具体设备结果。

最重要的结论：

- 人工输入总入口：`app/app_core.py::manual_match`；
- Aspen 数据总入口：`scripts/aspen_equipment_derivation.py::derive_bundle`；
- 单台设备运行时算法总入口：`scripts/equipment_design_match.py::match_one`；
- 公式执行中心：`scripts/equipment_design_match.py::run_calculations`；
- `scripts/equipment_calc.py` 提供基础公式，同时承担独立计算台账/教材公式审计；它不是 GUI 单台选型的总入口。

如果只想理解程序怎样给出一个结果，请先读 `match_one`，不要从文件第一行的大型字段表开始逐项读。

## 1. 最短阅读路线

1. [`app/app_core.py::manual_match`](../app/app_core.py#L882)：人工输入怎样进入算法；
2. [`scripts/equipment_design_match.py::match_one`](../scripts/equipment_design_match.py#L7713)：单台设备完整执行链；
3. [`scripts/equipment_design_match.py::run_calculations`](../scripts/equipment_design_match.py#L3605)：公式如何按设备族执行；
4. [`build_model_recommendation`](../scripts/equipment_design_match.py#L6788)：怎样从计算结果筛选具体型式；
5. [`build_engineering_adjustment_plan`](../scripts/equipment_design_match.py#L5976)：设备过大、泵工况异常或单台不合理时怎样给出数量与并串联方案；
6. Aspen 项目再读 [`derive_bundle`](../scripts/aspen_equipment_derivation.py#L10701)；
7. 管线再读 [`derive_piping`](../scripts/aspen_equipment_derivation.py#L8814) 和 [`build_programmatic_pipe_specification`](../scripts/aspen_equipment_derivation.py#L7272)。

## 2. 主执行链

### 2.1 人工输入

```text
app_core.manual_match
  → equipment_service_profile.build_manual_service_profile
  → equipment_design_match.match_one
      → normalize_record
      → match_family
      → validate_parameters
      → prepare_family_effective_inputs
      → apply_design_fallbacks
      → run_calculations
      → build_design_parameter_package
      → determine_model_status
      → build_model_recommendation
      → build_engineering_adjustment_plan
      → build_progress
  → connection_component_selection.build_manual_connection_component_selections
  → customer_delivery / result_presentation
```

### 2.2 Aspen 输入

```text
aspen_com_import（从 Aspen 副本读取）
  → aspen_equipment_derivation.derive_bundle
      → normalize_stream / normalize_block
      → enrich_stream_viscosities
      → run_gate（收敛、错误、警告门）
      → derive_equipment（Aspen 模块）
          → service_profile
          → equipment_design_match.match_one
          → connection component selector
      → derive_piping（物料流股/物理 PIPE 模块）
          → hydraulic preselection
          → verified DN / OD / wall / PN data
          → programmatic pipe specification
      → 来源链、哈希和公开投影刷新
  → customer_delivery / result_presentation
```

Aspen 层不自行发明另一套设备选型算法。它负责把 Aspen 的流股、模块、拓扑、单位和运行状态转换为 `match_one` 能消费的统一参数。

## 3. 每个算法文件具体做什么

| 文件 | 是否直接决定工程结果 | 具体职责 | 主要入口 |
| --- | --- | --- | --- |
| `scripts/equipment_design_match.py` | 是，主算法 | 单位规范化、设备族识别、缺项检查、公式执行、具体型式候选、证据门、数量/并串联和超界修改方案 | `match_one`, `run_calculations`, `build_model_recommendation` |
| `scripts/equipment_calc.py` | 是，基础公式；同时含离线审计 | 管径、流速、设计压力、筒体/封头厚度、泵功率、压比、塔底持液高度、膜面积、传热和 Ergun 压降等公式 | 文件前部纯函数；`build_calculations` 是独立台账入口 |
| `scripts/aspen_equipment_derivation.py` | 是，Aspen/管线推导层 | 统一 Aspen 单位和字段、校验收敛状态、补充受控黏度、建立模块/流股来源链、推导设备和未显式建模的管线 | `derive_bundle`, `derive_equipment`, `derive_piping` |
| `scripts/equipment_service_profile.py` | 是，工况标签层 | 从真实温压、相态、组成、流股方向和模块类型生成工况画像；拒绝用户直接写入“腐蚀/有毒”等标签冒充事实 | `build_aspen_service_profile`, `build_manual_service_profile` |
| `scripts/connection_component_selection.py` | 是，连接部件层 | 校验连接选择包，绑定同一流股和同一组成哈希，选择法兰型式、密封面、垫片和紧固件路线 | `build_aspen_connection_component_selections` |
| `app/viscosity_fallback.py` | 是，但只能初筛 | Aspen 未给黏度时，按来源锁定的纯组分模型和混合规则估算单相黏度；禁止两相估算和越界外推 | `estimate_stream_viscosity` |
| `app/database_authority.py` | 不计算设备尺寸，但控制数据可用性 | 决定 RAG 库和结构化标准库是否为活动版本，检查大小、SHA-256、表结构、记录数和数据集状态 | `verify_consumer_database` |
| `app/app_core.py` | 只编排 | 给 GUI/CLI 提供统一入口，组织输入、算法调用、知识检索和客户输出 | `manual_match`, `auto_match`, `knowledge_search` |
| `app/customer_delivery.py` | 否，只投影 | 把机器结果整理为客户设备一览表、数据表和证据索引，不重新计算 | 客户交付构建函数 |
| `app/result_presentation.py` | 否，只展示 | 把同一机器结果渲染成 GUI、HTML、Markdown 和 Agent 组织答案 | 展示构建与渲染函数 |
| `app/llm_bridge.py` | 否 | 让 LLM 解释或在登记选项中协作；不能改写公式数值、证据状态或未登记型号 | 混合执行与供应商调用函数 |

## 4. `equipment_design_match.py` 内部怎样工作

这个文件很长，是因为它把 17 个设备族放在同一套可靠性框架中。可分成八段：

| 阶段 | 函数 | 做什么 |
| --- | --- | --- |
| 输入规范化 | `normalize_record` | 把中英文别名和带单位数值映射为规范字段；记录冲突和未识别字段 |
| 设备族识别 | `match_family` | 按显式设备族、Aspen 模块类型、设备名称别名和工艺功能评分；含糊时不强行选一个 |
| 输入检查 | `validate_parameters` | 检查压力基准、相态、数值有限性、范围和必要物理条件 |
| 受控默认 | `apply_design_fallbacks` | 只对初步设计补登记的缺省设计基础并标记 `J/provisional`；正式证据包存在时不覆盖 |
| 公式执行 | `run_calculations` | 按设备族登记的 `calculation_rules` 执行公式，生成结果、待补输入和派生参数 |
| 参数包 | `build_design_parameter_package` | 将计算值、输入、来源、字段状态和选择特征向量装入固定机器结构 |
| 具体型式 | `build_model_recommendation` | 读取型式规则和权威图谱，执行硬约束、谓词和证据门，生成登记的具体终端型式候选 |
| 超界调整 | `build_engineering_adjustment_plan` | 单台不合理时计算数量、备用、并联/串联/分列和单台目标工况；保留算法警告 |

`match_one` 严格按这个顺序调用。文件开头的字段别名、数值字段和设备族映射是算法词典，不是最终结果；真正开始执行的是 `match_one`。

## 5. 公式具体在哪里

### 5.1 可复用基础公式

在 `scripts/equipment_calc.py` 文件前部：

| 函数 | 计算内容 |
| --- | --- |
| `pipe_required_diameter` | 由体积流量和目标速度求所需内径 |
| `pipe_actual_velocity` | 由流量、外径和壁厚反算实际速度 |
| `design_pressure` | 操作压力乘设计系数 |
| `cylinder_calc_thickness` | 内压圆筒计算厚度 |
| `ellipsoidal_head_calc_thickness` | 2:1 椭圆封头计算厚度 |
| `pump_hydraulic_power_kw` | `ρgQH` 液压功率 |
| `pump_shaft_power_kw` | 液压功率除以泵效率 |
| `pressure_ratio` | 出口绝压与入口绝压之比 |
| `tower_bottom_liquid_height` | 由流量、停留时间和塔径求塔底液位高度 |
| `membrane_area_m2` | 圆柱通道膜面积 |
| `overall_u_outer_area` | 多层热阻合成总传热系数 |
| `ergun_pressure_drop` | 填充床 Ergun 压降 |

### 5.2 运行时公式调度

`scripts/equipment_design_match.py::run_calculations` 读取每个设备族的 `calculation_rules`，再调用上述基础公式或在该函数中执行设备族专用公式。主要包括：

- 压力基准和设计压力；
- 容器/塔器直径、高度、容积和壁厚初算；
- 泵扬程、液压功率、轴功率、NPSH 约束；
- 压缩比和压缩机/风机边界；
- 换热负荷、温差和面积初算；
- 管径、标准 DN/外径、实际流速、雷诺数和压降初筛；
- 反应器容积、停留时间及部分反应/填充床路线；
- 过滤、干燥、结晶、膜和萃取设备的登记初筛公式。

每次执行都生成 `equipment-formula-trace-v1`，包括表达式、代入式、输入值、单位、来源、代码位置、适用范围和双 SHA-256。

## 6. 管线算法单独说明

管线不是简单“有流股就给一个 DN”。主链在 `scripts/aspen_equipment_derivation.py`：

1. `derive_piping` 判断流股是物理管线、端点状态还是 Aspen 逻辑连接；
2. `apply_pipe_hydraulic_preselection` 根据相态、流量、密度、黏度和目标速度计算初始直径；
3. `_pipe_hydraulic_screening_metrics` 计算实际速度、雷诺数、Darcy 摩阻系数和每 100 m 压降；
4. 压降过大时 `_pipe_pressure_gradient_required_diameter_mm` 增大候选直径；
5. `load_verified_pipe_standard_store` 只读取活动数据库中 `CURRENT + VERIFIED + DIRECT_REUSE_VERIFIED` 的 PN 和尺寸数据集；
6. `build_programmatic_pipe_specification` 形成具体 DN、外径、壁厚、PN、初步材料/制造路线、腐蚀裕量、数量和警告；
7. 正式管道等级、材料压力—温度额定和产品标准尚未闭合时，结果保持项目权威开放门。

黏度若来自内置公式，管线结果会带 `internal_correlation_used=true` 和预设计警告，不会伪装成 Aspen 实测物性。

## 7. 算法规则文件

Python 负责执行逻辑，以下 JSON/图谱负责声明“有哪些设备族、需要哪些字段、哪些具体型式允许成为候选”：

| 文件 | 作用 |
| --- | --- |
| `knowledge_graph/equipment_match_rules.json` | 设备族别名、输入字段、计算规则和基础匹配条件 |
| `knowledge_graph/equipment_model_recommendation_rules.json` | 17 个设备族的具体终端型式、谓词、约束和受控默认策略 |
| `knowledge_graph/equipment_parameter_chain_templates.json` | 各设备族参数链模板和字段职责 |
| `knowledge_graph/equipment_customer_output_profiles.json` | 客户一览表/数据表对每个设备族的字段投影 |
| `equipment_selection_graph/equipment_selection_graph_v2.json` | 设备族、标准路线、型号来源、证据门和图谱关系 |

代码和规则必须同时存在。只改 Python、不更新规则哈希，或只改规则、不更新测试和源码/运行时清单，都不能视为有效算法版本。

## 8. 哪些文件不是算法

- `app/schemas/*.json`：只约束输入输出结构；
- `app/tk_gui.py`、`app/static/*`：只负责界面；
- `app/result_presentation.py`：只负责展示；
- `docs/*`：说明和审计记录；
- `app/tests/*`：验证算法行为；
- `data/*.csv`：小型公开目录数据，不是公式实现；
- `knowledge_graph/standards_graph/*`：RAG/标准证据载荷，不等于设备计算算法。

## 9. 用一个泵例子定位代码

人工输入流量、扬程、密度、效率和 NPSH 后：

1. `app_core.manual_match` 接收字段；
2. `match_one` 规范化单位并识别 `family_pump`；
3. `run_calculations` 调用 `pump_hydraulic_power_kw` 和 `pump_shaft_power_kw`；
4. `assess_pump_npsh_constraint` 检查 NPSHA/NPSHR 证据；
5. `_pump_standard_candidates` 与公开标准设计点形成参考候选；
6. `_pump_series_parallel_screen` 判断单台是否合理，必要时估算并联台数或串联级数；
7. `build_model_recommendation` 给出具体登记泵型，而不是“非标准泵”；
8. `build_engineering_adjustment_plan` 输出工作台可显示的系统修改方案与警告；
9. `customer_delivery` 把同一结果投影到设备一览表。

调查“为什么这台泵得到这个结果”时，应从返回对象里的 `calculations`、`model_recommendation`、`engineering_adjustment_plan` 和 `formula_trace` 反查上述函数，而不是从 GUI 文本猜测。
