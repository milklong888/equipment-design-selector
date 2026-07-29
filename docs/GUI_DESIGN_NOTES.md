# GUI 设计依据与实现说明

## 目标

本软件不是展示型网页，而是需要长时间核对参数、分支、公式和证据门的 Windows 工程工具。界面优先级依次是：

1. 用户始终知道当前处于输入、计算、审核还是导出阶段；
2. 高频操作容易找到，低频技术信息仍可到达但不挤占主导航；
3. 输入缺口用文字和颜色共同表达，不把“有颜色”当成唯一提示；
4. 键盘可以完成导入、运行、筛选、保存、重算和导出；
5. 美化服务于信息层级和可读性，不伪装成程序并未实现的原生 WinUI 能力。

## 采用的官方设计依据

- Microsoft Windows 11 设计原则强调 effortless、calm、familiar、coherent，并建议用层级、颜色、字形和动作反馈帮助用户聚焦：
  <https://learn.microsoft.com/en-us/windows/apps/design/design-principles>
- Microsoft Windows 应用界面指南把布局、导航、控件、可用性、写作和无障碍作为同一套体验问题：
  <https://learn.microsoft.com/en-us/windows/apps/design/guidelines-overview>
- Microsoft Commanding basics 建议突出主要命令、把相关命令分组，并避免一次展示过多同级命令：
  <https://learn.microsoft.com/en-us/windows/apps/design/basics/commanding-basics>
- Microsoft 键盘交互指南要求常见动作可以通过键盘到达：
  <https://learn.microsoft.com/en-us/windows/apps/design/input/keyboard-interactions>
- Microsoft 无障碍清单要求提供键盘访问、清楚的名称、可见焦点和不依赖单一感官的状态提示：
  <https://learn.microsoft.com/en-us/windows/apps/design/accessibility/accessibility-checklist>
- WCAG 2.2 建议交互目标至少达到 24×24 CSS 像素，并要求键盘焦点可见、输入错误能以文字识别：
  <https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html>
  <https://www.w3.org/WAI/WCAG22/Understanding/focus-visible>
  <https://www.w3.org/WAI/WCAG22/Understanding/error-identification>
- Tkinter `ttk` 官方文档用于约束可实现的主题、状态映射和控件行为：
  <https://docs.python.org/3/library/tkinter.ttk.html>

## 已实现

### 1. 持久工作流状态

顶部流程条固定显示“输入 → 确定性计算 → 审核与调整 → 导出”。后台运行时显示进度；结果生成后直接进入审核阶段；导出成功后显示文件名。

### 2. 结果导航从十个同级页签改为四个任务组

- 流程：PFD；
- 选型结果：客户交付、分支选择、参数卡、候选型号；
- 计算与校核：推导与调整、校核与缺口、公式链；
- Agent 与数据：大模型调控、Agent 组织答案、机器 JSON。

底层结果对象和原有功能没有删除，只改变信息层级。

### 3. 手动字段检索和状态筛选

可按中文标签、字段 ID、分组或单位检索；可切换“全部默认字段 / 只看主计算必填 / 只看待补字段 / 只看已修改”。筛选项在展开框中显示中文，内部仍保留稳定代码。

主计算缺口、候选闭合缺口和正式证据门继续分层显示。缺少主输入时程序不静默伪造数据：仍按登记降级链生成候选，同时将焦点定位到首个待补字段并在状态栏说明。

### 4. 高频命令和快捷键

| 动作 | 快捷键 |
|---|---|
| 选择 Aspen 文件 | `Ctrl+O` |
| 运行当前入口主操作 | `Ctrl+Enter` |
| 定位手动字段筛选 | `Ctrl+F` |
| 保存 JSON | `Ctrl+S` |
| 导出报告 | `Ctrl+Shift+S` |
| 仅重算当前设备 | `Ctrl+R` |
| 切换四个输入入口 | `Alt+1`～`Alt+4` |
| 使用说明 | `F1` |

快捷键与按钮共用同一函数，不绕过确定性校核、证据门或重算审计。

### 5. 可读性和交互反馈

- 按钮、输入框、页签和表格行增大垂直间距；
- 输入焦点使用强调色边框；
- 顶部标题区和命令区采用响应式网格，支持的最小窗口宽度内仍保留状态、导入、运行、保存和导出；
- 页签改用扁平表面、弱边框和明确的选中/悬停状态，减少旧式凸起控件感；
- 启动窗口按当前屏幕可用尺寸居中收缩，不再固定生成超出屏幕的 1480×900 窗口；
- 表格标题加粗，行高统一；
- 缺口卡使用红/黄/绿背景，但同时保留完整文字；
- 尚无结果时保存和导出快捷按钮不可用；
- 尚无 PFD 时显示“导入—运行—点设备查看参数”的空状态引导，而不是留下一块无法判断下一步的空白区域；
- 客户交付、参数卡和候选型号表支持双击或回车打开完整只读字段，并可一键复制，避免窄表格把长型号、证据门和程序方案截断；
- 后台任务运行时显示活动进度并禁用冲突操作。

## 当前技术边界

界面基于 Tkinter/ttk。当前实现采用 Windows 风格的信息层级、间距、状态与快捷操作，但没有宣称具备 WinUI 3 的 Mica、原生触摸动画、完整 UI Automation 语义或高对比主题自动适配。这些能力若要正式实现，需要迁移到 WinUI 3、WPF 或 Qt，并进行 Narrator、键盘遍历、高 DPI、多显示器和高对比模式专项测试。

## 验证

- GUI 回归测试覆盖四层结果导航、中文筛选、快捷键绑定、流程状态、单设备重算和恢复默认；
- 响应式回归测试覆盖支持的最小窗口宽度下顶部状态区和主命令区不越界，以及空 PFD 的操作引导；
- 现有 Aspen、Agent、参数卡、公式链和报告导出控件继续由原测试覆盖；
- 源码权威清单重新生成并通过路径、大小和 SHA-256 校验。
