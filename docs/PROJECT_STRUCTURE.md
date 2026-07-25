# 项目目录与逐文件说明

本文说明源码仓库的目录边界、运行时依赖方向，以及每一个受 Git 追踪文件的职责。它描述的是源码仓库，不等同于打包后的目录：大型冻结知识包、真实 Aspen 工程、运行输出和独立程序由发布流程单独管理。

## 1. 从哪里开始读

按下面顺序阅读，可以最快理解程序为什么能给出设备结果，以及结果为什么可追溯。

1. `README.md`：先了解产品能力、七阶段工作机理和工程边界。
2. `app/equipment_design_app.py` 与 `app/equipment_design_agent.py`：查看 GUI/API 和 CLI/Agent 两个入口。
3. `app/app_core.py`：查看入口如何组织目录、参数检查、计算、选型和客户交付。
4. `data/database_authority_registry.json` 与 `docs/DATABASE_STRUCTURE.md`：确认程序实际使用的数据库、表合同和隔离状态。
5. `scripts/aspen_equipment_derivation.py`：查看 Aspen 原始数据怎样成为统一的设备参数。
6. `scripts/equipment_calc.py`：查看确定性工程公式和参数台账。
7. `scripts/equipment_design_match.py`：查看具体设备型式、候选和证据门怎样生成。
8. `app/customer_delivery.py` 与 `app/result_presentation.py`：查看机器结果怎样投影为设备一览表和报告。
9. `app/derivation_workbench.py` 与 `app/tk_gui.py`：查看用户改参、单设备重算和恢复默认。
10. `app/llm_bridge.py`：最后查看 LLM 怎样在不能修改确定性结果的前提下参与解释和协作。

主执行链如下：

```text
Aspen COM / 提取 JSON / 人工输入
              │
              ▼
      输入规范化与来源登记
   app/aspen_com_import.py
   scripts/aspen_equipment_derivation.py
              │
              ▼
      公式计算与参数台账
      scripts/equipment_calc.py
              │
              ▼
      具体型式与证据门
  scripts/equipment_design_match.py
              │
              ├──────────────┐
              ▼              ▼
   连接部件与工况画像      用户覆盖与重算
 connection_component_   derivation_workbench.py
 selection.py
              └──────┬───────┘
                     ▼
       客户一览表、报告、GUI、CLI
 customer_delivery.py / result_presentation.py
```

## 2. 目录职责

| 目录 | 主要职责 | 不负责的内容 |
| --- | --- | --- |
| `app/` | 应用编排、GUI、Agent、PFD、报告、运行时验证和对外对象模式 | 不保存教材、真实项目和厂家私有数据 |
| `app/assets/` | Windows/Tk 程序图标及其可验证来源记录 | 不参与设备计算 |
| `app/fixtures/` | 自动化请求、模拟 Aspen 数据、17 类设备最小输入和混合协议样例 | 不作为生产项目结果 |
| `app/schemas/` | 固定模块间交换字段、状态码、哈希和证据边界 | 不替代实际计算 |
| `app/static/` | 浏览器轻量界面的结构、样式和交互 | 不掌握选型权威 |
| `app/tests/` | 对计算、提取、选型、证据门、报告、GUI 和打包行为做回归保护 | 不生成生产交付结论 |
| `scripts/` | Aspen 推导、工程计算、设备匹配、连接部件选择和批量审计 | 不直接组织最终用户界面 |
| `data/` | 可公开随源码发布的小型目录、数据库权威注册表和公开 SQL 结构 | 不包含大型 SQLite 载荷、完整知识库或真实 BKP |
| `knowledge_graph/` | 公开确定性匹配规则、参数模板、客户字段合同及接口 Schema；发布包另含大型 RAG 资产 | 公开部分不包含教材/标准正文、页面图像和大型索引 |
| `equipment_selection_graph/` | 公开设备族、具体型式来源、证据门、标准/厂家路线及旧结果隔离关系 | 不包含厂家 PDF 和标准正文 |
| `docs/` | 项目结构、数据库拆解、检索边界和版本交付核验 | 解释运行合同，但不替代机器注册表和校验器 |

