# 内置公式可追溯性

## 1. 为什么旧展示仍然是黑箱

旧版本已经显示“目标量 = 公式 = 代入式 = 答案”，并在内部登记了公式 ID、适用范围和 `source_refs`。但这还不够，因为用户无法直接回答：

- 这些输入值从 Aspen、用户、上游公式还是程序保底值而来；
- `source_refs` 指向的文件是否真的存在、是否是这一版本、锚点是否能找到；
- 当前程序执行的是哪一份源码；
- 相同公式换一组输入后，怎样证明计算链随之变化；
- 缺少输入来源或外部标准原文时，系统是否仍把它说成“完整追溯”。

因此，本版本把公式说明升级成机器可校验的 `equipment-formula-trace-v1`。公式链不再只是一段显示文本。

## 2. 一次公式追溯包含什么

每条已执行计算都带有：

```text
formula_trace
├─ schema
├─ traceability_status
├─ calculation_id
├─ formula_id
├─ formula_definition
│  ├─ formula_expression
│  ├─ target_field / output_unit
│  ├─ dependency_fields
│  ├─ release_class / declared_evidence_class
│  ├─ applicability / does_not_prove / promotion_cap
│  ├─ implementation_binding
│  │  ├─ implementation_ref
│  │  ├─ engine_version
│  │  ├─ source_file_sha256
│  │  ├─ source_code_set_sha256
│  │  ├─ source_manifest_payload_sha256
│  │  └─ binding_status
│  └─ source_bindings[]
│     ├─ reference / relative_path / anchor
│     ├─ locator_line_1based
│     ├─ source_file_sha256
│     └─ binding_status
├─ formula_definition_sha256
├─ input_bindings[]
│  ├─ field_id / value / unit
│  ├─ source_kind / binding_status / evidence_class
│  ├─ upstream_calculation_id
│  ├─ upstream_formula_trace_sha256
│  ├─ fallback_tier
│  └─ field_value_sha256
├─ substitution
├─ output
├─ open_traceability_gaps[]
└─ calculation_trace_sha256
```

JSON 合同见
`app/schemas/equipment_formula_trace.schema.json`。

## 3. 两个哈希分别证明什么

### 3.1 公式定义 SHA-256

`formula_definition_sha256` 对以下内容做规范 JSON 哈希：

- 公式表达式和目标单位；
- 依赖字段；
- 公式等级、适用范围和禁止声称；
- 知识来源路径、源文件哈希和定位状态；
- 代码实现路径、引擎版本和源码清单绑定。

公式文字、适用条件、来源资产或实现代码任一变化，定义哈希都会变化。

### 3.2 本次计算追溯 SHA-256

`calculation_trace_sha256` 在公式定义之外继续绑定：

- 本次实际输入值和输入单位；
- 每个输入值的来源类型及字段值哈希；
- 上游公式追溯哈希；
- 代入式；
- 输出字段、数值和单位；
- 尚未闭合的追溯缺口。

同一公式只要输入、上游计算、输出或缺口变化，本次计算哈希就会变化。

哈希证明的是“这份记录没有悄悄变化”，不是“公式一定适用于该项目”。适用性仍由公式分支、证据等级和专业复核决定。

## 4. 输入来源怎样标记

| `source_kind` | 含义 | 是否自动视为正式来源 |
| --- | --- | --- |
| `upstream_registered_calculation` | 本输入由另一条登记公式计算，并绑定上游计算哈希 | 否；继承上游证据等级和缺口 |
| `registered_or_model_fallback` | 本输入来自登记保底值或受控模型估算 | 否；固定为 provisional，最高只可型式初筛 |
| `normalized_input` | 匹配器收到一个已经规范化的直接输入 | 否；如果调用层没有附带 Aspen 节点/用户记录证据，来源仍开放 |

当前匹配器不会伪造 Aspen 节点出处。仅凭 `normalized_input` 可以复算，但会产生：

```text
input_source_provenance_open:<field_id>
```

只有后续把 Aspen 提取谱系或用户签名输入真正绑定到字段，才能关闭这个缺口。

## 5. 公式来源怎样绑定

程序只把以下路径当成本地可绑定来源：

- `knowledge_graph/`
- `data/`
- `app/`
- `scripts/`

对于本地文件，程序计算整文件 SHA-256；带 `#anchor` 时还会搜索锚点标识并记录定位行。

