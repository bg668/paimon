# paimonsdk 路线图 v1

## 1. 文档目标

本文档用于把当前 `paimonsdk` 的后续开发方向固化为一份可执行的近期路线图，作为设计评审、开发排期和后续文档更新的共同依据。

本文档不重复描述当前实现的所有细节。关于当前能力边界，请结合 [paimonsdk 功能说明](./paimonsdk%20功能说明.md) 和 [paimonsdk 开发者参考](./paimonsdk%20开发者参考.md) 一起阅读。

## 2. 核心边界

`paimonsdk` 的定位是单 agent runtime kernel，而不是 workflow framework。

SDK 层只保留以下 6 类通用能力：

1. 模型适配
2. 上下文对象
3. 工具 schema 与执行
4. agent loop
5. 事件流
6. 可序列化 session

以下能力明确留在应用层，不进入 SDK：

- session 状态机
- 提示词装配
- 项目上下文与项目记忆
- 任务单、计划文件、角色协作
- 审批策略
- 前端 UI
- CLI / tmux / 外部工具目录
- 多 agent workflow、DAG、协同策略

判断标准只有一条：如果某项能力不是“单 agent 生命周期的最小通用内核”，就不应进入 SDK。

## 3. 当前实现判断

当前 `paimonsdk` 已经具备较清晰的 runtime kernel 骨架，主要体现在以下方面：

- 已有稳定的 `Agent` 入口、loop、tool execution、状态视图、事件机制和取消控制。
- 已有 OpenAI 风格 `chat.completions` 适配器，流式与非流式路径已基本统一。
- 上层应用仓已经把 session、审批、工作流和渲染放在 SDK 之外，整体分层方向是对的。

但和目标边界相比，当前实现仍有几个关键缺口：

- 缺少真正的“可序列化 session”。目前只有内存态快照，没有 checkpoint、恢复、回放能力。
- 事件流还不够稳定和可追踪，缺少 `run_id`、顺序号、稳定事件 envelope 等基础协议字段。
- `AgentOptions` 与 `AgentLoopConfig` 已开始混入部分应用层或 provider 细节，存在配置面膨胀的风险。
- tool contract 已有骨架，但结果契约、artifact 边界和错误治理仍偏弱。

因此，近期工作重点应放在“补全 kernel 缺失能力”和“收缩边界”上，而不是扩展更多高层抽象。

## 4. 路线总览

近期路线按 `P0 / P1 / 明确不做` 管理。

### 4.1 P0

- `P0-1` 单 agent session 的序列化、恢复、回放
- `P0-2` 事件流协议稳定化

### 4.2 P1

- `P1-1` 收缩 SDK 配置面，清理边界泄漏
- `P1-2` 强化 tool contract / artifact contract
- `P1-3` 配套工程治理：发布、版本、契约测试

### 4.3 明确不做

- SDK 内建 subagent
- SDK 内建 workflow / DAG
- SDK 内建角色协作与审批策略
- SDK 内建项目记忆或业务状态机

## 5. P0 路线

### 5.1 P0-1 单 agent session 的序列化、恢复、回放

#### 目标

把“可序列化 session”补成 SDK 一等能力，使 SDK 不再只支持内存态运行，而能够围绕单 agent 生命周期形成稳定边界。

#### 需要落地的能力

- 定义 `AgentSession` 或等价对象，显式承载：
  - transcript
  - system prompt
  - model 引用信息
  - tools 引用信息
  - steering / follow-up 队列
  - metadata
  - 最近稳定边界
- 定义 checkpoint 数据结构，支持导出与导入。
- 支持从稳定边界恢复继续执行。
- 支持事件日志回放。

#### 明确不做

- 不做 token 级流中断恢复。
- 不做跨 provider 的 transport 恢复。
- 不把业务 session 状态机放进 SDK。

#### 价值

- 满足“SDK 只负责单 agent 生命周期”的核心要求。
- 让 CLI、UI、状态文件、调试工具能够围绕统一 session 协议协同。
- 为后续发布版隔离、版本迁移和问题复盘提供基础设施。

#### 完成标志

- 可以把一个 agent 的稳定状态完整导出为可持久化结构。
- 可以从该结构恢复到可继续运行状态。
- 可以基于事件日志重放一轮运行的关键过程。

### 5.2 P0-2 事件流协议稳定化

#### 目标

把现有事件从“运行时内部通知”提升为“稳定外部协议”，使其可用于状态同步、日志、调试和回放。

#### 需要落地的能力

- 为所有事件增加统一 envelope，至少包括：
  - `event_id`
  - `run_id`
  - `turn_id`
  - `seq`
  - `timestamp`
- 明确事件顺序与归约规则。
- 明确 message / tool execution / turn 的关联关系。
- 明确哪些事件可用于 session 重建，哪些只用于展示。

#### 明确不做

- 不在 SDK 中引入复杂的遥测平台抽象。
- 不把日志存储方案绑死到某个外部系统。

#### 价值

- 让事件流真正成为 CLI、UI、日志系统、回放器之间的公共接口。
- 降低当前运行过程“对外可见但不可重建”的问题。
- 为 session checkpoint 和 replay 提供基础协议。

#### 完成标志

- 事件具备稳定身份和顺序信息。
- 外部应用可以只靠事件流重建主要运行过程。
- 文档中明确说明事件协议与使用边界。