## 3. 根目录文件

| 文件 | 作用 |
| --- | --- |
| `.gitignore` | 排除真实 Aspen 文件、知识包、构建缓存、运行输出、旧发布目录和临时数据库；只允许明确列出的脱敏数据进入 Git。 |
| `README.md` | 项目总说明，描述系统目标、功能、工作机理、设备族覆盖、可靠性边界、运行方式和验证状态。 |
| `THIRD_PARTY_NOTICES.md` | 记录程序使用的第三方组件、许可证和再分发提示，供打包与交付审查。 |
| `build_equipment_design_app.ps1` | Windows 构建入口；收集 Python/Tk 运行时、应用资源和冻结知识包，生成图形版与 CLI 版独立程序。 |
| `requirements-app.txt` | 源码运行和构建所需的最小 Python 依赖清单。 |
| `使用说明.md` | 面向非开发用户说明拖入 Aspen 文件、人工录入、运行选型、改参重算、导出报告和 Agent 设置。 |

## 4. `app/` 应用核心

| 文件 | 作用 |
| --- | --- |
| `app/__init__.py` | 声明本地设备设计应用包，提供稳定的 Python 包边界。 |
| `app/app_core.py` | 应用总编排核心；加载目录和参数定义，处理人工/自动匹配，连接计算、选型、连接部件、客户交付和运行时验证。 |
| `app/aspen_com_import.py` | Aspen COM 隔离导入器；复制工程、读取单位与运行状态、遍历流股和模块、提取物性，并支持在副本中添加输运物性后重跑。 |
| `app/aspen_pfd.py` | 把 Aspen 模块、流股拓扑和统一参数确定性映射为 PFD 节点；只确定设备族/应用类型，不冒充厂家机械型号。 |
| `app/authority_revision.py` | 生成并验证当前确定性权威版本，把核心源码、模式、运行时清单和哈希绑定为一次可审计修订。 |
| `app/customer_delivery.py` | 将匹配器已有的机器状态投影为客户设备一览表、设备数据表和证据索引；不重新计算或擅自升级证据。 |
| `app/database_authority.py` | 数据库权威解析与 fail-closed 校验器；验证活动状态、包内相对路径、大小、SHA-256、SQLite 完整性、表/列/记录数、构建清单和数据集晋升门。 |
| `app/derivation_workbench.py` | 建立可视化推导工作台对象；提供默认值、当前值、可编辑字段、中文选项、模板选择、单设备覆盖和重算审计。 |
| `app/equipment_design_agent.py` | 无界面 CLI/Agent 入口；处理 JSON/JSONL 请求，调度人工匹配、Aspen 推导、PFD、报告、知识查询、混合协作和自检。 |
| `app/equipment_design_app.py` | 图形应用入口和本地 API 门面；管理报告状态、原子写入、GUI 启动以及 Aspen 子进程调用。 |
| `app/llm_bridge.py` | 受控 LLM 桥；建立不可变上下文包、候选/条件登记表和公式配方，测试供应商连接并验证模型返回，拒绝模型改写确定性数值。 |
| `app/pfd_canvas.py` | 使用 Tk 矢量图元绘制原创 PFD，显示映射状态、设备族、候选和选择状态，并提供节点交互。 |
| `app/principles_requirements.py` | 加载和校验原理性需求图，索引各设备族需要的字段，并评估输入是否覆盖必要设计条件。 |
| `app/result_presentation.py` | 把机器结果转换为展示对象、设备卡片、完整一览表、公式链和 Agent 组织答案，并渲染 HTML/Markdown。 |
| `app/runtime_bundle.py` | 创建和校验冻结知识包清单，检查文件数量、大小、SHA-256、额外文件、链接和 SQLite 完整性，异常时关闭运行。 |
| `app/source_code_manifest.json` | 当前固定核心源码快照的路径与 SHA-256 清单，用于证明打包程序运行的是哪一组源码。 |
| `app/source_code_manifest.py` | 创建、校验并对比核心源码清单；检测源码缺失、篡改、额外文件和打包快照漂移。 |
| `app/tk_gui.py` | 主 Tk 图形界面；实现四种入口、中文下拉框、Aspen 导入、设备一览表、推导流程、改参重算、报告和 LLM 设置。 |
| `app/user_guide.py` | 内置到 GUI 的中文操作说明文本，使独立程序不依赖外部说明文件也能提供帮助。 |
| `app/viscosity_fallback.py` | 黏度缺失时的受控估算器；气体使用 Sutherland/Wilke 路线，液体使用组分混合路线，并对越界、两相和资料不足明确阻断或警告。 |

