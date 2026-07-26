# 数据库公开结构、权威状态与调用合同

本文把设备设计选型程序使用的 SQLite 数据库作为源码合同公开拆解。目的不是把受体积、版权和项目保密约束的大型数据载荷提交到 Git，而是让任何开发者或后续 agent 都能回答以下问题：

- 程序实际使用哪个数据库；
- 该库为什么允许使用，允许用于什么范围；
- 每张业务表保存什么，至少必须有哪些列和记录；
- 文件、构建清单、数据集状态怎样共同通过验证；
- 哪些旧库和候选库不得进入运行链；
- 数据库文件缺失、被替换或路径被硬编码时，程序怎样关闭运行。

机器可读入口是 `data/database_authority_registry.json`，其 JSON Schema 是 `app/schemas/equipment_database_authority_registry.schema.json`。两类数据库的公开 DDL 位于 `data/database_contracts/`。本文件是解释层，注册表和校验代码才是运行时权威。

## 1. 两类数据库，各自只做一件事

```text
用户查询
  └─ app/app_core.py::_standards_sqlite_search
       └─ standards_knowledge.sqlite
          用途：文档段落、表格、图、公式的检索与定位
          边界：检索命中只是证据上下文，不能直接成为工程定值

Aspen/人工输入 → 管道计算
  └─ scripts/aspen_equipment_derivation.py::load_verified_pipe_standard_store
       └─ executable_standard_data.sqlite
          用途：读取已提升的 PN 系列和管径/壁厚记录
          边界：只允许 VERIFIED + CURRENT + DIRECT_REUSE_VERIFIED 数据集
```

二者不能互换。检索库适合“找到哪里说过什么”，结构化执行库适合“在已核准的有限数据集中选取一条机器记录”。程序不会因为检索到了表格文字，就自动把其中数字提升为设计值。

`knowledge_graph/ai_engineering_choice_registry.json` 不是第三个数据库。它是随源码公开、受哈希保护的有限选择注册表：
保存 17 个设备族可交给 AI 判断的具体型式、材料/零部件组合、触发条件、选择依据和来源引用。
模型只能返回登记 ID；程序必须对照该 JSON 复核固定字段和值，并以 `J / TYPE_SCREENING` 暂定等级重算。
标准 SQLite 的搜索命中不能绕过人工晋升流程直接新增或修改这个注册表。

## 2. 当前数据库状态总表

| 数据库 ID | 状态 | 运行时 | 构建/角色 | 大小（字节） | SHA-256 |
| --- | --- | --- | --- | ---: | --- |
| `standards_knowledge_authority` | `ACTIVE` | 允许，必需 | 标准资料检索权威 | 322,678,784 | `B24192732565F47FE4378C04A253A6F48C4FD984BB489AE99C3C3E003F06D5A4` |
| `executable_standard_data_current` | `ACTIVE` | 允许，必需 | `20260720-visual-batch-v2` 当前结构化基线 | 107,720,704 | `27A1C4B0FA5CA9DAEACD727808A4758ECC156835BADC83461BF789DA0BA8F551` |
| `executable_standard_data_reconciled_legacy` | `QUARANTINED` | 禁止 | `20260720-reconciled-001` 历史库 | 106,627,072 | `CFDA54ECA29A03B910A5D73266EB0F516BFC4A313919DD41F1A6B00E2FE794CB` |
| `gbt17261_delta_v1_invalid` | `INVALID` | 禁止 | 不完整、已被 v2 取代 | 107,728,896 | `E0C578CEAFFA93A60CF6A902744B29B063D970C37E67A50E14D4EA1414BF767E` |
| `gbt17261_delta_v2_candidate` | `CANDIDATE` | 禁止 | 已核查增量候选，尚未晋升 | 107,728,896 | `A63A3615330896F3E793CF8050A376E44D41828C5D99BAB4F27BDDF3B8E77908` |

旧 `reconciled` 库被隔离，是因为其构建清单登记的输入注册表哈希与包内唯一可用注册表不一致。候选 v2 仍有 741 个关键表和 691 个图未解决，因此不能以“数据更多”为理由替换当前基线。候选 v1 不完整且已被 v2 取代。