## 6. P1 路线

### 6.1 P1-1 收缩 SDK 配置面，清理边界泄漏

#### 目标

把 SDK 配置面重新收敛到 runtime kernel 必需项，避免应用层策略和 provider 细节继续侵入核心接口。

#### 处理原则

- `AgentOptions` 只保留 kernel 真正消费且具有稳定语义的字段。
- provider 特有请求参数尽量下沉到 adapter 配置。
- 未被 runtime 真正消费的字段，不进入长期公开 API。

#### 重点关注项

- 审查 `session_id`、`base_url`、`on_payload`、`thinking_budgets`、`max_retry_delay_ms` 等字段是否应保留在 kernel 接口。
- 将 OpenAI 风格的 transport / request 参数与通用 runtime 参数分离。
- 重新梳理 `AgentOptions` 与 `AgentLoopConfig` 的职责。

#### 本轮审查结论

- kernel 必需项收敛为：`system_prompt`、`model`、`messages`、`session`、`tools`、`stream_fn`、`convert_to_llm`、`transform_context`、`before_tool_call`、`after_tool_call`、`steering_mode`、`follow_up_mode`、`tool_execution`、`thinking_level`、`metadata`。
- `AgentLoopConfig` 只保留单轮运行真正需要的字段：模型标识、`stream_fn`、上下文转换、消息供应器、工具执行模式、tool hooks、`thinking_level` 和 `metadata`。
- `session_id` 改为 `AgentSession` 协议字段，不再作为 `AgentOptions` / `AgentLoopConfig` 公开配置。
- `base_url`、`api_key`、`temperature`、`top_p`、`max_tokens` 等 provider 请求参数下沉到 OpenAI adapter 专属配置。
- `on_payload`、`thinking_budgets`、`max_retry_delay_ms` 因默认主链路不消费，已从 kernel 公开配置移除。

#### 价值

- 减少 API 假抽象。
- 降低后续适配器扩展时的耦合。
- 避免 SDK 逐步演化成“把应用策略和 provider 选项都塞进来”的大杂烩。

### 6.2 P1-2 强化 tool contract / artifact contract

#### 目标

在不扩大 SDK 边界的前提下，把 tool 输入输出契约做强，使工具调用从“能跑”提升到“稳定、可治理、可演化”。

#### 需要落地的能力

- 明确 `content` 与 `details` 的语义边界：
  - `content` 面向模型
  - `details` 面向应用
- 补充结果契约与错误契约。
- 如确有需要，增加最小 artifact 引用能力，但不单独引入大框架。

#### 明确不做

- 不把 artifact 系统扩展成独立存储平台。
- 不引入复杂的多模态资产编排框架。

#### 价值

- 让工具执行更适合被上层应用治理和复用。
- 让 `before_tool_call` / `after_tool_call` 不只是补丁式 hook，而是建立在稳定 contract 上。

### 6.3 P1-3 配套工程治理：发布、版本、契约测试

#### 目标

把 `paimonsdk` 真正按发行版管理，而不是按“应用内共享源码目录”管理。

#### 需要落地的能力

- 明确 SDK 的发布方式与版本策略。
- 让上层 app 通过依赖安装固定版本，而不是直接引用源码副本。
- 增加 contract tests，约束上层 app 实际依赖的行为语义。

#### 价值

- 让 SDK 更新与 app 升级解耦。
- 让“像第三方库一样使用 SDK”成为可操作的工程实践。
- 降低多份实现副本造成的漂移风险。

## 7. 明确不做的方向

以下方向不进入近期路线，也不建议放入 SDK：

### 7.1 SDK 内建 subagent / workflow / DAG

理由：

- 这会把 SDK 从 runtime kernel 推向 workflow framework。
- 它不属于单 agent 生命周期的最小通用能力。
- 这类编排抽象应由应用层自行组合。

### 7.2 SDK 内建角色协作、审批、计划管理

理由：

- 这些都是业务工作流与交互策略问题。
- 不同 app 的约束差异过大，不适合做成 SDK 公共能力。

### 7.3 SDK 内建项目记忆与业务状态文件

理由：

- 这类状态天然依赖项目和业务上下文。
- 应由 app 层或独立存储层维护，不应进入 runtime kernel。

## 8. 建议执行顺序

建议按以下顺序推进：

1. 先做 `P0-1`，建立 `AgentSession / Checkpoint / Resume / Replay` 的基础模型。
2. 再做 `P0-2`，让事件流具备稳定 envelope 和可重建性。
3. 然后推进 `P1-1`，收缩公开配置面，避免新增能力继续长偏。
4. 最后做 `P1-2` 和 `P1-3`，强化工具契约并补齐发布治理。

## 9. 对后续文档的要求

路线图开始落地后，以下文档需要同步维护：

- [paimonsdk 功能说明](./paimonsdk%20功能说明.md)
- [paimonsdk 开发者参考](./paimonsdk%20开发者参考.md)
- [概要设计](./概要设计.md)
- [详细设计](./详细设计.md)

原则如下：

- 路线图描述“准备做什么”。
- 功能说明描述“已经做成了什么”。
- 开发者参考描述“应该如何使用”。
- 设计文档描述“为什么这么设计、内部如何组织”。

只有这四类文档保持同步，路线图才不会沦为孤立的计划文件。