## 5. `app/assets/` 图标资源

| 文件 | 作用 |
| --- | --- |
| `app/assets/equipment_design_app.ico` | Windows 可执行程序和窗口使用的多尺寸图标。 |
| `app/assets/equipment_design_app_icon.png` | Tk 窗口和非 ICO 场景使用的透明 PNG 图标。 |
| `app/assets/equipment_design_icon_manifest.json` | 记录图标源文件、派生文件尺寸和哈希，防止构建时图标被替换。 |
| `app/assets/equipment_design_icon_source.jpg` | 应用图标的原始设计源图，供重新生成 ICO/PNG 时追溯。 |

## 6. `app/fixtures/` 可重复输入

| 文件 | 作用 |
| --- | --- |
| `app/fixtures/PROTOCOL_1_6_HYBRID_FIXTURES.md` | 说明程序与 LLM 交错执行协议 1.6 的样例生成方法和预期输出。 |
| `app/fixtures/agent_all_family_model_candidates_request.json` | 请求全部设备族运行候选型式生成，用于检查 17/17 具体型式覆盖。 |
| `app/fixtures/agent_aspen_derive_request.json` | 请求从已提取 Aspen 导出文件执行设备参数推导。 |
| `app/fixtures/agent_knowledge_pump_review_request.json` | 请求知识查询并聚焦泵的设计复核信息。 |
| `app/fixtures/agent_knowledge_request.json` | 通用知识搜索请求样例。 |
| `app/fixtures/agent_manual_pump_request.json` | 人工泵工况匹配样例，验证扬程、功率、NPSH 和具体型式链。 |
| `app/fixtures/agent_packaged_mock_aspen_import_request.json` | 打包环境下使用模拟 Aspen 数据执行导入的请求，避免测试依赖本机 Aspen。 |
| `app/fixtures/agent_partial_flow_request.json` | 字段不完整的设备匹配请求，验证缺项不会被静默默认。 |
| `app/fixtures/agent_render_packaged_formula_report_request.json` | 请求在打包环境中输出带工程公式链的报告。 |
| `app/fixtures/agent_render_representative_report_request.json` | 多类代表设备报告请求，用于检查设备卡片、一览表和警告展示。 |
| `app/fixtures/agent_representative_parameter_chain_request.json` | 多设备人工批处理请求，用于展示来源、公式、候选和结果的完整参数链。 |
| `app/fixtures/agent_selftest_request.json` | 调用 `system.selftest` 的最小请求，用于安装后快速检查运行环境。 |
| `app/fixtures/all_family_minimum_meaningful_inputs.json` | 17 类设备各自能够启动有效初筛的最小有意义输入集合。 |
| `app/fixtures/generate_protocol_1_6_hybrid_fixtures.py` | 运行受控混合协议并重新生成固定请求/响应样例，同时审计模型步骤输出。 |
| `app/fixtures/mock_aspen_pump.json` | 脱离 COM 的泵 Aspen 模拟导出，包含流股、模块、单位和泵工况。 |
| `app/fixtures/protocol_1_6_hybrid_prepare_request.json` | 混合协作第一阶段请求：由程序准备不可变上下文和允许模型处理的步骤。 |
| `app/fixtures/protocol_1_6_hybrid_run_no_llm_request.json` | 不调用远程 LLM 的混合流程请求，用于验证确定性部分可以独立完成。 |