这些状态不是注释。`app/database_authority.py` 会拒绝把任何非 `ACTIVE` 数据库绑定给运行时消费者。

## 3. 标准检索库 `standards_knowledge.sqlite`

路径：

`knowledge_graph/standards_graph/source_layer/indexes/standards_knowledge.sqlite`

业务表和当前记录数：

| 表 | 记录数 | 职责 | 关键公开字段 |
| --- | ---: | --- | --- |
| `documents` | 77 | 一份来源文档的身份、来源哈希、资料族和默认证据等级 | `doc_id`, `relative_path`, `source_pdf_sha256`, `family`, `source_kind`, `evidence_default`, `package_status` |
| `chunks` | 20,237 | 可搜索正文段落及页码/章节定位 | `chunk_id`, `doc_id`, `page_start`, `page_end`, `section_path`, `text_sha256`, `location_status`, `text` |
| `tables_data` | 5,198 | 表题、单元格文本、页面和数字复用门 | `table_id`, `doc_id`, `page_1based`, `caption`, `source_pdf_sha256`, `cell_text`, `numeric_reuse_allowed`, `asset_qa_status` |
| `figures_data` | 3,477 | 图号、图题、页面和来源身份 | `figure_id`, `doc_id`, `page_1based`, `caption`, `source_pdf_sha256` |
| `formulas_data` | 91 | 公式原文、页面和 QA 状态 | `formula_id`, `doc_id`, `page_1based`, `raw_text`, `qa_status`, `source_pdf_sha256` |

库中还包含 FTS5、trigram 和相应影子表，用于索引实现；它们不是稳定业务合同。公开合同只保证上表中的业务表、字段、记录数、文件哈希和 `PRAGMA quick_check=ok`。

完整公开 DDL：

`data/database_contracts/standards_knowledge_public_schema.sql`

当前应用检索采用有界 SQL 查询，并返回来源文档哈希、页码、位置状态和复用边界。数据库命中不会绕过公式、选型规则或标准数据集晋升门。

## 4. 结构化执行库 `executable_standard_data.sqlite`

当前路径：

`knowledge_graph/standards_graph/executable_data/build_20260720_visual_batch_v2/executable_store/executable_standard_data.sqlite`

配套构建清单：

`knowledge_graph/standards_graph/executable_data/build_20260720_visual_batch_v2/executable_store/build_manifest.json`

业务表和当前记录数：

| 表 | 记录数 | 职责 | 关键公开字段 |
| --- | ---: | --- | --- |
| `datasets` | 24 | 数据集级标准身份、生命周期、复用等级、QA 和构建身份 | `dataset_id`, `standard_id`, `standard_version`, `authority_state`, `lifecycle_state`, `reuse_class`, `qa_status`, `record_count`, `build_id` |
| `standard_records` | 24,887 | 标准数值/文本记录、来源单元格、规范化数值和记录哈希 | `dataset_id`, `record_id`, `standard_id`, `standard_version`, `source_sha256`, `raw_value`, `normalized_number`, `source_payload_json`, `reuse_class`, `qa_status`, `record_sha256` |
| `figure_datasets` | 14 | 图形数据集身份、表示类型、状态和计数 | `dataset_id`, `representation_type`, `standard_id`, `lifecycle_state`, `reuse_class`, `qa_status`, `record_count` |
| `figure_records` | 1,844 | 图形/曲线的结构化记录、载荷和记录哈希 | `dataset_id`, `figure_record_id`, `figure_id`, `record_kind`, `standard_id`, `source_sha256`, `payload_json`, `reuse_class`, `qa_status`, `record_sha256` |

完整公开 DDL：

`data/database_contracts/executable_standard_data_public_schema.sql`

当前程序只声明两个可执行数据集：

| 数据集 ID | 用途 | 必须同时满足 |
| --- | --- | --- |
| `gbt1048_nominal_pressure_series` | 从设计压力映射 PN 系列候选 | `lifecycle_state=CURRENT`, `reuse_class=DIRECT_REUSE_VERIFIED`, `qa_status=VERIFIED` |
| `gbt17395_pipe_dimensions_weights` | 读取公制外径、壁厚和重量候选 | 同上 |

