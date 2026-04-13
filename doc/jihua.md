# VulnHelper Rich 终端工作台改造方案

## Summary
把当前 `vulhelper` 的 `print/input` CLI 升级为一个全屏 Rich 工作台：固定顶部状态栏、固定中部时间线聊天区、固定底部单行 `thinking` ticker、固定最底部输入区。业务编排逻辑保持不变，主要新增一层终端壳和展示模型，让主区只展示结果内容，`thinking` 只在底部单行滚动，不写入主聊天流。

## Key Changes
- 新增一个 Rich 全屏 shell，负责布局、刷新节流、终端尺寸变化、输入锁定/解锁和事件订阅；默认在交互式终端下进入全屏工作台，不适合时自动降级到现有朴素 CLI。
- 输入方案使用 `Rich + prompt_toolkit`：输入框固定在最下方，支持基础编辑和历史，模型处理期间显示忙碌态并临时禁用提交，避免界面抖动。
- 中间主区采用“时间线聊天流”，按轮次保留最近可见内容；每轮包含用户输入卡片，以及结构化结果卡片：
  - 确认阶段：查询理解/确认摘要卡片
  - 报告阶段：分析结论卡片 + 漏洞列表表格卡片 + 修复建议卡片
  - 下钻阶段：过滤结果表格卡片
  - 异常阶段：错误卡片
- 扩展 `UserTurnOutput` 的公开展示接口，保留现有 `markdown` 兼容旧路径，同时新增一个可选的结构化 `presentation` 载荷，供终端 UI 直接渲染，不再依赖从 markdown 反解析。
- Orchestrator 在各状态返回时同步填充 `presentation`：
  - `waiting_for_confirmation` 返回确认摘要块
  - `report_ready` 返回分析、修复建议、漏洞表格块
  - `drilldown_ready` 返回过滤表格块
  - `failed` / `restart` 返回 notice/error 块
- 顶部状态栏固定展示：当前阶段、会话状态、短会话 ID、处理中标记；阶段来源于 agent event，状态来源于 `UserTurnOutput.state`，文案统一中文化。
- 底部 `thinking` ticker 改为独立组件：继续消费 `thinking_delta`，做空白归一化与字符窗口裁剪，只保留最近片段；以固定节奏向左滚动；阶段切换时重置；完成后清空；绝不进入聊天历史。
- 现有 `ThinkingStreamPrinter` 的逻辑迁移为 ticker/view-model 组件，CLI 入口改为驱动 shell，而不是直接向 stdout 写回车覆盖行。
- 依赖变更：新增 `rich`、`prompt_toolkit`；不引入 Textual，不做复杂键盘导航或应用内滚动。

## Public Interfaces / Types
- `UserTurnOutput` 增加可选 `presentation` 字段，作为终端/UI 的首选渲染来源；`markdown` 保留为兼容和降级输出。
- 新增结构化展示类型，最少覆盖这些 block kind：`user_message`、`confirmation`、`analysis`、`vulnerability_table`、`fix_strategy`、`notice`、`error`。
- agent 事件订阅接口保持不变，Rich shell 继续沿用 `subscribe_agent_events(listener)`，只替换消费端。

## Test Plan
- 单元测试：
  - ticker 只保留最近窗口、阶段切换重置、完成后清空、不会把 thinking 写进 transcript
  - marquee 左滚节奏和节流逻辑稳定
  - 状态栏能正确映射 phase/state/busy 文案
  - `presentation` block 到 Rich 卡片/表格的映射正确
- 集成测试：
  - `new_query -> confirm -> drilldown` 流程下，返回的 `presentation` 结构完整且和状态一致
  - 全屏 shell 在一次完整会话中能稳定累积时间线内容，并只保留当前窗口可见区域
  - 非交互终端或依赖不可用时自动降级到旧 CLI 输出
- 回归测试：
  - 保留现有 `markdown` 路径的测试，确保老调用方式不坏
  - 保留并改造 `test_thinking_printer.py` 为 ticker/view-model 测试

## Assumptions
- 第一版不做应用内历史滚动，只显示当前窗口内最近若干轮消息。
- 第一版不做双栏主区，主区保持单列聊天时间线，信息最清晰。
- `thinking` 显示原始 delta 的归一化短片段，不做摘要改写。
- 全屏工作台使用简洁、低噪音样式：顶部单行状态栏，中部卡片化结果，底部单行 ticker，避免花哨边框和主区污染。