## 7. `app/schemas/` 数据合同

| 文件 | 作用 |
| --- | --- |
| `app/schemas/equipment_agent_organized_answer.schema.json` | 约束 Agent 最终答案的固定章节、结论、计算、候选、警告、缺证和下一步。 |
| `app/schemas/equipment_customer_delivery_bundle.schema.json` | 约束一览表、设备数据表和证据索引组成的权威绑定客户交付包。 |
| `app/schemas/equipment_customer_output_profiles.schema.json` | 定义每个设备族应向客户展示哪些字段及字段来源合同。 |
| `app/schemas/equipment_database_authority_registry.schema.json` | 定义数据库注册表的消费者、版本状态、运行许可、文件身份、表合同和非 SQL 目录记录。 |
| `app/schemas/equipment_design_agent_request.schema.json` | 定义 CLI/Agent 可接受的操作名、输入对象、文件路径、LLM 配置和输出选项。 |
| `app/schemas/equipment_design_agent_response.schema.json` | 定义 Agent 成功/失败响应、结果对象、错误和产物清单。 |
| `app/schemas/equipment_design_authority_revision.schema.json` | 定义确定性权威修订中的源码、模式、知识清单版本及哈希关系。 |
| `app/schemas/equipment_design_hybrid_result.schema.json` | 定义程序结果与受控 AI 辅助结果共同存在时的混合结果信封。 |
| `app/schemas/equipment_design_interleaved_timeline.schema.json` | 定义程序计算、AI 建议、程序复核和继续执行的交错时间线。 |
| `app/schemas/equipment_design_llm_context_pack.schema.json` | 定义发给 LLM 的不可变最小上下文、候选登记表、缺项和哈希。 |
| `app/schemas/equipment_design_llm_orchestration.schema.json` | 定义允许的 LLM 步骤、供应商设置、失败策略和严格编排状态。 |
| `app/schemas/equipment_design_llm_prepared.schema.json` | 定义混合执行开始前由程序冻结的准备包。 |
| `app/schemas/equipment_design_llm_step_output.schema.json` | 限制单个模型步骤只能返回登记的规则、条件、选择或解释字段。 |
| `app/schemas/equipment_design_pfd_mapping.schema.json` | 定义 Aspen 模块、PFD 节点、流股边、映射状态、用户覆盖和局部重算信息。 |
| `app/schemas/equipment_design_presentation.schema.json` | 定义设备参数、公式链、候选、证据和警告的统一展示对象。 |
| `app/schemas/equipment_design_report_status.schema.json` | 定义 GUI 报告旁路状态文件，绑定输入响应、展示对象、报告文件和哈希。 |
| `app/schemas/equipment_design_source_code_manifest.schema.json` | 定义固定核心源码清单的文件记录、集合哈希和版本。 |
| `app/schemas/equipment_evidence_index.schema.json` | 定义同一设备、同一工况下可用于型号升级的证据索引。 |
| `app/schemas/equipment_family_datasheet.schema.json` | 定义按设备族输出的客户数据表、字段状态和证据等级。 |
| `app/schemas/equipment_formula_trace.schema.json` | 定义内置公式的表达式、输入、出处、代码实现、开放缺口和双 SHA-256 机器追溯合同。 |
| `app/schemas/equipment_overview_table.schema.json` | 定义完整设备选型一览表、公开字段、开放缺口、数量和证据门。 |

## 8. `app/static/` 浏览器界面

| 文件 | 作用 |
| --- | --- |
| `app/static/index.html` | 轻量浏览器界面的页面结构和 Aspen/人工入口表单。 |
| `app/static/styles.css` | 页面布局、设备卡片、状态和表单的视觉样式。 |
| `app/static/app.js` | 浏览器交互、表单校验和本地 API 调用；压力基准等关键字段没有静默默认。 |

