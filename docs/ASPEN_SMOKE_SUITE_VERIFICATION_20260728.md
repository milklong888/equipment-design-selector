# Aspen S01–S06 实际运行与选型门禁报告

## 结论

2026-07-28 在本机 Aspen Plus V14（COM ProgID `Apwn.Document.40.0`）上，用派生引擎 1.9.5 对 continuation pack 的 S01–S06 六个 BKP 做了严格串行运行。批任务状态为 `PASS`，验收模式为 `equipment_selection`：

- 6/6 案例完成，0 个批任务失败；
- 6/6 案例可继续设备选型；
- 6/6 物理设备都有程序生成的具体候选；
- 19/19 物理管线都有程序生成的具体候选；
- 6/6 源 BKP 在运行前后 SHA-256 相同；
- 3/6 案例具有干净原始历史并达到正式工艺基础门槛；
- 其余 3/6 案例保留原始历史或物性阻断，没有因“程序执行成功”被错误升级。

本报告证明当前代码、当前 Aspen V14 和这六个测试文件的实际行为，不证明所有 Aspen 版本、物性方法、用户模型或生产工程均兼容。

## 逐案结果

| 案例 | 流程类型 | 设备候选 | 管线候选 | 原始运行历史 | 输运物性门禁 | 源文件未变 |
| --- | --- | ---: | ---: | --- | --- | --- |
| S01 | absorber | 1/1 | 4/4 | `DIRTY_RUN_EVIDENCE` | `PASS` | 是 |
| S02 | shortcut_distillation | 1/1 | 3/3 | `CLEAN_RUN_EVIDENCE` | `PASS` | 是 |
| S03 | rigorous_distillation | 1/1 | 3/3 | `CLEAN_RUN_EVIDENCE` | `PASS` | 是 |
| S04 | liquid_liquid_extraction | 1/1 | 4/4 | `CLEAN_RUN_EVIDENCE` | `PASS` | 是 |
| S05 | stoichiometric_reactor | 1/1 | 2/2 | `DIRTY_RUN_EVIDENCE` | `PASS` | 是 |
| S06 | equilibrium_reactor | 1/1 | 3/3 | `DIRTY_RUN_EVIDENCE` | `BLOCKED_UNVERIFIABLE_ASPEN_PHASE_AND_VISCOSITY` | 是 |

## S06 缺状态管线的实际退化

S06 的 `L-PRO` 没有可用正流量、相态、压力或温度。程序没有返回空名称或“非标准型”，而是输出：

`DN25 / φ34×4 mm / PN16 / 20钢 / 对焊（BW） / CS20-PN16-BW-CA1.5`

该分支的 `terminal_selection.default_applied=true`，并固定输出最高级警告：

- 这是程序登记的最低完整规格占位分支；
- 水力计算未执行；
- 只用于保证设备选型一览表存在具体、可追溯的程序候选；
- 禁止直接采购、施工或报审；
- 需要补齐正流量、相态、压力、温度及正式项目管道等级后重算。

## 执行与防篡改机制

1. 每案先校验清单中的预期 SHA-256。
2. Aspen 只打开独立暂存副本，源 BKP 为只读输入语义。
3. 六案严格串行；单案失败不会跳过后续案例。
4. 每案单独保存 `.his`、运行状态、输运物性验证、Aspen 导出、设备推导、PFD 和 worker 结果。
5. 汇总报告增量、原子写入，异常中断时已完成案例仍可审计。
6. “可继续选型”和“正式工艺基础”是两个独立门禁。

## 本次证据哈希

- `aspen_suite_report.json`：`F675A0F84D6B98FB04F72AD00E805481B6323BC0EFC5AC9B706980DFE4E1B49E`
- `aspen_suite_report.md`：`7915A3470584088DA2E7E239AE91FC7CEE7F0D3A30339EB192C7B978DD2D695D`
- Agent 响应：`357C3496B08D328BA0B1938C9EBA03EC887ACB2E0FDB072240B7758C68FEC694`
- 本次源码权威清单：`8C06296290C4FB06BCDF1B26F51CA80CC51A6BD6F985B6310DD61D9E8F0AEBCD`

原始运行产物位于本地忽略目录 `tmp/real_smoke_suite_1_9_5_20260728/`，不会提交真实 BKP 或大体积运行文件到普通 Git 历史。

## 实用目录版成品验收

由于运行时知识资产约 1.42 GiB，最终成品采用 Windows 目录版，避免单文件自解压在超大 SQLite 资产上出现不可靠行为。`_internal` 必须与对应 EXE 保持原目录关系，不能只复制 EXE。

- GUI：`D:\equipment-selector-release-20260728\EquipmentDesignGraphApp\EquipmentDesignGraphApp.exe`
  - EXE SHA-256：`151522C2559E50F71DA29C2F0960A9E634226D85D10C50C77EC5F36FF43B083E`
  - 目录文件数：7773；目录大小：1,527,249,184 bytes
  - 隐藏冷启动 20 秒后主窗口已创建、进程响应正常，随后按精确进程号关闭验收实例
- Agent：`D:\equipment-selector-release-20260728\EquipmentDesignAgentCLI\EquipmentDesignAgentCLI.exe`
  - EXE SHA-256：`A64D6B3E83C0B352760DE3A9AC4135F45B45591AAEB84A2F6FCB136553D6FBF4`
  - 目录文件数：7773；目录大小：1,527,291,904 bytes
  - 打包后 17/17 自检通过；能力查询实际返回 `aspen_import`、`aspen_derive` 和 `aspen_suite`
- 两个目录中的 `runtime_asset_manifest.json` SHA-256 均为
  `5A8D74761C27C4EFA2745C597A64ED43728A906EF0D9646B355D9E432309059F`
- 运行时清单包含 6761 个项目资产，共 1,489,356,444 bytes；Tk/Tcl 运行目录和标准知识数据库均存在。