使用这些记录仍不代表完成正式管道等级。材料相容性、压力—温度额定、腐蚀裕量、制造路线、项目等级和厂家/项目权威仍有各自的门，程序会保留警告与待办。

## 5. 程序怎样确定“到底用哪个库”

唯一允许的解析链是：

```text
consumer_id
  → data/database_authority_registry.json
  → ACTIVE database_id
  → relative_path（只能位于程序包内）
  → 文件大小 + SHA-256
  → SQLite quick_check
  → 必需表 + 必需列 + 精确记录数
  → build_manifest 的 build_id + sqlite_sha256
  → 必需 dataset 的 CURRENT / DIRECT_REUSE_VERIFIED / VERIFIED
  → 只读 URI mode=ro
```

对应源码：

- `app/database_authority.py`：加载注册表、阻止路径逃逸、验证文件与表合同、验证构建清单和数据集门；
- `app/app_core.py#_standards_sqlite_search`：通过消费者 `standards_knowledge_search` 获取检索库；
- `scripts/aspen_equipment_derivation.py#load_verified_pipe_standard_store`：通过消费者 `pipe_standard_store` 获取结构化执行库；
- `scripts/audit_database_authority.py`：独立输出库存或执行完整审计；
- `scripts/build_public_rag_contract_bundle.py`：生成不含数据库和版权正文的小型 RAG 公开合同 ZIP；
- `app/runtime_bundle.py`：把注册表、公开 DDL、两个活动库和活动构建清单列为发布包必需资产。

禁止在业务代码中新增 `build_.../executable_standard_data.sqlite` 硬编码路径。版本切换必须先更新注册表、公开状态、构建清单和测试，再由注册表完成原子切换。

## 6. GitHub 源码与 Release 载荷的边界

公开 Git 仓库追踪：

- 数据库权威注册表；
- 两类数据库的公开 SQL 表结构；
- 注册表 JSON Schema；
- 验证器与审计脚本；
- 数据库说明、调用边界和回归测试；
- 两个可公开的小型 CSV 目录。

Git 仓库不追踪：

- 322 MB/108 MB 的 SQLite 载荷；
- 教材、标准 PDF、页面图像和真实 Aspen 工程；
- 构建缓存、运行输出和临时测试数据库。

大型只读载荷由发布构建复制进独立程序，并同时进入运行时资产清单。源代码检出后若没有 `knowledge_graph/`，结构测试仍可运行；需要真实检索或管线标准数据的集成测试应明确跳过或提示安装 Release 资产，不能偷偷改用旧库。

## 7. 后续 agent 接手检查单

1. 先运行 `python scripts/audit_database_authority.py --inventory-only`，确认活动/隔离/候选状态。
2. 有完整知识包时，再运行 `python scripts/audit_database_authority.py`；只有输出 `PASS` 才能声称数据库链已验证。
3. 搜索源码中是否出现新的 `executable_standard_data.sqlite` 硬编码路径；消费者只能经 `database_authority` 解析。
4. 不得把 `QUARANTINED`、`INVALID`、`CANDIDATE` 改名或复制成活动路径来绕过校验。
5. 新库晋升必须更新：注册表、SHA-256、大小、表计数、build ID、数据集状态、公开 DDL（若结构变化）、测试、源码清单和 Release 构建。
6. 检索命中与工程定值必须继续分层；检索库的表格文字不能直接进入计算。
7. 管线仍需项目管道等级、材料/温压适用性和正式产品标准闭合；数据库有记录不等于设计完成。
8. 若工作目录使用 Windows junction，比较路径时应以注册表相对路径和实际哈希为准，不能依据盘符猜测版本。

需要把 RAG 结构单独交给审查者时，运行：

```powershell
python scripts/build_public_rag_contract_bundle.py
```

默认生成 `outputs/equipment-rag-public-contract-2026-07-25.zip`。压缩包自带文件级 SHA-256 清单，并明确排除 SQLite、PDF、图片和 Aspen 工程。

这套合同解决的是“程序用了哪个数据库、数据库有没有被替换、该数据能用到哪一步”。它不宣称当前 Phase 1 的所有标准表和图都已结构化，也不把供应商曲线、项目材料规范或生产 Aspen COM 验证伪装成已完成。