## 9. `app/tests/` 回归测试

| 文件 | 作用 |
| --- | --- |
| `app/tests/test_agent_pfd.py` | 验证 Agent 的 PFD 构建、覆盖、恢复自动映射、局部重算和产物哈希。 |
| `app/tests/test_app_core.py` | 验证应用目录、人工字段合同、泵计算闭合、候选层级和设备族具体型式。 |
| `app/tests/test_aspen_com_import.py` | 验证 Aspen COM 导入的压力基准、单位、运行状态、拓扑、物性和输运性质提取。 |
| `app/tests/test_aspen_equipment_derivation.py` | 验证 Aspen 流股/模块推导、单位换算、泵功率/NPSH、换热、塔器、管线和异常历史归属。 |
| `app/tests/test_aspen_pfd.py` | 验证模块类型、拓扑和参数到设备族/PFD 的确定性映射，不错误提升机械型号。 |
| `app/tests/test_audit_multi_bkp_overview_gate.py` | 验证多 BKP 一览表的字段完整性、开放缺口、具体型式措辞和单元格/行/表哈希。 |
| `app/tests/test_audit_stage1_detailed_reliability.py` | 验证最终行与来源、推导链和证据谱系绑定，检测篡改与陈旧计数。 |
| `app/tests/test_customer_delivery.py` | 验证客户一览表、数据表和证据索引不会丢失具体型式或把缺失值伪装成默认值。 |
| `app/tests/test_customer_delivery_real_bkp_stage1.py` | 用已核查真实 BKP 派生结果验证塔、泵、六条物理管线及公开字段的诚实性。 |
| `app/tests/test_database_authority.py` | 验证活动库绑定、旧库/候选库禁用、路径逃逸、文件替换、表合同、构建清单和数据集晋升门均 fail-closed。 |
| `app/tests/test_engineering_adjustment_workbench.py` | 验证大换热器、奇异泵、大塔调整方案，以及工作台改参、中文选项和组织答案。 |
| `app/tests/test_equipment_design_agent.py` | 验证 CLI/JSONL 协议、打包运行、自检、人工批处理、错误封装和运行时 fail-closed。 |
| `app/tests/test_equipment_design_app.py` | 验证 GUI API 的 Aspen 前置检查、子进程调用和报告状态旁路文件。 |
| `app/tests/test_equipment_design_match_safety.py` | 验证 NPSH、喘振、容积、阀 Cv、反应器尺寸和塔高等约束失败时不伪造终选。 |
| `app/tests/test_icon_assets.py` | 验证图标源、派生资源和图标清单哈希一致，并能在打包布局中找到。 |
| `app/tests/test_llm_orchestration.py` | 验证模型不能替代程序算术、配方输入保持一致、缺段可补全、坏模型输出被隔离。 |
| `app/tests/test_pfd_canvas.py` | 验证 17 类设备矢量符号、状态颜色、文字压缩和真实 Tk 画布渲染。 |
| `app/tests/test_principles_requirements.py` | 验证原理需求图的节点、边、哈希、字段覆盖和正式证据边界。 |
| `app/tests/test_public_algorithm_source.py` | 验证公开源码确实包含可执行的 17 类设备匹配规则、型号规则、参数模板和设备选择图谱。 |
| `app/tests/test_public_rag_contract_bundle.py` | 验证 RAG 公开合同包可重复构建、文件哈希一致，并且不含 SQLite、PDF、图片或 Aspen 工程。 |
| `app/tests/test_result_presentation.py` | 验证一览表字段、公式显示、候选门、终端型式来源和 HTML/Markdown 展示。 |
| `app/tests/test_runtime_bundle.py` | 验证知识包清单对缺失、篡改、同尺寸替换和额外文件均能关闭运行。 |
| `app/tests/test_source_code_manifest.py` | 验证源码及打包快照的缺失、修改、额外文件和清单漂移检测。 |
| `app/tests/test_static_aspen_ui.py` | 验证浏览器界面不默认压力基准或大气压，并在提交前阻止含糊输入。 |
| `app/tests/test_tk_gui.py` | 验证真实 Tk 窗口、四种入口、中文下拉、公式、完整一览表、LLM 连接测试和人工字段。 |
| `app/tests/test_viscosity_fallback.py` | 验证气/液黏度关联式、质量/摩尔组成换算、痕量组分、越界、两相和缺资料阻断。 |