| 绑定状态 | 含义 |
| --- | --- |
| `FILE_AND_ANCHOR_BOUND` | 文件存在、哈希已记录、锚点标识已找到 |
| `FILE_BOUND_NO_ANCHOR` | 文件存在并已绑定，但引用没有声明锚点 |
| `FILE_BOUND_ANCHOR_OPEN` | 文件存在，但声明的锚点标识未找到 |
| `REGISTERED_ASSET_MISSING` | 规则登记了本地来源，但当前运行包没有该文件 |
| `EXTERNAL_DOCUMENT_NOT_PACKAGED` | 引用的是外部标准/文献名称，没有本地原文哈希 |

例如 `GB/T 12459-2025 Table 2` 是外部引用；随程序发布的
`data/pipe_gbt12459_2025_dn_od_catalog.csv` 则能绑定具体文件哈希。程序会同时显示两者，不会用 CSV 的存在冒充整份国标原文已经随包提供。

## 6. 代码实现怎样绑定

每条公式记录：

```text
scripts/equipment_design_match.py#run_calculations
```

并从 `app/source_code_manifest.json` 读取：

- 公式执行源码文件 SHA-256；
- 整个核心源码集合 SHA-256；
- 源码清单载荷 SHA-256；
- 引擎版本。

源码树存在时还会重新计算实际文件哈希，与清单比较：

| 状态 | 含义 |
| --- | --- |
| `SOURCE_FILE_MATCHES_MANIFEST` | 当前源码文件与清单完全一致 |
| `PACKAGED_SOURCE_BOUND_BY_MANIFEST` | 冻结程序依赖已验证打包源码清单 |
| `SOURCE_FILE_MANIFEST_MISMATCH` | 源码已变化但清单未更新，追溯不完整 |
| `SOURCE_MANIFEST_BINDING_MISSING` | 没有可用源码清单绑定 |

后两种状态会进入 `open_traceability_gaps`。

## 7. 追溯状态

### `COMPLETE_REPRODUCIBLE_TRACE`

仅当以下条件同时满足：

- 所有输入都有可接受的来源绑定；
- 公式来源文件/锚点已经绑定；
- 代码文件与源码清单一致；
- 没有 provisional 保底输入；
- 没有外部未打包引用。

### `REPRODUCIBLE_TRACE_WITH_OPEN_PROVENANCE`

公式、代入式、输入、输出和代码仍可以复算，但至少有一项来源证据开放。常见情况：

- 匹配器只收到规范化输入，没有 Aspen 节点/用户输入凭证；
- 使用了设计保底值；
- 只登记了标准名称，没有原文文件哈希；
- 知识资产没有进入当前源码运行目录；
- 源码和源码清单不一致。

这不是失败状态，而是诚实地说明“数值可复算，但来源尚未完全闭合”。

## 8. 泵水力功率示例

对公式：

```text
P_h = ρ·g·Q·H
```

追溯记录会绑定：

- `formula_id=A_PUMP_HYDRAULIC_POWER`；
- `flow_m3_h`、`head_m`、`density_kg_m3` 的值、单位和字段值哈希；
- 如果 `head_m` 来自压差折算，则绑定上游
  `B_PUMP_PRESSURE_HEAD` 的 `calculation_trace_sha256`；
- 知识来源
  `knowledge_graph/formula_family_nodes.md#formula_pump_hydraulic_power`
  的文件哈希和定位行；
- 实现源码及源码集合哈希；
- 代入式和最终 kW 数值；
- 仍未闭合的直接输入来源。

这样用户能沿着“轴功率 → 水力功率 → 扬程 → 进出口压力/密度”逐级回看，而不是只看到最终功率。

## 9. 40 条登记计算规则覆盖

所有 `CALCULATION_REQUIREMENTS` 项现在必须在 `CALCULATION_POLICIES` 中存在完全对应的来源策略；测试要求两组 ID 完全相等。