## 10. `data/` 小型运行数据

| 文件 | 作用 |
| --- | --- |
| `data/aspen_equipment_export_sample.json` | 脱敏 Aspen 设备/流股导出样例，用于演示和基本推导测试。 |
| `data/aspen_run_status_clean_sample.json` | 干净 Aspen 运行状态样例，用于区分已收敛、警告和错误状态。 |
| `data/equipment_match_examples.json` | 多种设备工况与预期具体型式的匹配示例。 |
| `data/database_authority_registry.json` | 数据库机器权威注册表；公开当前活动库、隔离/无效/候选库、消费者、文件大小、SHA-256、表计数和允许使用范围。 |
| `data/database_contracts/standards_knowledge_public_schema.sql` | 标准检索库的公开业务表 DDL，不包含受版权约束的数据载荷。 |
| `data/database_contracts/executable_standard_data_public_schema.sql` | 结构化执行库的公开业务表 DDL，不包含标准记录载荷。 |
| `data/pipe_gbt12459_2025_dn_od_catalog.csv` | 程序使用的 DN—外径小型目录，支持管径计算后选择标准公称直径和外径。 |
| `data/pump_gbt5662_2013_design_points.csv` | 泵标准设计点/型式初筛目录，支持泵候选条件核对，不代替厂家性能曲线。 |

## 11. `scripts/` 确定性工程脚本

| 文件 | 作用 |
| --- | --- |
| `scripts/aspen_equipment_derivation.py` | 核心 Aspen 推导引擎；规范化流股和模块、换算单位、建立来源谱系、补充黏度并计算各设备族的参数链。 |
| `scripts/audit_database_authority.py` | 公开数据库审计入口；可只列库存，也可完整核对活动库哈希、SQLite、表合同、构建清单和必需数据集。 |
| `scripts/build_public_rag_contract_bundle.py` | 将 RAG 注册表、公开 SQL、检索说明和验证源码打成确定性小型 ZIP，同时阻止数据库及版权正文进入压缩包。 |
| `scripts/audit_multi_bkp_model_gate.py` | 批量检查多个 BKP 派生结果是否给出具体登记型式，统计候选、开放缺口和不合格笼统措辞。 |
| `scripts/audit_multi_bkp_overview_gate.py` | 批量审计客户设备一览表的物理设备计数、字段覆盖、开放门、来源及单元格/行/表哈希。 |
| `scripts/audit_stage1_detailed_reliability.py` | 第一阶段深度可靠性审计；逐设备核查输入、公式、型式、数量、管线细节、调整方案、来源和公开投影。 |
| `scripts/connection_component_selection.py` | 根据介质、相态、组分、温压、材料、DN 和连接条件选择登记的管件、法兰、垫片及密封路线。 |
| `scripts/equipment_calc.py` | 确定性工程公式库和参数台账；包含管径/流速、压力与壁厚、泵、换热、塔器、反应器等计算。 |
| `scripts/equipment_design_match.py` | 设备选型主引擎；执行单位规范化、特征生成、候选规则、具体终端型式、证据门、数量及超界系统修改方案。 |
| `scripts/equipment_service_profile.py` | 把 Aspen 或人工输入整理为统一工况画像，包括相态、组分、腐蚀/固体风险和连接部件所需属性。 |
| `scripts/experiment_aspen_com_transport_add.py` | 生产机实验工具；在隔离副本中尝试通过 COM 添加输运物性集合并记录操作结果。 |
| `scripts/probe_aspen_com_configuration.py` | 只读探查 Aspen COM 配置树和自动化对象表面，帮助确定不同版本可访问的配置节点。 |
| `scripts/probe_aspen_transport_property_tree.py` | 定向搜索 Aspen 属性树中的黏度等输运物性节点，为修复提取路径提供证据。 |
| `scripts/rebuild_standards_runtime_sidecars.py` | 从标准知识 SQLite 权威载体重建紧凑目录、交叉表和审计旁路文件，不复制标准 PDF。 |

## 12. `knowledge_graph/` 公开算法规则与接口合同

这里只提交理解和运行确定性算法必需、且不包含版权正文的规则/合同。`standards_graph/`、文档切片、图片和 SQLite 仍由 Release 资产管理。

| 文件 | 作用 |
| --- | --- |
| `knowledge_graph/aspen_equipment_derivation_chain.md` | 解释 Aspen 导出到设备/管线推导结果的字段链、状态和边界。 |
| `knowledge_graph/aspen_equipment_export.schema.json` | 约束 Aspen COM/模拟导出中的案例、单位、流股、模块、拓扑和运行状态。 |
| `knowledge_graph/equipment_connection_selection_package.schema.json` | 约束法兰、密封面、垫片、紧固件和连接证据包。 |
| `knowledge_graph/equipment_customer_output_profiles.json` | 登记 17 个设备族在客户一览表/数据表中应公开的字段及来源。 |
| `knowledge_graph/equipment_design_parameter_package.schema.json` | 约束设备设计参数包、选择特征向量、字段状态和来源。 |
| `knowledge_graph/equipment_match_input.schema.json` | 约束确定性匹配器接受的规范输入字段和类型。 |
| `knowledge_graph/equipment_match_rules.json` | 登记 17 个设备族的别名、Aspen 模块映射、尺寸字段、验证字段和计算规则。 |
| `knowledge_graph/equipment_model_recommendation_rules.json` | 登记具体终端型式、适用谓词、硬约束、候选优先级和受控预设计默认。 |
| `knowledge_graph/equipment_parameter_chain_templates.json` | 登记每个设备族的主计算、候选闭合、高级条件和正式证据字段。 |
| `knowledge_graph/equipment_service_label_derivation_contract.md` | 说明工况标签必须从真实流股事实推导，禁止用户/LLM 直接标签成为证据。 |
| `knowledge_graph/equipment_service_profile.schema.json` | 约束设备工况画像、边界流股、观察值、标签、未知项和诊断。 |
| `knowledge_graph/equipment_type_applicability_contract.md` | 说明设备终端型式的适用性、禁用条件和证据边界。 |
| `knowledge_graph/equipment_type_applicability_graph.schema.json` | 约束设备型式适用性图谱的节点、边和规则关系。 |
| `knowledge_graph/equipment_type_applicability_label_catalog.json` | 登记型式适用性规则使用的标准标签和含义。 |

## 13. `equipment_selection_graph/` 型式权威图谱