| 设备/领域 | 计算 ID → 公式 ID |
| --- | --- |
| 泵 | `pump_head_from_pressure → B_PUMP_PRESSURE_HEAD`；`pump_hydraulic_power → A_PUMP_HYDRAULIC_POWER`；`pump_shaft_power → A_PUMP_SHAFT_POWER`；`pump_cavitation_margin → A_PUMP_CAVITATION_DIFFERENCE` |
| 阀门 | `valve_pressure_drop_from_streams → A_VALVE_PRESSURE_DROP_FROM_STREAMS`；`valve_liquid_equivalent_cv_screening → B_VALVE_LIQUID_EQUIVALENT_CV_SCREENING`；`valve_maximum_pressure_drop_screening → B_VALVE_MAXIMUM_PRESSURE_DROP_SCREENING` |
| 液力回收 | `liquid_turbine_pressure_head → B_LIQUID_TURBINE_PRESSURE_DROP_HEAD_COMPONENT`；`liquid_turbine_hydraulic_power → B_LIQUID_TURBINE_PRESSURE_DROP_POWER_COMPONENT`；`liquid_turbine_shaft_power → B_LIQUID_TURBINE_SHAFT_POWER_FROM_PRESSURE_COMPONENT` |
| 压缩/膨胀 | `pressure_ratio → A_PRESSURE_RATIO`；`compressor_isentropic_shaft_power → B_COMPRESSOR_ISENTROPIC_SHAFT_POWER_FROM_ACTUAL_INLET_FLOW`；`compressor_total_power → B_COMPRESSOR_TOTAL_INPUT_POWER_SCREENING` |
| 管线 | `pipe_required_diameter → A_PIPE_REQUIRED_ID`；`pipe_standard_dn_selection → B_PIPE_STANDARD_DN_SELECTION`；`pipe_actual_velocity → A_PIPE_ACTUAL_VELOCITY` |
| 压力与机械初算 | `design_pressure_basis_conversion → A_DESIGN_PRESSURE_ABSOLUTE_TO_GAUGE`；`design_pressure → B_DESIGN_PRESSURE_FACTOR`；`cylinder_thickness → B_VESSEL_SHELL_THICKNESS`；`head_thickness → B_VESSEL_ELLIPSOIDAL_HEAD_THICKNESS` |
| 塔器 | `tower_preliminary_diameter → B_TOWER_PRELIMINARY_DIAMETER_FROM_TRAFFIC`；`tower_tray_spacing → B_TOWER_TRAY_SPACING_SERIES`；`tower_cross_section → A_CIRCULAR_CROSS_SECTION`；`tower_active_area_fraction → B_TOWER_ACTIVE_AREA_CLOSURE`；`tower_active_area → B_TOWER_ACTIVE_AREA`；`tower_hole_area → B_TOWER_HOLE_AREA`；`tower_actual_superficial_velocity → B_TOWER_ACTIVE_AREA_VELOCITY`；`tower_internal_height → B_TOWER_ACTIVE_TRAY_HEIGHT`；`tower_preliminary_height → B_TOWER_PRELIMINARY_HEIGHT`；`tower_bottom_liquid_height → B_TOWER_HOLDUP_HEIGHT` |
| 容器/储存 | `cylinder_volume → A_CYLINDER_STRAIGHT_VOLUME`；`storage_required_volume → A_STORAGE_REQUIRED_VOLUME_FROM_RESIDENCE` |
| 膜 | `membrane_area → A_TUBULAR_MEMBRANE_GEOMETRIC_AREA` |
| 换热器 | `heater_sensible_duty_screening → B_HEATER_SENSIBLE_DUTY_SCREENING`；`exchanger_area → B_HEX_LMTD_AREA`；`exchanger_tube_count → B_HEX_TUBE_COUNT_FROM_AREA` |
| 结晶/过滤/干燥 | `crystallizer_working_volume → A_CRYSTALLIZER_WORKING_VOLUME`；`filter_area_from_cake_flux → B_FILTER_AREA_FROM_CAKE_FLUX`；`dryer_water_evaporation → A_DRYER_WATER_BALANCE`；`dryer_specific_duty → B_DRYER_SPECIFIC_DUTY` |

## 10. 用户在哪里查看

- GUI：选择设备后进入“公式链”页，每条公式下方直接显示完整追溯。
- HTML 报告：“公式可追溯性”章节按计算列出输入、来源、代码和缺口。
- Markdown 报告：每条计算下嵌公式 ID、双哈希、输入绑定、来源绑定和追溯缺口。
- JSON：`calculations[].formula_trace` 保存完整机器对象。
- Agent：组织答案保留完整 `formula_trace`，LLM 只能解释，不能修改。
- Schema：CLI 的 `schema_get` 可读取 `equipment-formula-trace-v1`。

## 11. 当前仍未闭合的部分

这次修改消除了“公式执行过程不可见”的黑箱，但没有伪造下列证据：

1. 匹配器不能仅凭一个数值证明它来自哪个 Aspen 节点或哪次用户输入；
2. 外部标准名称没有随包原文时，不能产生标准原文哈希；
3. 知识图谱没有安装的纯 Git 源码环境会显示来源资产缺失；
4. A 类恒等式只说明数学关系精确，不说明输入适合当前工况；
5. B 类公式即使完全可追溯，也仍只能用于 provisional/type screening；
6. 厂家曲线、EDR、塔内件水力学和正式机械计算仍需同设备外部证据。

下一步要关闭的重点不是继续添加显示字段，而是让 Aspen 提取层和人工输入层提供可验证的逐字段来源记录，再把这些记录传入 `input_bindings`。只有这样才能把“可复算公式链”进一步升级为“完整来源链”。