| 文件 | 作用 |
| --- | --- |
| `equipment_selection_graph/equipment_selection_graph_v2.json` | 机器图谱主体；连接 17 个设备族、标准身份、厂家来源、模板、证据门和复用等级。 |
| `equipment_selection_graph/README.md` | 说明图谱范围、入口和使用边界。 |
| `equipment_selection_graph/00-authority-registry.md` | 列出图谱权威来源、状态和冲突处理顺序。 |
| `equipment_selection_graph/05-evidence-reuse-boundaries.md` | 区分直接复用、方法借用、软件边界、厂家边界和禁止迁移。 |
| `equipment_selection_graph/10-equipment-family-router.md` | 说明设备族路由和跨族冲突处理。 |
| `equipment_selection_graph/12-template-coverage-router.md` | 说明设备一览表模板覆盖关系。 |
| `equipment_selection_graph/13-overview-table-field-schema.md` | 说明设备一览表字段分组与公开状态。 |
| `equipment_selection_graph/15-standard-designation-rules.md` | 说明标准系列/规格名称何时可以进入候选。 |
| `equipment_selection_graph/16-dynamic-vendor-rules.md` | 说明厂家系列、平台和正式型号证据门。 |
| `equipment_selection_graph/17-piping-selection-router.md` | 说明管线、管件、法兰垫片和阀门路由。 |
| `equipment_selection_graph/18-model-size-standard-router.md` | 说明型号、规格尺寸和标准系列之间的边界。 |
| `equipment_selection_graph/19-overview-table-source-precision.md` | 说明一览表字段所需的来源精度。 |
| `equipment_selection_graph/20-model-determination-card.md` | 正式设备型号确定前必须核对的证据卡。 |
| `equipment_selection_graph/21-line-determination-card.md` | 正式管道等级确定前必须核对的证据卡。 |
| `equipment_selection_graph/30-overview-table-interface.md` | 设备一览表对外接口规则。 |
| `equipment_selection_graph/31-piping-overview-interface.md` | 管线一览表对外接口规则。 |
| `equipment_selection_graph/40-legacy-model-quarantine.md` | 旧项目型号、数值和历史候选的隔离规则。 |
| `equipment_selection_graph/90-unknowns-router.md` | 无法闭合字段、未知项和下一步动作的路由。 |

## 14. `docs/` 文档

| 文件 | 作用 |
| --- | --- |
| `docs/FORMULA_TRACEABILITY.md` | 说明内置公式的机器追溯合同、输入/来源/代码绑定、哈希复核、40 条规则覆盖和剩余边界。 |
| `docs/ALGORITHM_GUIDE.md` | 按真实执行顺序说明算法入口、文件分工、关键函数、公式位置、规则 JSON、管线主链和泵实例。 |
| `docs/DATABASE_STRUCTURE.md` | 公开拆解数据库版本、路径、哈希、业务表、记录数、调用方、晋升/隔离原因、Git/Release 边界和后续接手检查单。 |
| `docs/PROJECT_STRUCTURE.md` | 当前文档；说明目录边界、阅读顺序、执行链和所有受 Git 追踪文件的职责。 |
| `docs/RETRIEVAL_AND_GAPS.md` | 说明知识检索、设备型式检索、排序与证据门，并逐项区分未实现、部分实现和待生产验证能力。 |
| `docs/STAGE1_2_4_0_RELEASE_VERIFICATION_20260724.md` | 记录 2.4.0 完成范围、测试结果、17 类设备覆盖、程序/源码/知识包哈希及待生产环境验证。 |

## 15. 没有进入 Git 的目录

这些内容由 `.gitignore` 明确隔离，因此不会在 GitHub 源码树中看到：

| 内容 | 原因与交付方式 |
| --- | --- |
| `knowledge_graph/` 中未列入第 12 节的内容 | 约 1.49 GB 冻结知识资产中的标准正文索引、文档切片、图片、候选证据和大型 SQLite；随独立程序打包并由运行时清单校验。 |
| `dist_stage*/`、`build/` | 独立程序和构建缓存；最终 EXE 作为 GitHub Release 附件发布。 |
| `outputs/`、`tmp/` | 用户报告、审计输出、COM 隔离副本和临时文件；属于运行结果。 |
| `*.bkp`、`*.apw`、`*.inp` | Aspen 工程可能包含项目机密和第三方数据，只允许用户在本地显式处理。 |
| 教材、标准 PDF、图片页和压缩包 | 体积、版权和再分发边界不适合进入公开源码历史。 |

新增文件时，应同时决定它属于哪一层、是否参与确定性权威、需要什么模式或测试，以及是否必须加入源码/运行时哈希清单。这样项目目录本身也能维持可审计性。
