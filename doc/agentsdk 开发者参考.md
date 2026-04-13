# agentsdk 开发者参考

## 1. 文档说明

本文档面向希望接入和调用 `agentsdk` 的开发者，重点说明各项能力的调用方式、关键参数含义、运行时行为，以及最小可用示例。全文以仓库中的 `agentsdk/` 目录实现和 `agentsdk/tests/` 中的测试行为为准，不以早期设计稿或未来规划为准。

本文档与《agentsdk 功能说明》的定位不同：

- 《agentsdk 功能说明》回答“已经实现了什么能力、边界在哪里”。
- 《agentsdk 开发者参考》回答“这些能力应该怎么调用、调用后会发生什么、如何快速接入”。

从定位上看，`agentsdk` 更接近可嵌入应用中的 Agent Runtime Kernel，而不是完整的 Agent 平台。它覆盖了多轮对话、工具调用、事件分发、流式处理、运行状态和模型适配等核心能力；技能系统、长期记忆、任务调度、业务工作流和权限体系仍应由上层应用实现。

文档中的示例统一使用 `agentsdk` 作为导入名，因为本仓库的实际包目录名即为 `agentsdk`。如果你的外部工程对该包做过二次封装或发布了兼容别名，请按实际导出名调整导入语句。

阅读建议：

1. 如果你第一次接触该 SDK，先看“快速开始”。
2. 如果你已经知道整体能力，直接跳转到对应 API 章节。
3. 如果你在接入中遇到边界问题，优先看“错误处理与边界行为”。

如果你只是想快速建立整体心智模型，可以先建立以下三个基本认知：

- `Agent` 是对外统一入口。
- `AgentOptions` 用来声明模型、工具和扩展点。
- `prompt()` 发起的是一次完整 `run`；结果不会直接作为返回值返回，而是通过事件和 `agent.state` 暴露。

相关文档：

- [文档导航](./README.md)
- [agentsdk 功能说明](./agentsdk%20功能说明.md)
- [概要设计](./概要设计.md)
- [详细设计](./详细设计.md)

### 1.1 术语约定

为减少歧义，本文统一使用以下术语：

- `run`：一次完整执行过程，可能包含多轮模型生成、工具调用和排队消息消费。
- `turn`：一次 assistant 生成及其相关工具回写构成的一轮执行边界。
- `transcript`：当前会话中已经落地的结构化消息历史。
- “收敛”：指流式中间态或底层异常最终被归并为稳定消息或稳定结果的过程。
- “适配器”：指负责在 SDK 内部消息结构与具体模型协议之间做转换的组件。

## 2. 快速开始

### 2.1 适用场景

如果你符合下面任意一种情况，就适合先从本章的最小示例入手：

- 你要在 Python 应用中接入一个支持多轮上下文的 Agent。
- 你希望把模型调用、工具执行和消息状态管理收敛到一个统一对象里。
- 你需要流式事件，把运行过程同步到 CLI、Web UI、日志系统或外部状态存储。
- 你希望在一次运行中支持工具调用、继续推进和运行中插入消息，而不是手动管理多轮请求。

如果你的目标是“马上拥有一个完整的智能体平台”，那本 SDK 不是直接面向这个目标的产品。更准确地说，它适合做上层应用、服务端编排层或终端交互层的底层运行时内核。

### 2.2 环境准备

当前仓库的运行前提如下：

- Python 版本：`>= 3.11`
- 依赖管理：推荐使用 `uv`
- 已声明依赖：`openai`、`python-dotenv`、`pytest`

在仓库根目录执行：

```bash
uv sync
cp .env.example .env
```

然后在 `.env` 中填入你的 API Key：

```env
OPENAI_API_KEY=your-real-api-key
```

如果你使用的是 OpenAI 兼容接口而不是官方默认地址，可以额外设置：

```env
OPENAI_BASE_URL=https://your-compatible-endpoint/v1
OPENAI_MODEL=gpt-4o-mini
```

说明如下：

- `OPENAI_API_KEY` 用于初始化 `AsyncOpenAI` 客户端。
- `OPENAI_BASE_URL` 是可选项，只有在你接入兼容网关或代理地址时才需要。
- `OPENAI_MODEL` 也是可选项，不填时示例会默认使用 `gpt-4o-mini`。

仓库中已经有一个更完整的终端示例 [agent_sdk_demo.py](/Users/bg/project/uu-work/agent_sdk_demo.py)。本章给出的代码会更短，目的是先建立最小接入闭环，后续复杂用法再分别在后面章节展开。

### 2.3 最小可运行示例

下面这个例子展示了最短接入路径：创建模型信息、创建适配器、构造 `Agent`、发起一次 `prompt()`，最后从 `agent.state` 中取回本轮的 assistant 消息。

```python
import asyncio
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

from agentsdk import Agent, AgentOptions, AssistantMessage, ModelInfo, TextContent
from agentsdk.adapters.openai_chatcompletions import OpenAIChatCompletionsAdapter


def extract_text(message: AssistantMessage) -> str:
    return "".join(
        block.text
        for block in message.content
        if isinstance(block, TextContent)
    )


async def main() -> None:
    load_dotenv()

    api_key = os.environ["OPENAI_API_KEY"]
    base_url = os.getenv("OPENAI_BASE_URL") or None
    model_id = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    model = ModelInfo(
        id=model_id,
        provider="openai",
        api="chat.completions",
        base_url=base_url or "",
    )

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
    )
    adapter = OpenAIChatCompletionsAdapter(client)

    agent = Agent(
        AgentOptions(
            system_prompt="你是一个简洁、可靠的开发助手。",
            model=model,
            stream_fn=adapter.stream_message,
            temperature=0.2,
        )
    )

    await agent.prompt("请用两句话介绍 agentsdk 的定位。")
    last_message = agent.state.messages[-1]
    if not isinstance(last_message, AssistantMessage):
        raise RuntimeError("最后一条消息不是 assistant 消息，请检查本轮执行状态。")

    if last_message.error_message:
        print(f"[error] {last_message.error_message}")
        return

    print(extract_text(last_message))


if __name__ == "__main__":
    asyncio.run(main())
```

这个示例有几个关键点：

- `Agent` 本身不直接连接模型，真正负责与模型通信的是 `stream_fn`，这里使用的是 `OpenAIChatCompletionsAdapter.stream_message`。
- `prompt()` 不会直接返回一条 assistant 消息；它返回时，这一轮 run 已经完成，结果已经写入 `agent.state.messages`。
- `wait_for_idle()` 仍然有用，但主要用于把 run 放到后台任务执行，或在 `abort()` 之后等待统一收尾。
- `system_prompt` 会保存在 `AgentContext.system_prompt` 中；如果直接使用仓库里的默认 OpenAI 适配器，它会在请求消息列表最前面映射成一条 system message。
- 即使只做最简单的调用，也建议保留 `AssistantMessage.error_message` 的检查，因为模型调用失败、取消或适配层异常都会收敛到 assistant 错误消息中。

如果你想立刻看到流式增量输出，而不是等运行结束后一次性读取最终结果，请继续阅读第 7 章“事件与流式输出”。

### 2.4 运行后会发生什么

上面的示例虽然代码很短，但实际触发的是一次完整 run。按当前实现，它大致会经历下面几个阶段：

1. `prompt("...")` 把字符串自动包装成一条 `UserMessage`，并作为本轮输入加入执行链路。
2. `Agent` 创建本轮上下文快照，内部包含 `system_prompt`、已有 transcript 和注册工具。
3. 运行时调用 `stream_fn`。在这个示例里，`stream_fn` 实际上会转发到 `OpenAIChatCompletionsAdapter.stream_message()`。
4. 适配器把内部消息结构转换成 OpenAI Chat Completions 所需的请求格式，并向模型发起流式请求。
5. SDK 在内部逐步收敛流式事件，构造一条最终的 `AssistantMessage`。
6. 当消息结束后，这条 assistant 消息会被写入 `agent.state.messages`，本轮 run 收尾完成。
7. `await agent.prompt(...)` 返回，此时 `agent.state.is_streaming` 已经回到 `False`，你可以安全地读取 transcript、错误信息和最终消息。

在这个最小示例中，我们没有注册工具，也没有订阅事件，因此你只会在运行结束时看到最终文本。即便如此，底层仍然走的是统一运行时，而不是“一次请求、一次返回”的轻量封装。这一区别决定了后续能力都围绕 run 展开：

- 如果 assistant 消息中出现工具调用，SDK 会继续执行工具并追加后续轮次，而不是立即结束。
- 如果你注册了事件监听器，消息增量、工具执行进度和 run 生命周期事件都会在过程中发出。
- 如果你调用 `abort()`，这次 run 会尽量在合适的检查点收敛为一个 `aborted` 结束，而不是直接以未处理异常结束。

你可以把 `prompt()` 理解成“启动一次 Agent run”，而不是“同步获取一条回复”。后续章节中的 `continue_()`、`steer()`、`follow_up()`、工具调用和流式更新，都建立在这个理解之上。

## 3. 核心对象速览

### 3.1 Agent

`Agent` 是 SDK 的对外统一入口。对调用者来说，几乎所有运行时能力都围绕它展开：发起输入、继续执行、插入消息、取消运行、读取状态、订阅事件，都是通过这个对象完成的。

你可以把 `Agent` 理解成一个“可持续运行的对话执行器”，而不是一个“包装了模型调用的函数”。它内部持有以下几类关键状态：

- 当前会话的 `system_prompt`
- 当前模型信息和工具列表
- 已落地的 transcript
- 正在进行中的流式消息
- steering / follow-up 队列
- 当前 run 的取消控制句柄

对外最常用的方法和属性如下：

| 成员 | 用途 | 什么时候会用到 |
| --- | --- | --- |
| `prompt()` | 发起一轮新的输入 | 新用户问题、新任务开始 |
| `continue_()` | 基于当前 transcript 继续推进 | 上一轮停在可继续边界时 |
| `steer()` | 以较高优先级插入新消息 | 运行中想临时转向 |
| `follow_up()` | 在当前链路收束后追加消息 | 回答结束后自动追问 |
| `abort()` | 请求取消当前运行 | 用户点击停止、超时、切页 |
| `wait_for_idle()` | 等待后台 run 完整结束 | 使用 `create_task(...)` 或 `abort()` 后收尾 |
| `reset()` | 清空 transcript 和运行态 | 重开一个全新会话 |
| `state` | 读取当前状态快照 | 展示历史、查询错误、判断是否在流式中 |
| `subscribe()` | 订阅生命周期和流式事件 | CLI 输出、日志、UI 同步 |

什么时候优先想到 `Agent`：

- 你想封装一个可复用的聊天或问答对象。
- 你想把一次运行中发生的模型调用、工具调用和后续轮次推进收敛到一个地方。
- 你需要一个稳定的状态读取入口，而不是到处散落临时变量。

如果你接下来想看每个方法的调用细节，直接跳到第 4 章。如果你想先理解 `state` 里到底放了什么，可以先记住一点：`Agent` 暴露的是快照视图，适合读取当前状态，不适合当作内部控制句柄直接改写。

### 3.2 AgentOptions

`AgentOptions` 是创建 `Agent` 时的统一配置入口。它决定了这个 `Agent` 用什么模型、有哪些工具、如何与模型通信、在工具调用前后怎么插逻辑，以及运行时的若干行为策略。

从使用习惯上看，`AgentOptions` 可以分成六组：

| 配置组 | 代表字段 | 作用 |
| --- | --- | --- |
| 基础配置 | `system_prompt`、`model`、`messages` | 定义初始上下文和模型身份 |
| 模型接入 | `stream_fn`、`api_key`、`base_url`、`get_api_key` | 说明如何真正调用底层模型 |
| 生成参数 | `temperature`、`top_p`、`max_tokens` | 控制模型生成行为 |
| 工具相关 | `tools`、`tool_execution`、`before_tool_call`、`after_tool_call` | 注册工具并控制调用链 |
| 上下文扩展 | `transform_context`、`convert_to_llm` | 在发给模型前重写上下文 |
| 运行策略 | `steering_mode`、`follow_up_mode`、`thinking_level`、`metadata` | 控制队列消费与补充运行信息 |

对大多数接入方来说，最常用的一组起步配置通常包括三个核心项和一组常见可选项：

- `model`
- `stream_fn`
- 按业务需要决定是否显式设置 `system_prompt`
- 可选的生成参数，如 `temperature`

其中最重要、也最容易被忽略的是 `stream_fn`。`Agent` 本身不会自行决定如何连接模型；如果没有配置 `stream_fn`，运行时会在实际执行时报错。因此，只要你打算真正跑通一次调用，就必须提供一个可用的 `stream_fn`，最常见的做法就是使用 `OpenAIChatCompletionsAdapter.stream_message`。

你通常会在这些场景下显式修改 `AgentOptions`：

- 想切换模型或模型供应商信息
- 想注册一组工具
- 想在工具调用前后执行治理逻辑
- 想定制上下文压缩或消息协议转换
- 想调整 steering / follow-up 的消费模式

第三章只做对象速览，第 5 章会逐项展开 `AgentOptions` 的字段含义、默认行为和推荐配置方式。

### 3.3 ModelInfo

`ModelInfo` 用来描述当前 `Agent` 正在面向哪个模型运行。它不是模型客户端本身，而是一份轻量的模型元数据，主要用来给运行时和适配层提供统一的模型身份信息。

当前结构中的关键字段包括：

| 字段 | 说明 |
| --- | --- |
| `id` | 模型 ID，例如 `gpt-4o-mini` |
| `provider` | 模型提供方标识，例如 `openai` |
| `api` | 所用协议类型，例如 `chat.completions` |
| `base_url` | 可选的兼容接口地址 |
| `reasoning` | 是否属于带 reasoning 特性的模型 |
| `input_modalities` | 输入模态说明，例如文本、图像 |
| `cost` | 价格信息结构 |
| `context_window` / `max_tokens` | 上下文和输出相关上限 |

在当前实现里，接入方最少通常只需要关心四个字段：

- `id`
- `provider`
- `api`
- `base_url`

这是因为适配器真正发请求时最依赖的是模型身份和协议类型，而计费、上下文窗口、模态能力等字段更偏向“描述信息”，当前运行时不会对这些字段做复杂调度决策。

一个最常见的初始化方式如下：

```python
model = ModelInfo(
    id="gpt-4o-mini",
    provider="openai",
    api="chat.completions",
    base_url="",
)
```

如果你要接兼容 OpenAI 协议的网关，也仍然建议沿用 `provider="openai"` 和 `api="chat.completions"`，并把差异放在 `base_url` 上。这样可以最大程度复用现有适配器。

### 3.4 消息模型

消息模型是整个运行时的核心数据结构。无论是用户输入、assistant 输出，还是工具执行回写，最终都会表现为结构化消息并进入 transcript。当前对外最常见的三类消息如下：

| 消息类型 | 作用 | 典型来源 |
| --- | --- | --- |
| `UserMessage` | 表示用户或调用侧输入 | `prompt()` 传入字符串或显式构造消息 |
| `AssistantMessage` | 表示模型输出 | 模型流式结果收敛后的最终消息 |
| `ToolResultMessage` | 表示工具执行结果 | SDK 执行工具后自动回写 |

消息中的内容并不是单一字符串，而是由内容块组成。当前常见的内容块包括：

| 内容块 | 作用 |
| --- | --- |
| `TextContent` | 文本内容 |
| `ImageContent` | 图片输入或图片结果 |
| `ThinkingContent` | thinking / reasoning 内容 |
| `ToolCallContent` | assistant 发起的工具调用描述 |

这意味着 transcript 不是“纯文本聊天记录”，而是一段结构化历史。对于开发者来说，这个设计有三个直接影响：

- 当你调用 `prompt("hello")` 时，SDK 会自动把它包装为带 `TextContent` 的 `UserMessage`。
- 当 assistant 想调用工具时，消息里出现的不是普通文本，而是 `ToolCallContent`。
- 当工具执行完成后，结果不会直接塞回 assistant 文本，而是落成一条独立的 `ToolResultMessage`。

如果你只是做最简单的文本问答，通常只会直接接触到 `UserMessage`、`AssistantMessage` 和 `TextContent`。但一旦开始处理图片、工具调用或流式推理，理解这些结构化内容块就很关键了。

第 6 章会分别展开每种消息和内容块的字段结构、构造方式与使用建议。

### 3.5 工具模型

工具模型用于描述“模型可以调用什么外部能力”。在当前 SDK 中，工具本身通过 `AgentTool` 协议定义，工具返回值通过 `AgentToolResult` 表达。

一个工具对象至少需要回答五个问题：

| 成员 | 作用 |
| --- | --- |
| `name` | 模型调用时使用的工具名 |
| `label` | 展示或标识用名称 |
| `description` | 给模型看的能力说明 |
| `input_schema` | 工具参数结构 |
| `execute()` | 真正执行工具逻辑 |

此外，工具还可以提供：

- `prepare_arguments`：先把模型生成的原始参数转换成你真正想要的参数结构
- `on_update` 回调支持：在长任务执行中上报中间进度

工具执行结束后，返回值会被包装为 `AgentToolResult`。它包含两部分：

| 字段 | 作用 |
| --- | --- |
| `content` | 要回写到对话里的可见内容 |
| `details` | 附带的结构化细节，供业务层使用 |

从调用者视角看，工具模型的职责边界很清楚：

- `AgentTool` 定义“如何被模型调用”
- `ToolCallContent` 表达“模型已经请求调用某个工具”
- `ToolResultMessage` 表达“工具执行结果已经回到 transcript”

如果你接下来要实现第一个工具，重点不是先追求复杂能力，而是先把 `name`、`input_schema` 和 `execute()` 三件事定义清楚。第 8 章会给出最小工具示例、参数准备、串行/并行执行模式和错误处理方式。

### 3.6 事件模型

事件模型是 SDK 对外暴露运行过程的主要方式。`Agent` 在执行过程中会不断发出结构化事件，调用侧可以通过 `subscribe()` 订阅这些事件，把它们用于终端输出、页面刷新、日志采集或外部状态同步。

当前事件大致分成四层：

| 事件层级 | 代表事件 | 说明 |
| --- | --- | --- |
| Agent 级 | `AgentStartEvent`、`AgentEndEvent` | 一次 run 的开始和结束 |
| Turn 级 | `TurnStartEvent`、`TurnEndEvent` | 一轮 assistant 生成与工具回写的边界 |
| Message 级 | `MessageStartEvent`、`MessageUpdateEvent`、`MessageEndEvent` | 单条消息的流式开始、增量更新和结束 |
| Tool 级 | `ToolExecutionStartEvent`、`ToolExecutionUpdateEvent`、`ToolExecutionEndEvent` | 单次工具执行过程 |

对多数接入方来说，最常用的是这三类：

- `MessageUpdateEvent`：做打字机式流式输出
- `ToolExecutionUpdateEvent`：展示工具执行进度
- `AgentEndEvent`：在一轮执行完全结束时触发收尾逻辑

事件模型和 `agent.state` 的区别可以简单理解为：

- 事件回答“刚刚发生了什么”
- 状态回答“此刻系统是什么样”

如果你在做 CLI、聊天 UI 或日志落盘，通常会同时用到两者：用事件推送增量变化，用 `state` 做最终一致的快照读取。第 7 章会详细展开如何订阅事件、如何处理流式更新，以及不同事件的推荐使用方式。

## 4. Agent 生命周期与核心方法

### 4.1 `prompt()`

`prompt()` 用于发起一轮新的输入，是 `Agent` 最常用的入口方法。对调用者来说，它的含义不是“立即拿到一条模型回复”，而是“启动一次新的 run”。

方法签名如下：

```python
await agent.prompt(
    input_value: str | AgentMessage | list[AgentMessage],
    images: list[ImageContent] | None = None,
)
```

参数说明：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `input_value` | `str \| AgentMessage \| list[AgentMessage]` | 本轮输入，可以是字符串、单条消息或消息列表 |
| `images` | `list[ImageContent] \| None` | 仅在 `input_value` 为字符串时使用，会和文本一起组装成一条 `UserMessage` |

当前实现中的输入归一化行为如下：

- 如果传入字符串，SDK 会自动包装成一条 `UserMessage`，其 `content` 默认先放入一段 `TextContent`。
- 如果同时传入 `images`，这些 `ImageContent` 会被追加到同一条 `UserMessage.content` 中。
- 如果传入的是单条消息对象，SDK 不会再自动包装。
- 如果传入的是消息列表，SDK 会按原样作为本轮 prompt 列表处理。

最常见的调用方式：

```python
await agent.prompt("请总结这段代码的职责")
```

如果你想显式构造消息，也可以这样写：

```python
from agentsdk import TextContent, UserMessage

message = UserMessage(content=[TextContent(text="请解释这个异常")])
await agent.prompt(message)
```

运行行为需要特别注意三点：

1. `prompt()` 期间如果 `Agent` 已经有活跃 run，会抛出 `AgentAlreadyRunningError`。
2. `prompt()` 本身不返回 assistant 消息；结果会通过事件流和 `agent.state.messages` 暴露。
3. 一次 `prompt()` 可能触发多轮模型调用和多次工具执行，直到没有更多 tool calls、steering 或 follow-up 需要处理时才会结束。

典型时序如下：

1. 归一化输入为消息列表。
2. 发送 `agent_start`、`turn_start`、`message_start`、`message_end` 等事件，把 prompt 消息写入当前 run。
3. 调用模型生成 assistant 消息。
4. 如果 assistant 含有 `ToolCallContent`，执行工具并写回 `ToolResultMessage`。
5. 如果 run 中新增了 steering 或 follow-up，继续推进后续轮次。
6. 最后发送 `agent_end`，run 收尾完成。

使用建议：

- 如果你是直接 `await agent.prompt()`，它返回时状态已经稳定；只有把 run 放到后台执行时，才需要额外调用 `wait_for_idle()`。
- 如果你正在做流式 UI，不要等 `prompt()` 返回再做展示，而是用 `subscribe()` 订阅事件。
- 如果你要在运行中插入新的高优先级消息，不要再次调用 `prompt()`，而应使用 `steer()` 或 `follow_up()`。

### 4.2 `continue_()`

`continue_()` 用于基于当前 transcript 边界继续推进，而不是重新构造一条新的 prompt。它最适合用于“当前会话还没有结束，只是停在某个可继续的边界上”的场景。

理解 `continue_()` 时，最关键的一点是：它有严格的前置条件，不等于无条件“再跑一次”。

当前实现中的规则如下：

| 当前 transcript 末尾状态 | 是否允许 `continue_()` | 说明 |
| --- | --- | --- |
| transcript 为空 | 否 | 会抛出 `InvalidContinuationError` |
| 最后一条是 `assistant`，且 steering / follow-up 都为空 | 否 | 当前边界不可继续 |
| 最后一条是 `assistant`，但 steering 队列非空 | 是 | 会先消费 steering |
| 最后一条是 `assistant`，steering 为空但 follow-up 非空 | 是 | 会消费 follow-up |
| 最后一条不是 `assistant` | 是 | 会基于现有 transcript 继续跑 |

一个典型的合法场景是：transcript 最后一条是 `UserMessage` 或 `ToolResultMessage`，你希望让模型继续接着生成下一条 assistant 消息。

```python
await agent.continue_()
```

另一个重要场景是：上一轮已经停在 assistant，但你在此期间塞入了新的队列消息：

```python
agent.steer(UserMessage(content=[TextContent(text="优先回答最新问题")]))
await agent.continue_()
```

当前语义里，`continue_()` 的优先级处理如下：

1. 如果末尾是 assistant，先尝试取 steering 队列。
2. 只有 steering 为空时，才尝试取 follow-up 队列。
3. 如果两类队列都为空，则报错，说明当前无法继续。

这也意味着在 assistant 边界上，如果 steering 和 follow-up 同时存在，steering 一定先被消费。测试中已经验证了这一点。

使用建议：

- 想表达“新的一轮用户输入”，用 `prompt()`。
- 想表达“基于现有 transcript 的恢复或续跑”，才用 `continue_()`。
- 如果你不确定当前边界是否合法，最好先检查 `agent.state.messages` 的最后一条消息角色。

### 4.3 `steer()`

`steer()` 用于向 steering 队列中插入一条消息。steering 代表“高优先级的运行中转向”，在当前实现里，它的消费优先级高于 follow-up。

方法签名：

```python
agent.steer(message: AgentMessage) -> None
```

特点如下：

- 这是一个同步方法，只负责把消息放入队列，不会直接触发运行。
- 如果当前 run 正在进行，主循环会在合适的轮次边界优先拉取 steering 消息。
- 如果当前没有运行中的任务，插入 steering 后需要通过 `continue_()` 或下一次 `prompt()` 才会进入执行链路。

最常见的使用场景：

- 用户在模型还没完全收束前追加新的高优先级要求
- 系统策略临时要求模型改变回答方向
- 前端希望实现“打断式追问”体验

示例：

```python
from agentsdk import TextContent, UserMessage

agent.steer(
    UserMessage(content=[TextContent(text="先不要展开背景，直接给结论")])
)
```

在运行语义上需要记住一点：steering 是“优先插入”，不是“立刻中断当前 Python 调用栈”。它要等到运行时进入下一个可检查队列的边界时才会被消费。

如果你想要的是“尽快停止当前运行”，那应该使用 `abort()`，而不是 `steer()`。

### 4.4 `follow_up()`

`follow_up()` 用于把消息加入 follow-up 队列。与 steering 相比，它更适合表达“当前链路收束后，再继续处理的补充内容”。

方法签名：

```python
agent.follow_up(message: AgentMessage) -> None
```

行为特点：

- 同样只负责入队，不直接启动 run。
- 只有在当前轮次和 steering 队列都处理完成后，follow-up 才会被拉取。
- 如果多个 follow-up 同时积压，具体一次消费一条还是全部消费，取决于 `follow_up_mode`。

适合的场景：

- 一轮回答完成后自动发起补充追问
- 系统在当前答复后追加澄清问题
- 业务层想把后续动作显式排队，而不是和高优先级转向竞争

示例：

```python
agent.follow_up(
    UserMessage(content=[TextContent(text="再给一个更短的版本")])
)
```

和 `steer()` 的区别可以概括为：

- `steer()` 是“尽快插入”
- `follow_up()` 是“本轮收束后继续”

如果二者同时存在，当前实现保证 steering 先于 follow-up 被消费。

### 4.5 `abort()`

`abort()` 用于请求取消当前运行。它不会在任意语句点直接打断所有逻辑，而是通过取消令牌让模型流式处理和工具执行在合适的检查点感知取消，并尽量收敛成一个明确的结束状态。

方法签名：

```python
agent.abort() -> None
```

当前行为如下：

- 如果没有活跃 run，`abort()` 不会报错，也不会产生额外效果。
- 如果当前有活跃 run，会触发该 run 的 `CancelToken`。
- 运行时随后会尽量收敛为一条 `AssistantMessage`，其 `stop_reason` 通常为 `"aborted"`，`error_message` 中会包含取消原因或适配层返回的错误文本。

一个常见的取消流程如下：

```python
task = asyncio.create_task(agent.prompt("开始一个较长的任务"))
await asyncio.sleep(0.5)
agent.abort()
await agent.wait_for_idle()
await task
```

取消后建议同时检查：

- `agent.state.is_streaming` 是否已经回到 `False`
- `agent.state.error_message` 是否记录了取消信息
- transcript 最后一条 assistant 消息的 `stop_reason`

当前测试已经验证：`wait_for_idle()` 会等待 `agent_end` 监听器也执行完成，因此在复杂接入场景中，`abort()` 和 `wait_for_idle()` 通常需要配套使用。

### 4.6 `wait_for_idle()`

`wait_for_idle()` 用于等待当前后台 run 完整结束。这里的“完整结束”不仅包含模型流和工具执行收尾，也包含事件监听器的执行完成。

方法签名：

```python
await agent.wait_for_idle()
```

它最适合两类场景：

- 你把 `prompt()` / `continue_()` 放进了 `asyncio.create_task(...)`，需要从外部等待统一收尾。
- 你调用了 `abort()`，想等取消后的状态、消息和监听器都稳定下来。

行为特点：

- 如果当前没有活跃 run，会立即返回。
- 如果有活跃 run，会一直等待到内部 `RunHandle` 被标记为 idle。
- 在当前实现里，只有当 run 生命周期走完整，且相关监听器执行完成后，`wait_for_idle()` 才会返回。

这个方法的价值主要体现在：

- 它给了调用方一个适合“后台 run 收尾”的严格边界。
- 你可以在它返回之后放心读取稳定状态、做资源释放、结束 HTTP 请求或更新 UI。
- 如果你依赖 `agent_end` 事件执行一些异步收尾逻辑，这个方法能够保证这些逻辑已经跑完。

常见用法如下：

```python
task = asyncio.create_task(agent.prompt("解释这个函数"))
await agent.wait_for_idle()
final_messages = agent.state.messages
await task
```

如果你在后台运行期间不调用 `wait_for_idle()`，你依然可能在中途读取到：

- `is_streaming=True`
- `streaming_message` 仍有内容
- 监听器里的异步副作用尚未完成

如果你本来就是直接 `await agent.prompt(...)` 或 `await agent.continue_()`，通常不需要再额外补一次 `wait_for_idle()`。

### 4.7 `reset()`

`reset()` 用于把当前 `Agent` 重置到一个新的会话起点。它会清空 transcript，并把当前运行态字段和两类待消费队列一起清空。

方法签名：

```python
agent.reset() -> None
```

当前会被清空的内容包括：

- `state.messages`
- `state.streaming_message`
- `state.pending_tool_calls`
- `state.error_message`
- steering 队列
- follow-up 队列

不会被清空的内容包括：

- `system_prompt`
- `model`
- 已注册 `tools`
- 构造 `Agent` 时提供的静态配置

因此，`reset()` 更接近“重开会话”，而不是“重新初始化对象”。如果只是想清空历史但保留同一套模型和工具配置，这个方法比较合适。

示例：

```python
agent.reset()
await agent.prompt("我们重新开始，请只回答一句话。")
```

注意事项：

- 如果在活跃 run 期间直接 `reset()`，虽然当前实现没有显式阻止，但在实际接入中不建议这样做。
- 更稳妥的做法是先 `abort()`，再 `await wait_for_idle()`，最后 `reset()`。

### 4.8 `state`

`state` 是 `Agent` 对外暴露的运行状态快照入口，返回的是 `AgentStateView`。它适合在任意时刻读取当前运行快照，但不应该被当作内部可变状态直接修改。

最常用的状态字段如下：

| 字段 | 说明 |
| --- | --- |
| `system_prompt` | 当前会话系统提示词 |
| `model` | 当前模型信息副本 |
| `thinking_level` | 当前 thinking 级别 |
| `tools` | 已注册工具的只读元组视图 |
| `messages` | 当前 transcript 的深拷贝快照 |
| `is_streaming` | 当前是否有活跃 run |
| `streaming_message` | 当前消息生命周期中的最新消息快照；在流式生成阶段通常是 partial assistant message |
| `pending_tool_calls` | 正在执行中的工具调用 ID 集合 |
| `error_message` | 最近一次运行记录到的错误信息 |

为什么说它是“快照”而不是“句柄”：

- `messages`、`model`、`streaming_message` 等字段在读取时都会做拷贝保护。
- `tools` 则以只读元组视图暴露，适合做读取，不适合拿来假设可变引用语义。
- 你修改这些返回值，不会反向影响 `Agent` 内部真实状态。

这很适合做以下事情：

- UI 读取当前 transcript
- 业务层判断是否仍在流式中
- 调试最近一次错误信息
- 查询还有哪些工具调用尚未结束

最常见的读取方式：

```python
snapshot = agent.state
print(snapshot.is_streaming)
print(snapshot.error_message)
print(len(snapshot.messages))
```

如果你需要知道“刚刚发生了什么”，优先用事件；如果你需要知道“此刻系统长什么样”，优先用 `state`。

### 4.9 `subscribe()`

`subscribe()` 用于注册一个事件监听器，让调用方可以在运行过程中感知生命周期变化、消息增量和工具执行进度。

方法签名可以概括为：

```python
unsubscribe = agent.subscribe(listener)
```

其中监听器签名为：

```python
async def listener(event, cancel_token): ...
```

也支持同步函数监听器。返回值 `unsubscribe` 是一个无参函数，调用后会把当前监听器移除。

示例：

```python
def on_event(event, cancel_token):
    print(event.type)

unsubscribe = agent.subscribe(on_event)
```

监听器会收到两类信息：

- `event`：具体的事件对象，例如 `MessageUpdateEvent`
- `cancel_token`：当前 run 的取消令牌，可用于在监听逻辑中感知取消状态

当前实现中的几个关键特性：

- 监听器按注册顺序依次执行。
- 监听器既可以是同步函数，也可以是异步函数。
- 监听器发生在活跃 run 内部；如果内部逻辑在没有活跃 run 的情况下尝试分发监听器，会触发 `ListenerOutsideRunError`。
- `wait_for_idle()` 会等待监听器执行完成，因此监听器中的耗时逻辑会影响 run 的最终收尾时间。

使用建议：

- 做终端流式输出时，重点监听 `message_update`。
- 做工具进度展示时，重点监听 `tool_execution_update`。
- 做统一收尾逻辑时，重点监听 `agent_end`。
- 如果监听器里有较重的 I/O，建议控制好耗时，否则会直接拉长整轮 run 的收尾时间。

## 5. AgentOptions 配置参考

本章中的“当前状态”统一按以下口径理解：

- “已生效”：当前默认运行链路已经实际消费该字段，并会影响行为。
- “已保存、已透传”：字段会进入 `Agent` 状态或 `AgentLoopConfig`，但是否真正影响模型请求或运行效果，取决于具体适配器实现。
- “当前预留”：字段已建模，但默认主链路尚未消费。

### 5.1 基础配置

基础配置决定了一个 `Agent` 的初始身份和起始上下文，主要包括：

- `system_prompt`
- `model`
- `messages`
- `tools`

最常见的构造方式如下：

```python
options = AgentOptions(
    system_prompt="你是一个可靠的开发助手。",
    model=ModelInfo(id="gpt-4o-mini", provider="openai", api="chat.completions"),
    messages=[],
    tools=[],
)
```

字段说明：

| 字段 | 默认值 | 当前作用 |
| --- | --- | --- |
| `system_prompt` | `""` | 作为 `AgentContext.system_prompt` 保存在当前会话中；默认 OpenAI 适配器会把它写入请求首条 system message |
| `model` | `ModelInfo()` | 标识当前模型身份与协议类型 |
| `messages` | `[]` | 作为初始 transcript 注入 `Agent` |
| `tools` | `[]` | 作为当前会话可用工具列表 |

推荐理解方式：

- `system_prompt` 是当前会话级系统提示，而不是每轮单独传入的参数；默认 OpenAI 适配器会保留它，并把它写进请求里的首条 system message。
- `messages` 适合在恢复历史会话、做 continuation 测试或初始化带上下文的 Agent 时使用。
- `tools` 注册的是“本 Agent 生命周期内可见的工具集合”。

什么时候会显式配置 `messages`：

- 你要恢复一段已有 transcript
- 你要从非空上下文开始 `continue_()`
- 你在测试或调试 continuation、队列和工具语义

什么时候会显式配置 `tools`：

- 你希望 assistant 可以发起工具调用
- 你需要统一管理一组业务工具，而不是每次运行临时拼装

如果只看“基础配置”这一组字段，最简单的聊天接入通常只需要重点关心 `system_prompt` 和 `model`；`messages`、`tools` 可以保持默认空值。真正可运行的最小闭环仍然需要同时配置可用的 `stream_fn`。

### 5.2 生成参数

生成参数用于控制模型调用时的采样行为。当前最直接生效的字段包括：

- `temperature`
- `top_p`
- `max_tokens`

这些字段在当前实现中会被透传给 `OpenAIChatCompletionsAdapter`，由适配器写入最终请求参数。

```python
options = AgentOptions(
    model=model,
    stream_fn=adapter.stream_message,
    temperature=0.2,
    top_p=0.95,
    max_tokens=1024,
)
```

字段说明：

| 字段 | 默认值 | 当前行为 |
| --- | --- | --- |
| `temperature` | `None` | 不为 `None` 时传给模型请求 |
| `top_p` | `None` | 不为 `None` 时传给模型请求 |
| `max_tokens` | `None` | 不为 `None` 时传给模型请求 |

使用建议：

- 想要更稳定、更收敛的输出时，把 `temperature` 调低。
- 如果你已经使用 `temperature` 调参，通常不需要同时频繁改 `top_p`。
- `max_tokens` 适合在输出长度可控、成本需要限制的场景中显式设置。

如果你使用的不是当前仓库里的 OpenAI Chat Completions 适配器，而是自定义 `stream_fn`，这些参数是否生效取决于你的 `stream_fn` 是否消费它们。

### 5.3 模型接入配置

模型接入配置决定了运行时如何真正与底层模型通信。当前最关键的字段有：

- `stream_fn`
- `api_key`
- `get_api_key`
- `base_url`

其中 `stream_fn` 是必需理解的核心字段。`Agent` 自身不内置模型通信实现，它只会在运行时调用 `stream_fn(model, context, config, cancel_token)`。

最常见的配置方式如下：

```python
client = AsyncOpenAI(api_key=api_key, base_url=base_url)
adapter = OpenAIChatCompletionsAdapter(client)

options = AgentOptions(
    model=model,
    stream_fn=adapter.stream_message,
    api_key=api_key,
)
```

字段说明：

| 字段 | 当前状态 | 说明 |
| --- | --- | --- |
| `stream_fn` | 已实际生效 | 运行时真正调用模型的入口 |
| `api_key` | 已实际生效 | 传给当前 OpenAI 适配器构造请求参数 |
| `get_api_key` | 已实际生效 | 在 `api_key` 为空时按 `provider` 动态解析 key |
| `base_url` | 当前 `AgentOptions` 中保存，但默认 OpenAI 适配器不直接读取它来改客户端地址 | 更适合作为元数据或由外部客户端初始化时使用 |

关于 `api_key` 和 `get_api_key` 的优先级：

- 如果显式设置了 `api_key`，当前运行时会直接使用它。
- 如果 `api_key` 为空且提供了 `get_api_key`，运行时会先调用 `get_api_key(model.provider)`。
- 解析出的 key 会进入本轮 `effective_config`，再传给 `stream_fn`。

关于 `base_url` 需要特别说明：

- `ModelInfo.base_url` 是模型信息的一部分。
- `AgentOptions.base_url` 会被带进 `AgentLoopConfig`。
- 但当前仓库中的 `OpenAIChatCompletionsAdapter` 并不会在请求构造时直接读取 `options.base_url`。
- 如果你要接兼容 OpenAI 的代理地址，推荐做法仍然是在创建 `AsyncOpenAI` 客户端时传入 `base_url`。

因此，第 5 章这里的建议是：把 `base_url` 当作会话配置元信息保留，但不要假设仅设置 `AgentOptions.base_url` 就能自动改写底层客户端地址。

### 5.4 上下文扩展配置

上下文扩展配置用于在请求发给模型之前，重写或裁剪内部消息上下文。当前两个核心扩展点是：

- `transform_context`
- `convert_to_llm`

二者的职责不同：

| 字段 | 作用阶段 | 典型用途 |
| --- | --- | --- |
| `transform_context` | 在内部 transcript 进入模型前 | 压缩历史、裁剪消息、补充上下文 |
| `convert_to_llm` | 在最终发给模型前 | 把内部消息转换成适配器期望的格式 |

当前默认行为中，`convert_to_llm` 如果没有显式提供，会退回到内置默认实现：只保留 `role` 为 `user`、`assistant` 和 `toolResult` 的消息。

示例：

```python
def trim_context(messages, cancel_token):
    return messages[-10:]

options = AgentOptions(
    model=model,
    stream_fn=adapter.stream_message,
    transform_context=trim_context,
)
```

你应该在这些场景下考虑使用它们：

- transcript 很长，需要裁剪上下文窗口
- 你要在发送前插入额外提示或过滤掉无关消息
- 你的自定义适配器需要接收与内部消息结构不同的输入格式

需要注意：

- `transform_context` 处理的是内部 `AgentMessage` 序列。
- `convert_to_llm` 的输出会直接进入 `stream_fn`。
- 如果你在这两个钩子里做了不兼容的转换，错误通常会在适配器层或模型调用阶段暴露出来。

这两个字段都已经在当前运行时实际生效，并由 `stream_assistant_response()` 在模型调用前依次执行。

### 5.5 工具扩展配置

工具扩展配置决定了当前 `Agent` 如何注册工具，以及工具执行时要采用什么策略。主要字段包括：

- `tools`
- `tool_execution`
- `before_tool_call`
- `after_tool_call`

示例：

```python
options = AgentOptions(
    model=model,
    stream_fn=adapter.stream_message,
    tools=[search_tool, fetch_tool],
    tool_execution=ToolExecutionMode.PARALLEL,
    before_tool_call=before_hook,
    after_tool_call=after_hook,
)
```

字段说明：

| 字段 | 当前作用 |
| --- | --- |
| `tools` | 注册本会话可用工具 |
| `tool_execution` | 控制工具串行还是并行执行 |
| `before_tool_call` | 在执行前检查、拦截或改写行为 |
| `after_tool_call` | 在执行后改写结果或错误标记 |

关于 `tool_execution`：

- 默认值是 `ToolExecutionMode.PARALLEL`
- `SEQUENTIAL` 会按 assistant 中 tool call 的顺序逐个执行
- `PARALLEL` 会并发执行可执行工具，但最终结果仍按原始 tool call 顺序回写

当前测试已经验证：

- 并行模式下多个工具会并发运行
- 即使完成顺序不同，最终 `ToolResultMessage` 仍按 assistant 源顺序落地

关于 `before_tool_call`：

- 执行时机：参数准备和参数校验之后、工具真正执行之前
- 可以返回 `BeforeToolCallResult(block=True, reason="...")` 来阻断调用
- 适合做权限控制、白名单校验、参数治理

关于 `after_tool_call`：

- 执行时机：工具执行完成之后、结果写回 transcript 之前
- 可以通过 `AfterToolCallResult` 改写 `content`、`details` 或 `is_error`
- 适合做统一结果包装、错误文案重写、结果清洗

这几个字段都已经在当前工具执行链路中实际生效。

### 5.6 队列与运行控制配置

这一组配置主要控制 steering / follow-up 的消费方式，以及部分运行时附加控制信息。核心字段包括：

- `steering_mode`
- `follow_up_mode`
- `thinking_level`
- `thinking_budgets`

其中前两个字段已经在当前队列实现中实际生效，后两个字段目前更多是状态和配置透传的一部分。

#### `steering_mode` 与 `follow_up_mode`

当前支持两个取值：

- `"one-at-a-time"`
- `"all"`

它们的含义是：

| 模式 | 行为 |
| --- | --- |
| `one-at-a-time` | 每次只从队列取出一条消息 |
| `all` | 每次把当前积压消息全部取出 |

默认值都是 `"one-at-a-time"`。

示例：

```python
options = AgentOptions(
    model=model,
    stream_fn=adapter.stream_message,
    steering_mode="all",
    follow_up_mode="one-at-a-time",
)
```

推荐理解：

- 如果你希望队列消息严格逐条推进，保留默认值即可。
- 如果你希望一口气消费当前积压的 steering 或 follow-up，使用 `"all"`。

#### `thinking_level` 与 `thinking_budgets`

当前实现中：

- `thinking_level` 会进入 `Agent` 状态，并被带进 `AgentLoopConfig`
- `thinking_budgets` 也会被带进 `AgentLoopConfig`

但在当前仓库默认的 OpenAI Chat Completions 适配器里，这两个字段没有被进一步用于请求构造。因此更准确的说法是：

- 它们目前是“已保存、已透传”的字段
- 是否真正影响模型行为，取决于你的自定义适配器或未来扩展实现

如果你现在只使用仓库中的默认 OpenAI 适配器，不应假设调整 `thinking_level` 就一定会改变模型输出。

### 5.7 其他配置

剩余字段主要用于补充运行信息或为自定义适配层留扩展位，包括：

- `session_id`
- `metadata`
- `on_payload`
- `max_retry_delay_ms`

这几个字段需要分开看待。

#### `metadata`

`metadata` 当前已经在 OpenAI 适配器中实际生效：如果字典非空，会被透传到请求参数中。

```python
options = AgentOptions(
    model=model,
    stream_fn=adapter.stream_message,
    metadata={"trace_id": "req-123", "scene": "cli-demo"},
)
```

适合用来放：

- 追踪 ID
- 场景标识
- 业务标签

#### `session_id`

`session_id` 当前会被保存到 `Agent` 和 `AgentLoopConfig` 中，但仓库内默认适配器没有继续消费它。因此它目前更适合作为上层应用自行读取或为未来扩展预留的会话标识字段。

#### `on_payload`

`on_payload` 已经出现在 `AgentOptions` 和 `AgentLoopConfig` 中，但当前仓库里的主运行链路和默认适配器没有实际调用它。换句话说，它目前是一个保留扩展点，而不是默认行为的一部分。

#### `max_retry_delay_ms`

`max_retry_delay_ms` 当前会保存在 `Agent` 对象上，但在现有运行时中没有看到实际重试逻辑消费它。因此目前不建议把它理解为“已经生效的重试配置”。如果后续引入带退避重试的适配层或外部封装，这个字段才可能真正发挥作用。

本节的使用建议是：

- 对于 `metadata`，可以放心作为请求附加元数据使用。
- 对于 `session_id`、`on_payload`、`max_retry_delay_ms`，应视作“当前预留字段或透传字段”，除非你的自定义实现明确消费了它们。

## 6. 消息模型参考

### 6.1 `UserMessage`

`UserMessage` 表示调用侧输入到当前会话中的一条用户消息。它既可以来自最常见的 `prompt("...")` 字符串输入，也可以由调用者显式构造。

当前结构如下：

```python
UserMessage(
    role="user",
    content=[...],
    timestamp=...,
)
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `role` | 固定为 `"user"` | 消息角色标识 |
| `content` | `list[UserContent]` | 用户消息内容块列表 |
| `timestamp` | `int` | 毫秒时间戳，默认自动生成 |

其中 `UserContent` 当前由两类内容块组成：

- `TextContent`
- `ImageContent`

最简单的构造方式：

```python
from agentsdk import TextContent, UserMessage

message = UserMessage(
    content=[TextContent(text="请解释这个错误日志")]
)
```

如果你直接调用：

```python
await agent.prompt("请解释这个错误日志")
```

SDK 会自动把它包装成等价的 `UserMessage`。如果还传入 `images` 参数，这些图片会和文本一起出现在同一条 `UserMessage.content` 中。

什么时候显式构造 `UserMessage` 更合适：

- 你要发送多段内容，而不是一段纯文本
- 你要显式混合文本和图片
- 你在操作 steering / follow-up 队列，需要手动入队一条结构化消息

### 6.2 `AssistantMessage`

`AssistantMessage` 表示模型输出的最终消息，也是 transcript 中最核心的一类消息。它不仅承载文本回答，还能表达工具调用、thinking 内容、结束原因和错误信息。

当前结构如下：

```python
AssistantMessage(
    role="assistant",
    content=[...],
    stop_reason="stop",
    error_message=None,
    usage=TokenUsage(),
    provider="unknown",
    model="unknown",
    api="unknown",
    timestamp=...,
)
```

关键字段说明：

| 字段 | 说明 |
| --- | --- |
| `content` | assistant 消息内容块列表 |
| `stop_reason` | 本条 assistant 消息如何结束 |
| `error_message` | 运行失败或中止时的错误信息 |
| `usage` | token 使用量 |
| `provider` / `model` / `api` | 生成该消息的模型元信息 |

`content` 中可能出现的块类型包括：

- `TextContent`
- `ImageContent`
- `ThinkingContent`
- `ToolCallContent`

当前 `stop_reason` 可能出现的值包括：

- `"stop"`：正常完成
- `"tool_calls"`：assistant 生成了工具调用请求
- `"length"`：输出长度受限
- `"content_filter"`：内容过滤中止
- `"error"`：运行或适配层错误
- `"aborted"`：取消收敛结束
- `"unknown"`：无法识别的结束原因

需要特别注意：

- 一条 `AssistantMessage` 不一定只有文本，也可能只包含工具调用。
- 当运行失败时，SDK 仍会尽量收敛出一条 `AssistantMessage`，此时 `error_message` 会携带错误文本。
- 在流式阶段，监听器收到的 `message_update` 事件里的 `event.message` 也是 `AssistantMessage`，但它是中间态，不一定等于最终落地消息。

最常见的文本抽取方式：

```python
text = "".join(
    block.text
    for block in assistant_message.content
    if isinstance(block, TextContent)
)
```

如果你要识别 assistant 是否发起了工具调用，不要只看文本，而应该扫描 `content` 中是否有 `ToolCallContent`。

### 6.3 `ToolResultMessage`

`ToolResultMessage` 表示一次工具执行结果回写到 transcript 后形成的消息。它不是 assistant 文本的一部分，而是一条独立消息，用来把工具结果重新放回对话上下文中，供后续轮次继续使用。

当前结构如下：

```python
ToolResultMessage(
    role="toolResult",
    tool_call_id="",
    tool_name="",
    content=[...],
    details=None,
    is_error=False,
    timestamp=...,
)
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `tool_call_id` | 对应 assistant 发起的 tool call ID |
| `tool_name` | 对应工具名称 |
| `content` | 要回写到上下文中的可见内容 |
| `details` | 附带结构化结果，可供业务层使用 |
| `is_error` | 本次工具结果是否为错误 |

`ToolResultMessage` 的来源只有一种：SDK 执行工具后自动创建并写回 transcript。调用者通常不需要手动构造它，除非你在做更底层的运行时模拟或测试。

需要注意两点：

- 即使工具执行失败，也仍然会生成一条 `ToolResultMessage`，并把 `is_error` 标为 `True`。
- 后续模型调用会看到这条消息，因此工具错误也会成为上下文的一部分。

在 UI 或日志中，`ToolResultMessage` 往往适合作为“工具执行记录”单独展示，而不是混进 assistant 的普通文本输出。

### 6.4 `TextContent`

`TextContent` 是最基础也最常见的内容块类型，用于表达纯文本。

结构非常简单：

```python
TextContent(
    type="text",
    text="...",
)
```

它可能出现在三类地方：

- `UserMessage.content`
- `AssistantMessage.content`
- `ToolResultMessage.content`

最常见的使用场景：

- 用户输入文本
- assistant 正常回答
- 工具返回可展示的文本结果

流式阶段最需要注意的一点是：`message_update` 事件里的文本通常是“累计后的当前完整文本”，而不是只包含刚新增的一小段。因此做打字机输出时，通常要自己计算增量差值，而不是直接 `print(block.text)`。

仓库里的 [agent_sdk_demo.py](/Users/bg/project/uu-work/agent_sdk_demo.py) 就采用了这个模式：用一个计数器记住已输出长度，只打印新增文本。

### 6.5 `ImageContent`

`ImageContent` 用于表达图片内容，既可以用于用户输入，也可以作为工具结果的一部分。

当前结构如下：

```python
ImageContent(
    type="image",
    image_url=None,
    mime_type=None,
    detail=None,
    alt_text=None,
)
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `image_url` | 图片地址 |
| `mime_type` | 可选的 MIME 类型 |
| `detail` | 图片细节级别提示 |
| `alt_text` | 可选的替代文本 |

在当前仓库的 OpenAI Chat Completions 适配器里：

- `UserMessage` 中的 `ImageContent` 会被映射为 `image_url` 类型输入
- 如果提供了 `detail`，会一并带入请求

示例：

```python
from agentsdk import ImageContent

image = ImageContent(
    image_url="https://example.com/diagram.png",
    detail="high",
    alt_text="系统架构图",
)
```

如果你需要发送“文本 + 图片”的复合输入，推荐做法是构造一条 `UserMessage`，把 `TextContent` 和 `ImageContent` 放进同一个 `content` 列表里。

需要注意的是：当前默认适配器对 assistant 输出图片和工具结果图片没有做复杂专用协议处理，更多仍然依赖上层应用如何解释这些内容块。

### 6.6 `ThinkingContent`

`ThinkingContent` 用于表示模型的 thinking / reasoning 内容块。它是消息模型中为推理过程预留的统一结构。

当前结构如下：

```python
ThinkingContent(
    type="thinking",
    thinking="...",
    signature=None,
)
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `thinking` | 当前累计的推理文本 |
| `signature` | 可选签名字段 |

它通常只会出现在 `AssistantMessage.content` 中。当前默认流式收敛逻辑支持 `thinking_delta` 事件：当适配器产生 reasoning 增量时，运行时会把这些增量合并到同一个 `ThinkingContent` 里，并通过 `message_update` 事件持续暴露出来。

但需要明确两点：

- 当前仓库的默认 OpenAI Chat Completions 适配器只有在底层响应里出现 `reasoning` 或 `reasoning_content` 字段时，才会生成这类内容。
- 如果你当前接入的模型或网关并不返回 reasoning 增量，那么 transcript 中自然也不会出现 `ThinkingContent`。

因此，`ThinkingContent` 是一个已经有统一结构、也有流式收敛支持的类型，但是否真的出现，取决于具体模型和适配器能力。

### 6.7 `ToolCallContent`

`ToolCallContent` 用于表示 assistant 发起的一次工具调用请求。它是 assistant 消息中的特殊内容块，也是工具执行链路的起点。

当前结构如下：

```python
ToolCallContent(
    type="toolCall",
    id="",
    name="",
    arguments={},
)
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `id` | 工具调用 ID |
| `name` | 要调用的工具名 |
| `arguments` | 工具参数，通常为字典 |

它通常只会出现在 `AssistantMessage.content` 中，典型含义是：“模型这轮不是单纯要回复文本，而是要调用一个名为 `name` 的工具，并传入 `arguments` 参数”。

在流式场景下，`ToolCallContent` 还有一个重要特征：参数可能是增量到达的。当前默认 OpenAI 适配器会在工具调用参数流式拼装过程中，持续产出 `tool_call_delta` 事件，并尽可能把部分 JSON 字符串修复解析为当前可用的 `arguments`。

因此你在 `message_update` 里看到的 `ToolCallContent.arguments` 可能是：

- 一个部分但已可解析的参数对象
- 一个最终完整的参数对象
- 在最开始阶段是空字典

一旦最终 assistant 消息收敛完成，运行时就会基于其中的 `ToolCallContent` 去执行工具，并在 transcript 中追加相应的 `ToolResultMessage`。

如果你的上层应用需要在 UI 中显示“模型正在准备调用哪个工具”，`ToolCallContent` 和 `tool_call_delta` 就是最直接的数据来源。

## 7. 事件与流式输出

### 7.1 事件订阅方式

事件订阅的入口是 `agent.subscribe()`。你向 `Agent` 注册一个监听器之后，运行时在每次 run 中都会把结构化事件依次分发给它。

最简单的用法如下：

```python
def on_event(event, cancel_token):
    print(event.type)

unsubscribe = agent.subscribe(on_event)
```

监听器既可以是同步函数，也可以是异步函数：

```python
async def on_event(event, cancel_token):
    ...
```

参数说明：

| 参数 | 说明 |
| --- | --- |
| `event` | 当前事件对象，类型属于 `AgentEvent` 联合 |
| `cancel_token` | 当前 run 的取消令牌，可选用于感知取消状态 |

还有两个行为细节很重要：

- `subscribe()` 会返回一个取消订阅函数，调用后可把当前监听器移除。
- 监听器按注册顺序执行，且 `wait_for_idle()` 会等待监听器也执行完毕。

因此，如果你在监听器中做较重的 I/O 或长耗时逻辑，会直接拉长整轮 run 的收尾时间。

如果你只是想打印事件类型，监听器可以很轻；如果你要把事件同步到外部系统，建议谨慎控制耗时或把重任务再异步转发出去。

### 7.2 Agent 级事件

Agent 级事件描述的是“一次 run 的开始和结束”。当前包括两种：

- `AgentStartEvent`
- `AgentEndEvent`

#### `AgentStartEvent`

在一次 run 启动时发出，没有额外负载字段，主要用于标记生命周期开始。

典型用途：

- UI 进入“运行中”状态
- 开始记录本轮 trace
- 初始化本轮临时统计信息

#### `AgentEndEvent`

在一次 run 完整结束时发出，包含：

| 字段 | 说明 |
| --- | --- |
| `messages` | 本次 run 期间新增的消息序列 |

这里的 `messages` 不是全量 transcript，而是“本次 run 新增的消息”。这很适合做：

- 本轮增量消息落盘
- 一次 run 的摘要统计
- 在 UI 中把本轮新增内容整体提交

如果你想拿到最终全量状态，仍然建议结合 `agent.state.messages` 一起看。

### 7.3 Turn 级事件

Turn 级事件描述的是“一轮 assistant 生成及其相关工具回写”的边界。当前包括：

- `TurnStartEvent`
- `TurnEndEvent`

#### `TurnStartEvent`

表示新一轮 turn 即将开始。当前事件本身没有额外字段。

它通常出现在：

- 初始 prompt 刚进入运行时
- 上一轮 tool call 处理完成后准备进入下一轮
- steering 或 follow-up 被拉取后准备进入新一轮

#### `TurnEndEvent`

表示当前一轮 turn 已结束，包含：

| 字段 | 说明 |
| --- | --- |
| `message` | 本轮产生的 assistant 消息 |
| `tool_results` | 本轮产生的工具结果消息列表 |

这个事件很适合做“轮级别”的处理，例如：

- 统计一轮中是否发生了工具调用
- 判断 assistant 这一轮是正常结束、错误结束还是被中止
- 在回合粒度上刷新 UI 或状态

常见判断示例：

```python
if event.type == "turn_end":
    msg = event.message
    if isinstance(msg, AssistantMessage) and msg.stop_reason == "tool_calls":
        ...
```

如果你做的是按回合组织的对话界面，`turn_end` 往往比单纯的 `message_end` 更适合作为“这一轮完成”的判断点。

### 7.4 Message 级事件

Message 级事件描述的是单条消息从开始、增量更新到结束的过程。当前包括：

- `MessageStartEvent`
- `MessageUpdateEvent`
- `MessageEndEvent`

#### `MessageStartEvent`

表示一条消息开始进入当前 run，字段如下：

| 字段 | 说明 |
| --- | --- |
| `message` | 当前消息对象 |

它既可能对应用户消息，也可能对应 assistant 消息，还可能对应 `ToolResultMessage`。在流式 assistant 场景下，`message_start` 往往对应的是一个“刚开始的 partial assistant message”。

#### `MessageUpdateEvent`

这是流式输出里最重要的事件，表示 assistant 消息发生了增量更新。字段如下：

| 字段 | 说明 |
| --- | --- |
| `message` | 当前最新的 partial assistant message |
| `assistant_message_event` | 底层 assistant 流式事件，如 `text_delta`、`thinking_delta`、`tool_call_delta` |

当前实现里，只有 assistant 流式消息才会触发 `message_update`。如果底层是非流式结果，通常只会收到 `message_start` 和 `message_end`。

#### `MessageEndEvent`

表示一条消息已经稳定结束，字段如下：

| 字段 | 说明 |
| --- | --- |
| `message` | 最终消息对象 |

对于 assistant 消息来说，这通常意味着：

- 流式阶段已完成
- transcript 中已经落入最终消息
- 之后可以安全读取最终文本、工具调用或错误信息

在当前 `stream_handler` 实现中：

- 流式 assistant 会按 `message_start -> 多次 message_update -> message_end` 收敛
- 非流式 assistant 会按 `message_start -> message_end` 收敛

这套契约已经有测试覆盖，可以把它当作对外稳定行为来理解。

### 7.5 Tool Execution 级事件

Tool Execution 级事件描述的是一次工具调用从开始、进度更新到完成的过程。当前包括：

- `ToolExecutionStartEvent`
- `ToolExecutionUpdateEvent`
- `ToolExecutionEndEvent`

#### `ToolExecutionStartEvent`

字段如下：

| 字段 | 说明 |
| --- | --- |
| `tool_call_id` | 当前工具调用 ID |
| `tool_name` | 工具名 |
| `args` | 原始参数 |

它适合用于：

- UI 展示“开始调用某个工具”
- 记录工具调用审计日志
- 更新当前 pending tool calls 视图

#### `ToolExecutionUpdateEvent`

字段如下：

| 字段 | 说明 |
| --- | --- |
| `tool_call_id` | 当前工具调用 ID |
| `tool_name` | 工具名 |
| `args` | 调用参数 |
| `partial_result` | 当前中间结果，类型为 `AgentToolResult` |

只有当工具在 `execute()` 中主动通过 `on_update` 上报进度时，才会出现这种事件。

适合用于：

- 长任务进度条
- 实时日志输出
- 展示工具执行中的中间态内容

#### `ToolExecutionEndEvent`

字段如下：

| 字段 | 说明 |
| --- | --- |
| `tool_call_id` | 当前工具调用 ID |
| `tool_name` | 工具名 |
| `result` | 最终 `AgentToolResult` |
| `is_error` | 是否错误结束 |

需要注意：

- 即使工具执行失败，也仍然会发出 `tool_execution_end`，只是 `is_error=True`
- 在该事件之后，运行时还会继续发出与对应 `ToolResultMessage` 相关的 `message_start` / `message_end`

因此，如果你既关心“工具是否结束”，又关心“工具结果是否已落入 transcript”，就需要同时关注 tool 级事件和 message 级事件。

### 7.6 流式输出最小示例

下面这个例子展示了如何通过事件监听实现一个最小的终端流式输出。它和仓库中的 [agent_sdk_demo.py](/Users/bg/project/uu-work/agent_sdk_demo.py) 是同一种思路，但更短一些。

```python
import asyncio

from agentsdk import Agent, AgentOptions, AssistantMessage, TextContent


async def run(agent: Agent) -> None:
    printed = 0

    def on_event(event, cancel_token):
        nonlocal printed

        if event.type == "message_start":
            printed = 0
            return

        if event.type == "message_update":
            for block in event.message.content:
                if isinstance(block, TextContent):
                    full_text = block.text
                    if len(full_text) > printed:
                        print(full_text[printed:], end="", flush=True)
                        printed = len(full_text)
            return

        if event.type == "message_end":
            msg = event.message
            if isinstance(msg, AssistantMessage) and msg.error_message:
                print(f"\n[error] {msg.error_message}")

    unsubscribe = agent.subscribe(on_event)
    try:
        await agent.prompt("请用三句话解释什么是流式事件。")
        print()
    finally:
        unsubscribe()
```

这个示例的关键点有三个：

1. `message_update` 里拿到的是当前完整 partial message，而不是仅新增文本，所以要自己做差值。
2. `message_start` 时把输出计数器清零，避免多条消息串在一起。
3. `message_end` 时检查 `AssistantMessage.error_message`，以便在流式场景下也能感知错误或中止。

如果你需要的不只是文本流式，而是更丰富的事件消费，可以按下面的思路扩展：

- 监听 `tool_execution_start` / `tool_execution_update` 展示工具进度
- 监听 `turn_end` 在回合结束时做统一收尾
- 监听 `agent_end` 把本轮新增消息整体提交到日志或数据库

从运行时实现看，assistant 流式消息的收敛顺序通常是：

1. `message_start`
2. 零次或多次 `message_update`
3. `message_end`

如果底层适配器返回的是非流式最终结果，也仍然会遵守统一的结束契约，只是中间没有 `message_update`。

## 8. 工具调用参考

### 8.1 工具对象需要实现什么

在当前 SDK 中，工具通过 `AgentTool` 协议定义。它不是必须继承某个基类，而是只要满足约定的属性和方法签名，就可以被注册到 `AgentOptions.tools` 中。

一个最小可用工具通常需要具备这些成员：

| 成员 | 是否必需 | 作用 |
| --- | --- | --- |
| `name` | 是 | 模型调用工具时使用的标识名 |
| `label` | 是 | 展示或日志用的人类可读名称 |
| `description` | 否，但强烈建议提供 | 帮助模型理解工具用途 |
| `input_schema` | 否，但强烈建议提供 | 声明参数结构，供适配层和运行时校验 |
| `prepare_arguments` | 否 | 在执行前预处理模型生成的参数 |
| `execute()` | 是 | 真正执行工具逻辑 |

最小骨架如下：

```python
from dataclasses import dataclass

from agentsdk import AgentToolResult, TextContent


@dataclass
class EchoTool:
    name: str = "echo"
    label: str = "Echo"
    description: str | None = "原样返回输入文本"
    input_schema: dict | None = None
    prepare_arguments = None

    async def execute(self, tool_call_id, params, cancel_token=None, on_update=None):
        return AgentToolResult(
            content=[TextContent(text=str(params))],
            details={"tool_call_id": tool_call_id},
        )
```

当 assistant 消息中出现 `ToolCallContent` 时，运行时会按如下顺序处理：

1. 根据 `name` 查找已注册工具
2. 如有 `prepare_arguments`，先做参数预处理
3. 根据 `input_schema` 做参数校验
4. 如配置了 `before_tool_call`，先执行前置 Hook
5. 调用 `execute()`
6. 如配置了 `after_tool_call`，执行后置 Hook
7. 把结果收敛为 `ToolResultMessage` 并写回 transcript

所以从调用侧角度看，一个工具对象至少要回答两个问题：

- 这个工具如何被模型识别和正确调用
- 这个工具执行后要把什么内容回写进对话

### 8.2 `name`、`description` 与 `input_schema`

这三个字段决定了“模型是否会调用工具”和“运行时是否能正确理解参数”。

#### `name`

`name` 是工具的唯一调用标识。assistant 生成的 `ToolCallContent.name` 会直接用它来匹配工具对象，因此它必须稳定、明确，并且在同一组工具中唯一。

建议：

- 使用简短、动作明确的英文名，如 `search_docs`、`read_file`
- 避免同义重复或容易混淆的名称

#### `description`

`description` 会被当前 OpenAI Chat Completions 适配器映射进工具定义，作为给模型看的用途描述。它越清晰，模型越容易在合适场景选择正确工具。

建议写法：

- 先写“做什么”
- 再写“输入是什么”
- 如有必要，再写“不做什么”

示例：

```python
description = "根据城市名称查询当前天气，返回简短天气摘要。不要用于历史天气。"
```

#### `input_schema`

`input_schema` 用于声明工具参数结构。当前运行时支持的是轻量 JSON Schema 风格校验，已经覆盖最常见的对象、数组和基础标量类型。

当前已支持的能力包括：

- `type`
- `properties`
- `required`
- `items`
- `additionalProperties`
- 基础类型：`string`、`number`、`integer`、`boolean`、`null`

示例：

```python
input_schema = {
    "type": "object",
    "properties": {
        "city": {"type": "string"},
        "unit": {"type": "string"},
    },
    "required": ["city"],
    "additionalProperties": False,
}
```

需要注意：

- 如果 `input_schema` 为 `None`，当前运行时不会做参数校验。
- 如果 `input_schema` 不是映射类型，会触发错误。
- 当参数不符合 schema 时，运行时不会让整个 run 崩掉，而是会把错误收敛为一个 `ToolResultMessage`，并标记 `is_error=True`。

因此，对大多数业务工具来说，建议始终提供 `description` 和 `input_schema`，这样模型更容易正确调用，运行时也更容易在错误输入时给出稳定反馈。

### 8.3 `prepare_arguments`

`prepare_arguments` 是工具可选的参数预处理入口。它的职责是把模型生成的原始参数转换成工具真正希望接收的结构。

当前执行顺序里，`prepare_arguments` 发生在参数校验之前：

1. assistant 生成 `ToolCallContent.arguments`
2. 如果工具定义了 `prepare_arguments`，先执行预处理
3. 对预处理后的结果做 `input_schema` 校验
4. 把校验后的参数传入 Hook 和 `execute()`

这意味着它非常适合做这些事：

- 补默认值
- 字段重命名
- 结构重组
- 基础类型转换

示例：

```python
def prepare_arguments(raw):
    city = raw.get("city", "").strip()
    unit = raw.get("unit") or "celsius"
    return {"city": city, "unit": unit}
```

一个很重要的细节是：在当前实现中，`before_tool_call` 拿到的 `context.args` 已经是“预处理并校验后的参数”，但 `context.tool_call` 仍然是原始的 `ToolCallContent`。因此如果你在 Hook 中既想看模型原始输入，又想看规范化后的参数，二者都能拿到。

使用建议：

- 如果工具参数本来就已经很简单，完全可以不实现 `prepare_arguments`
- 如果模型经常产出“接近正确但还差一步”的参数，优先在这里做轻量修正
- 不要把复杂业务逻辑塞进 `prepare_arguments`，它更适合做纯参数整形，而不是执行业务判断

### 8.4 `execute()`

`execute()` 是工具真正执行逻辑的入口，也是工具对象唯一必须实现的行为方法。

当前签名约定如下：

```python
async def execute(
    self,
    tool_call_id: str,
    params,
    cancel_token=None,
    on_update=None,
) -> AgentToolResult:
    ...
```

参数说明：

| 参数 | 说明 |
| --- | --- |
| `tool_call_id` | 当前工具调用 ID |
| `params` | 经过 `prepare_arguments` 和 `input_schema` 校验后的参数 |
| `cancel_token` | 当前 run 的取消令牌 |
| `on_update` | 可选的中间结果上报函数 |

你应该在 `execute()` 中完成的事：

- 执行业务逻辑
- 返回最终 `AgentToolResult`
- 如有需要，周期性调用 `on_update(...)` 上报进度

一个带中间进度的示例：

```python
async def execute(self, tool_call_id, params, cancel_token=None, on_update=None):
    if on_update is not None:
        on_update(AgentToolResult(
            content=[TextContent(text="正在查询...")],
            details={"step": "start"},
        ))

    result_text = f"{params['city']} 当前晴朗，22 度"
    return AgentToolResult(
        content=[TextContent(text=result_text)],
        details={"raw": {"city": params["city"], "temp": 22}},
    )
```

当前运行时中的错误处理方式：

- 如果 `execute()` 抛出异常，运行时会把异常文本收敛成一个错误型 `AgentToolResult`
- 该结果随后会被写成 `ToolResultMessage(is_error=True)`
- 整个 Agent run 不会因为单个工具异常而直接崩掉

因此，从工具作者角度看，可以按正常 Python 方式抛异常；运行时会负责把错误收口成统一对话语义。

### 8.5 `AgentToolResult`

`AgentToolResult` 是工具执行阶段使用的统一结果结构。它不是最终落地到 transcript 的消息，而是工具与运行时之间的中间结果对象。

结构如下：

```python
AgentToolResult(
    content=[...],
    details=None,
)
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `content` | 要暴露给对话层的内容块列表 |
| `details` | 附带结构化细节，供业务层或 Hook 使用 |

`content` 最终会被深拷贝后写入 `ToolResultMessage.content`，而 `details` 会被深拷贝后写入 `ToolResultMessage.details`。

推荐理解方式：

- `content` 是“给模型和用户看的”
- `details` 是“给程序看的”

示例：

```python
AgentToolResult(
    content=[TextContent(text="北京当前晴朗，22 度")],
    details={
        "city": "北京",
        "temperature": 22,
        "unit": "celsius",
    },
)
```

如果你的工具只需要把一段简单文本写回对话，`details` 可以为空；如果你还希望上层应用获得结构化原始数据，建议把这些信息放进 `details`，而不是塞进一大段字符串里。

### 8.6 串行与并行执行

当前 SDK 支持两种工具执行模式：

- `ToolExecutionMode.SEQUENTIAL`
- `ToolExecutionMode.PARALLEL`

通过 `AgentOptions.tool_execution` 配置：

```python
options = AgentOptions(
    ...,
    tool_execution=ToolExecutionMode.PARALLEL,
)
```

#### 串行模式

串行模式下，assistant 消息里的多个工具调用会按出现顺序逐个执行。每个工具完整结束并写回结果后，才会继续下一个。

适合场景：

- 工具之间存在顺序依赖
- 你更关心稳定性和可观察性
- 工具副作用较强，不适合并发执行

#### 并行模式

并行模式下，运行时会先准备各个工具调用，并发执行可执行工具，再按原始 tool call 顺序收集结果并回写。

当前实现的关键语义：

- 工具可以并发运行
- 完成顺序可以不同
- 最终 `ToolResultMessage` 的顺序仍按 assistant 源顺序保持稳定

这点已经由测试覆盖验证。

适合场景：

- 多个工具调用彼此独立
- 你希望缩短总等待时间
- 工具以查询型工作为主，副作用较少

选择建议：

- 默认并行模式适合多数“查询型”工具
- 只要存在共享资源、顺序依赖或副作用竞争，优先切回串行模式

### 8.7 工具结果如何进入 transcript

从 assistant 发起工具调用到工具结果进入 transcript，当前链路大致如下：

1. assistant 消息里出现一个或多个 `ToolCallContent`
2. 运行时查找对应工具
3. 完成参数预处理和参数校验
4. 执行 `before_tool_call`
5. 调用工具 `execute()`
6. 执行 `after_tool_call`
7. 发送 `tool_execution_end`
8. 构造 `ToolResultMessage`
9. 发送该消息的 `message_start` / `message_end`
10. 把 `ToolResultMessage` 追加进当前上下文和 transcript

这里有两个很重要的实现细节：

- `tool_execution_end` 先于对应的 `ToolResultMessage` 的 `message_start` / `message_end`
- `ToolResultMessage` 是从 `AgentToolResult` 深拷贝构造出来的，因此后续修改原始结果对象不会影响已落地 transcript

工具结果进入 transcript 后，后续模型轮次会把它视为普通上下文的一部分继续消费。因此工具不仅是一次外部调用，更是对后续对话语义的直接输入。

这也解释了为什么工具错误不会简单被吞掉：即使失败，错误型 `ToolResultMessage` 仍然会进入 transcript，让后续模型有机会基于该错误继续推理或解释。

### 8.8 工具注册最小示例

下面这个例子展示了一个最小可用工具，以及它如何注册到 `Agent` 中。它的目标是说明“工具定义长什么样、怎么挂到 `AgentOptions.tools`”，不是演示一次完整可运行的工具调用闭环。

```python
from dataclasses import dataclass

from agentsdk import (
    Agent,
    AgentOptions,
    AgentToolResult,
    ModelInfo,
    TextContent,
    ToolExecutionMode,
)

# 假设 adapter 已按 2.3 节的方式完成初始化


@dataclass
class WeatherTool:
    name: str = "get_weather"
    label: str = "Get Weather"
    description: str | None = "根据城市名返回简短天气摘要"
    input_schema: dict | None = None
    prepare_arguments = None

    def __post_init__(self):
        self.input_schema = {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
            },
            "required": ["city"],
            "additionalProperties": False,
        }

    async def execute(self, tool_call_id, params, cancel_token=None, on_update=None):
        city = params["city"]
        return AgentToolResult(
            content=[TextContent(text=f"{city} 当前晴朗，22 度")],
            details={"city": city, "temperature": 22},
        )


weather_tool = WeatherTool()

agent = Agent(
    AgentOptions(
        system_prompt="你是一个会在必要时调用工具的助手。",
        model=ModelInfo(id="gpt-4o-mini", provider="openai", api="chat.completions"),
        stream_fn=adapter.stream_message,
        tools=[weather_tool],
        tool_execution=ToolExecutionMode.PARALLEL,
    )
)
```

这个例子体现的关键点是：

- 工具只需要实现协议要求的字段和 `execute()`
- `input_schema` 用于保护参数形状
- 返回值用 `AgentToolResult` 表达，而不是直接返回字符串

如果你要跑一个完整闭环，最小路径通常是：

1. 按第 2.3 节初始化 `AsyncOpenAI` 客户端和 `OpenAIChatCompletionsAdapter`
2. 把工具注册到 `AgentOptions.tools`
3. 再调用一次 `await agent.prompt(...)`，让模型自行判断是否需要发起工具调用

真实运行时，assistant 是否会选择调用这个工具，取决于：

- `description` 是否清晰
- `input_schema` 是否足够明确
- 当前提示词和上下文是否真的让模型判断“需要使用工具”

因此如果你发现模型没有调用工具，不一定是运行时有问题，也可能只是工具定义对模型来说还不够清晰。

## 9. Tool Hook 参考

### 9.1 `before_tool_call`

`before_tool_call` 是工具调用前置 Hook，用于在工具真正执行之前插入策略判断、审计或参数级治理逻辑。

签名如下：

```python
async def before_tool_call(context, cancel_token) -> BeforeToolCallResult | None:
    ...
```

当前 `BeforeToolCallContext` 包含：

| 字段 | 说明 |
| --- | --- |
| `assistant_message` | 当前触发工具调用的 assistant 消息 |
| `tool_call` | 原始 `ToolCallContent` |
| `args` | 已经预处理并通过校验的参数 |
| `context` | 当前 `AgentContext` |

返回值可以是：

- `None`：不拦截，继续执行
- `BeforeToolCallResult(block=True, reason="...")`：阻断执行并把原因收敛成错误结果

需要注意：

- 这个 Hook 发生在 `prepare_arguments` 和参数校验之后
- 如果工具不存在，当前实现不会进入 `before_tool_call`
- 如果参数准备或校验本身失败，也不会进入 `before_tool_call`

因此它更适合做“已知工具、已知参数”的策略控制，而不是兜底参数解析。

### 9.2 `after_tool_call`

`after_tool_call` 是工具调用后置 Hook，用于在工具执行完成之后、结果写回 transcript 之前统一改写结果。

签名如下：

```python
async def after_tool_call(context, cancel_token) -> AfterToolCallResult | None:
    ...
```

当前 `AfterToolCallContext` 包含：

| 字段 | 说明 |
| --- | --- |
| `assistant_message` | 当前触发工具调用的 assistant 消息 |
| `tool_call` | 当前工具调用描述 |
| `args` | 传给工具执行的参数 |
| `result` | 工具原始 `AgentToolResult` |
| `is_error` | 当前结果是否为错误 |
| `context` | 当前 `AgentContext` |

返回值 `AfterToolCallResult` 可控制三部分：

| 字段 | 作用 |
| --- | --- |
| `content` | 改写最终回写内容 |
| `details` | 改写最终结构化细节 |
| `is_error` | 改写错误标记 |

这里有一个实现细节很关键：

- 如果某字段保持默认 `UNSET`，表示“沿用原值”
- 如果你显式传入 `content=None`，当前实现会把最终内容变成空列表，而不是保留原内容

这意味着写 `after_tool_call` 时，最好只显式返回你真的想改写的字段。

适合的使用场景：

- 把原始工具结果包装成统一文案
- 对错误结果做友好化处理
- 给结果额外补充业务标记
- 统一裁剪掉不该直接暴露给模型的细节

### 9.3 调用前拦截示例

下面这个例子展示了如何在调用前阻断某个工具，或限制某些参数范围。

```python
from agentsdk import BeforeToolCallResult


async def before_tool_call(context, cancel_token):
    if context.tool_call.name == "delete_file":
        return BeforeToolCallResult(
            block=True,
            reason="当前会话不允许执行删除类工具",
        )

    if context.tool_call.name == "search_docs":
        query = context.args.get("query", "")
        if len(query.strip()) < 2:
            return BeforeToolCallResult(
                block=True,
                reason="搜索关键词过短，请提供更明确的问题",
            )

    return None
```

这个 Hook 生效后，工具并不会真正执行；运行时会直接生成一个错误型结果，并继续保持统一链路：

- 发出 `tool_execution_end`
- 生成对应的 `ToolResultMessage(is_error=True)`
- 把该错误结果写回 transcript

也就是说，从 Agent 整体运行视角看，“被 Hook 阻断”仍然是一种被收敛的工具结果，而不是一次未处理异常。

### 9.4 调用后改写结果示例

下面这个例子展示了如何在工具调用后统一包装结果，让 transcript 中出现的内容更适合被后续模型继续消费。

```python
from agentsdk import AfterToolCallResult, TextContent


async def after_tool_call(context, cancel_token):
    if context.tool_call.name != "get_weather":
        return None

    if context.is_error:
        return AfterToolCallResult(
            content=[TextContent(text="天气服务暂时不可用，请稍后重试。")],
            is_error=True,
        )

    city = context.args["city"]
    raw_text = ""
    for block in context.result.content:
        if isinstance(block, TextContent):
            raw_text += block.text

    return AfterToolCallResult(
        content=[TextContent(text=f"[天气结果] {city}: {raw_text}")],
        details={
            "city": city,
            "source": "weather_tool",
            "raw": context.result.details,
        },
        is_error=False,
    )
```

这个模式适合在以下场景复用：

- 统一结果格式，减少后续模型理解成本
- 对内部错误文案做外部友好化
- 追加结构化元数据，方便上层程序消费

如果你同时使用 `before_tool_call` 和 `after_tool_call`，可以把它们理解为：

- `before_tool_call` 决定“能不能执行”
- `after_tool_call` 决定“结果最终以什么样子落地”

## 10. 上下文与模型适配

### 10.1 `transform_context`

`transform_context` 用于在内部 transcript 真正发送给模型之前，对消息序列做最后一轮上下文变换。它是最直接的“上下文裁剪 / 重排 / 补充”扩展点。

签名如下：

```python
def transform_context(messages, cancel_token) -> Sequence[AgentMessage]:
    ...
```

也可以是异步函数。

当前执行时机如下：

1. 运行时拿到当前 `AgentContext.messages`
2. 如果配置了 `transform_context`，先对这组内部消息做变换
3. 再把变换结果交给 `convert_to_llm`
4. 最终交给 `stream_fn`

这意味着 `transform_context` 看到的是“内部消息模型”，而不是已经适配成某个模型协议的消息结构。

最常见的用途：

- 只保留最近 N 条消息
- 压缩历史 transcript
- 删除不想传给模型的中间消息
- 在模型请求前补充额外上下文

最小示例：

```python
def transform_context(messages, cancel_token):
    return list(messages)[-12:]
```

一个稍微常见一点的变体，是只保留最近若干条用户 / assistant / toolResult 消息，而把更老的上下文交给外部摘要逻辑处理。

需要注意：

- `transform_context` 不会直接改写 `agent.state.messages`；它只影响“本次发送给模型的上下文视图”。
- 如果你在这里返回了不符合内部消息结构的对象，后续 `convert_to_llm` 或适配器层很可能会出错。
- 当前 `system_prompt` 并不在 `messages` 参数里，它会继续保留在 `AgentContext.system_prompt` 中。

因此，如果你要做的是“改写上下文窗口”，优先用 `transform_context`；如果你要做的是“把内部消息翻译成某种模型协议”，优先用 `convert_to_llm`。

### 10.2 `convert_to_llm`

`convert_to_llm` 用于把内部消息结构转换成最终交给模型适配层的消息序列。它发生在 `transform_context` 之后，是进入 `stream_fn` 之前的最后一层协议转换。

签名如下：

```python
def convert_to_llm(messages) -> Sequence[LLMMessage]:
    ...
```

也可以是异步函数。

当前默认行为：

- 如果你没有显式提供 `convert_to_llm`
- `Agent` 会使用内置默认实现
- 默认实现只保留 `role` 为 `user`、`assistant`、`toolResult` 的消息

当前仓库的默认实现并不做复杂重映射，更偏向保守透传。这意味着：

- 对于当前的 OpenAI Chat Completions 适配器，内部消息结构已经足够接近目标输入
- 如果你要对接别的协议、或者想改写某些消息表示方式，就需要自行提供 `convert_to_llm`

最小示例：

```python
def convert_to_llm(messages):
    return [message for message in messages if message.role != "toolResult"]
```

上面这个例子只是为了说明能力，不代表推荐默认做法。多数情况下，`toolResult` 仍然应该保留给模型。

更实际的使用场景包括：

- 自定义适配器只接受某种特定消息子集
- 想在进入模型前把内部消息转换成另一套 LLMMessage 协议
- 想在极特殊场景下屏蔽某类历史消息

使用建议：

- 只要当前默认适配器已经够用，尽量不要过早自定义 `convert_to_llm`
- 一旦自定义，最好保持输出仍然是结构稳定、可被 `stream_fn` 理解的消息序列
- 如果问题只是“上下文太长”，优先改 `transform_context`，不要把 `convert_to_llm` 当作裁剪器滥用

### 10.3 `stream_fn`

`stream_fn` 是整个运行时最核心的模型适配入口。`Agent` 并不直接调用模型，而是把当前 `ModelInfo`、上下文和运行配置传给 `stream_fn`，由它返回一个 `AssistantMessageEventStream`。

签名约定如下：

```python
async def stream_fn(
    model: ModelInfo,
    context: AgentContext,
    config: AgentLoopConfig,
    cancel_token,
) -> AssistantMessageEventStream:
    ...
```

从调用链上看，`stream_fn` 的输入已经是：

- 经过 `transform_context` 处理后的消息
- 再经过 `convert_to_llm` 转换后的消息
- 仍然带着 `system_prompt` 和工具列表的 `AgentContext`

`stream_fn` 需要返回的不是最终 assistant 消息本身，而是一个事件流对象。这个对象需要满足两点：

- 可异步迭代，产出 `AssistantMessageEvent`
- 提供 `result()` 方法，返回最终 `AssistantMessage`

为什么是这个协议：

- 这样运行时既能处理流式增量
- 也能把非流式结果统一收敛成相同的消息生命周期事件

最常见的配置方式是直接把适配器方法传进来：

```python
options = AgentOptions(
    model=model,
    stream_fn=adapter.stream_message,
)
```

需要特别强调：

- 如果没有配置 `stream_fn`，当前 `Agent` 会在真正执行时因缺少模型调用入口而失败。
- 失败不会一定以原始异常直接抛出到调用方；在默认运行链路里，常常会被收敛成一条 `AssistantMessage(stop_reason="error")`。

因此，对接新模型或新协议时，`stream_fn` 是第一优先级扩展点；只要这一层稳定，前面的上下文组织和后面的事件/工具链才能正常工作。

### 10.4 OpenAI Chat Completions 适配器

当前仓库已经提供了一个开箱可用的适配器：[openai_chatcompletions.py](/Users/bg/project/uu-work/agentsdk/adapters/openai_chatcompletions.py) 中的 `OpenAIChatCompletionsAdapter`。

最常见的接法如下：

```python
from openai import AsyncOpenAI

from agentsdk import Agent, AgentOptions, ModelInfo
from agentsdk.adapters.openai_chatcompletions import OpenAIChatCompletionsAdapter

client = AsyncOpenAI(
    api_key=api_key,
    base_url=base_url,
)
adapter = OpenAIChatCompletionsAdapter(client)

agent = Agent(
    AgentOptions(
        model=ModelInfo(
            id="gpt-4o-mini",
            provider="openai",
            api="chat.completions",
        ),
        stream_fn=adapter.stream_message,
    )
)
```

这个适配器当前做了几件事：

1. 把内部消息映射成 OpenAI 请求格式

- `UserMessage` 会映射成 `role="user"`，并把 `TextContent` / `ImageContent` 转成内容数组
- `AssistantMessage` 会映射成 `role="assistant"`，文本和 tool calls 分开表示
- `ToolResultMessage` 会映射成 `role="tool"`

2. 把工具定义映射成 OpenAI tools 协议

- 读取工具的 `name`
- 读取 `description`
- 把 `input_schema` 放入 `parameters`

3. 统一处理非流式和流式响应

- `create_message()`：更偏“一次性完成”的接口，直接返回最终 `AssistantMessage`
- `stream_message()`：返回 `AssistantMessageEventStream`，供当前 `Agent` 主流程使用

在本文示例和推荐接法中，通常把 `adapter.stream_message` 传给 `AgentOptions.stream_fn`，因为它更适合统一流式和非流式的收敛契约。需要注意的是，`Agent` 本身并不会默认内置 OpenAI 适配器；是否使用 `stream_message()` 仍由调用方传入的 `stream_fn` 决定。

4. 增量收敛 tool call 参数

在流式场景下，工具调用参数通常会分块到达。适配器会尽量修复和解析部分 JSON，把它持续映射到 `AssistantToolCallDelta.tool_call.arguments` 中。这样上层在 `message_update` 时就能看到越来越完整的参数对象。

5. 透传常用请求参数

当前会透传：

- `api_key`
- `temperature`
- `top_p`
- `max_tokens`
- `metadata`
- `tools`
- 流式请求时的 `stream_options={"include_usage": True}`

需要特别说明两点：

- 当前适配器真正使用 `base_url` 的推荐方式仍然是在创建 `AsyncOpenAI` 客户端时传入，而不是依赖 `AgentOptions.base_url` 自动生效。
- 当前适配器内部即使遇到映射或调用异常，通常也会把异常收敛成错误型 `AssistantMessage` 或错误事件流，而不是把 `OpenAIAdapterError` 直接抛给大多数上层调用方。

如果你的模型服务兼容 OpenAI Chat Completions 协议，这个适配器通常已经足够作为第一版接入方案。

## 11. 错误处理与边界行为

### 11.1 `AgentAlreadyRunningError`

`AgentAlreadyRunningError` 表示你在一个已有活跃 run 的 `Agent` 上，又尝试发起新的 `prompt()` 或 `continue_()`。

当前会在这些场景直接抛出：

- 正在执行 `prompt()` 时再次调用 `prompt()`
- 正在执行 run 时调用 `continue_()`
- 内部生命周期方法发现 `_active_run` 尚未结束

最常见的触发方式：

```python
first = asyncio.create_task(agent.prompt("first"))
await asyncio.sleep(0)
await agent.prompt("second")  # 这里会抛 AgentAlreadyRunningError
```

如何处理：

- 想等当前 run 结束后再继续：先 `await agent.wait_for_idle()`
- 想在当前 run 中插入新消息：改用 `steer()` 或 `follow_up()`
- 想停止当前 run 再开始新的：`abort()` -> `wait_for_idle()` -> 再次 `prompt()`

它属于“直接向调用方抛出的边界错误”，而不是被收敛成消息的那一类问题。

### 11.2 `InvalidContinuationError`

`InvalidContinuationError` 表示当前 transcript 边界不允许执行 `continue_()`。

当前会在这些场景直接抛出：

- transcript 为空
- 最后一条消息是 assistant，且 steering / follow-up 队列都为空

示例：

```python
await agent.continue_()  # 如果当前没有可继续边界，会直接抛错
```

推荐处理方式：

- 如果你真正想表达的是“新的一轮输入”，改用 `prompt()`
- 如果你确实想续跑，先检查最后一条消息角色以及队列中是否已有待处理消息
- 在恢复历史会话时，可以先用 `messages=[...]` 初始化一个可继续边界

它同样属于“直接抛出给调用方的使用边界错误”。

### 11.3 `OpenAIAdapterError`

`OpenAIAdapterError` 是 OpenAI Chat Completions 适配层内部使用的错误类型，用于表示消息映射或响应解析出现了适配器级问题。

当前源码中，典型触发点包括：

- 遇到不支持的消息角色
- OpenAI 响应里没有可用 choice

但是从调用方视角，需要非常明确地区分两层：

#### 作为“内部错误类型”

在适配器内部，确实会显式 `raise OpenAIAdapterError(...)`。

#### 作为“默认外部表现”

在 `create_message()` 和 `stream_message()` 这两个对外方法里，绝大多数异常又会被捕获，并收敛成：

- 一条错误型 `AssistantMessage`
- 或一个带 `AssistantStreamError` 的事件流

这意味着：

- 你在阅读源码时会看到 `OpenAIAdapterError`
- 但在实际使用 `Agent + adapter.stream_message` 时，更常见的外部表现不是 Python 异常，而是 transcript 里出现一条 `stop_reason="error"` 的 assistant 消息

因此，排查这类问题时，除了 try/except，更应该检查：

- `agent.state.error_message`
- 最后一条 `AssistantMessage.error_message`
- `turn_end` / `message_end` 中 assistant 的 `stop_reason`

只有在你直接调用更底层的适配器逻辑、或未来扩展没有捕获这类异常时，`OpenAIAdapterError` 才更可能直接浮出到调用方。

### 11.4 工具参数与执行错误

工具相关错误在当前默认链路中，通常不会直接让整个 Agent run 崩掉，而是会被统一收敛为错误型工具结果。

当前已经覆盖的常见错误包括：

- 工具不存在
- `prepare_arguments` 抛异常
- `input_schema` 校验失败
- `before_tool_call` 阻断
- 工具 `execute()` 抛异常

这些错误最终通常会表现为：

- 一条 `ToolResultMessage(is_error=True)`
- `content` 中是一段错误文本
- 工具调用链仍然完成闭环

例如：

- 工具未找到时：`"Tool xxx not found"`
- 参数不合法时：如 `"args.text is required"`
- 工具执行异常时：如异常消息 `"boom failed"`
- 被前置 Hook 阻断时：如 `"blocked by policy"`

这类设计的好处是，后续模型仍然能看到这条错误结果，并基于它继续推理或向用户解释，而不是整轮对话直接中断。

需要特别说明：

- 虽然 `ToolPreparationError` 已经被定义并在 runtime 层导出，但在当前主工具执行链里，参数准备失败更常见的默认表现仍然是“被收敛为错误工具结果”，而不是直接抛出这个异常。
- 因此你在业务侧处理工具错误时，优先检查 `ToolResultMessage.is_error`，而不是只依赖捕获 Python 异常。

### 11.5 `aborted` 与 `error` 的区别

`aborted` 和 `error` 都表示“这轮 assistant 没有正常完成”，但语义不同。

#### `aborted`

表示这轮运行是因为取消而收束结束的。常见来源：

- 你显式调用了 `agent.abort()`
- 底层流式处理感知到了取消令牌

此时常见表现是：

- `AssistantMessage.stop_reason == "aborted"`
- `AssistantMessage.error_message` 中带有取消信息
- `agent.state.error_message` 也可能记录相同信息

#### `error`

表示这轮运行因为异常或失败而结束。常见来源：

- `stream_fn` 缺失
- 适配器映射失败
- 模型请求异常
- 运行时内部出现未被更低层吞掉的异常

此时常见表现是：

- `AssistantMessage.stop_reason == "error"`
- `error_message` 带有异常描述

需要注意的一点是：工具错误不一定会把当前 assistant 变成 `error`。更常见的情况是工具错误被收敛为 `ToolResultMessage(is_error=True)`，随后模型还能继续下一轮。

因此可以简单记忆为：

- `aborted`：人为或系统取消导致的收尾
- `error`：本轮 assistant 本身发生了失败
- 工具错误：多数情况下先体现在 `ToolResultMessage.is_error`

### 11.6 常见接入误区

下面这些是当前仓库接入时最容易踩到的坑：

1. 以为 `prompt()` 会直接返回文本

不会。`prompt()` 启动的是一整轮 run，最终结果要从事件流或 `agent.state.messages` 里读取。

2. 忘记配置 `stream_fn`

如果没有可用的 `stream_fn`，当前运行时无法真正调用模型。默认表现通常不是优雅成功，而是收敛成错误 assistant 消息。

3. 把 `continue_()` 当作“再来一次”

`continue_()` 只在合法 transcript 边界上可用。想发起新的用户输入，请用 `prompt()`。

4. 在活跃 run 期间再次 `prompt()`

这会直接触发 `AgentAlreadyRunningError`。如果只是想插入新消息，应改用 `steer()` 或 `follow_up()`。

5. 只监听 `message_update`，不处理 `message_end`

这样你可能错过最终错误信息，也不容易知道一条消息是否已经稳定结束。

6. 误以为工具错误会直接抛异常终止整轮 run

默认情况下，大多数工具错误会被收敛为 `ToolResultMessage(is_error=True)`，而不是直接炸掉整个 Agent。

7. 以为只设置 `AgentOptions.base_url` 就会自动改客户端地址

对于当前默认 OpenAI 适配器，更稳妥的做法仍然是在创建 `AsyncOpenAI` 客户端时传入 `base_url`。

8. 在监听器里做过重逻辑

监听器会被 `wait_for_idle()` 一起等待，过重的 I/O 会直接拉长整轮运行的收尾时间。

9. 过早自定义 `convert_to_llm`

很多上下文裁剪问题其实用 `transform_context` 就够了。过早改协议转换层，往往更容易引入不必要的兼容问题。

## 12. 常见接入模式

### 12.1 CLI 聊天程序

这是最直接的接入模式，适合作为第一版实现。典型目标包括：

- 在终端中和 Agent 多轮对话
- 实时打印流式文本
- 保留上下文
- 在错误或中止时给出明确反馈

推荐组合：

- `Agent`
- `AgentOptions`
- `OpenAIChatCompletionsAdapter.stream_message`
- `subscribe()` 监听 `message_update`
- 如需后台运行或中止收尾，再配合 `wait_for_idle()`

典型流程：

1. 初始化 `AsyncOpenAI` 客户端
2. 创建 `OpenAIChatCompletionsAdapter`
3. 构造 `AgentOptions(system_prompt, model, stream_fn, temperature, max_tokens)`
4. 注册一个轻量事件监听器，处理 `message_start` / `message_update` / `message_end`
5. 在循环中调用 `await agent.prompt(user_input)`

仓库中的 [agent_sdk_demo.py](/Users/bg/project/uu-work/agent_sdk_demo.py) 就是这一模式的完整参考。

适用场景：

- 命令行助手
- 内部调试工具
- 快速验证模型接入是否正常
- 做第一版 SDK 演示

实现建议：

- 文本流式展示主要依赖 `message_update`
- 轮结束时仍要检查 `AssistantMessage.error_message`
- 在 CLI 里尽量保持监听器轻量，避免输出阻塞影响整轮响应

如果目标是桌面端、Web 聊天框或 Bot 控制台，这个模式通常也可以直接作为骨架，只需把终端输出替换成对应的 UI 更新逻辑。

### 12.2 带工具的问答 Agent

这是最常见的增强型接入模式，目标是在问答过程中按需调用工具获取外部信息或执行外部动作。

推荐组合：

- `AgentOptions.tools`
- `ToolExecutionMode`
- 工具定义中的 `name` / `description` / `input_schema` / `execute()`
- 可选的 `before_tool_call` / `after_tool_call`
- UI 或日志侧监听 `tool_execution_*` 事件

典型流程：

1. 定义一组查询型或只读型工具
2. 给每个工具提供清晰的 `description` 和可校验的 `input_schema`
3. 注册到 `AgentOptions.tools`
4. 根据业务特点选择 `SEQUENTIAL` 或 `PARALLEL`
5. 通过 `tool_execution_start` / `tool_execution_end` 展示执行过程
6. 允许 assistant 基于 `ToolResultMessage` 继续生成最终答案

适用场景：

- 文档问答
- 数据查询助手
- 文件检索 / 搜索类助手
- 带工具增强的研发助手

实践建议：

- 第一版工具尽量做“查询型”“纯函数型”，避免高副作用工具一开始就进入主链路
- 如果工具调用失败，优先让错误回写 transcript，而不是直接打断整轮对话
- 如果你需要控制某类工具的权限或输入范围，优先在 `before_tool_call` 里做策略控制

如果目标不是纯聊天，而是“能查、能取、能补充事实”的问答体验，这通常是最值得优先落地的模式。

### 12.3 可中止的长任务 Agent

这一模式适用于单次运行耗时较长、且用户需要随时停止的场景，例如：

- 长文本分析
- 多工具串联查询
- 慢外部服务调用
- 需要连续流式输出的长回答

推荐组合：

- `abort()`
- `wait_for_idle()`
- `state.is_streaming`
- `state.error_message`
- 监听 `agent_end` 或 `turn_end`

典型流程：

1. 用 `asyncio.create_task(agent.prompt(...))` 启动 run
2. 在 UI 或上层控制器里保留“停止”按钮
3. 用户请求停止时调用 `agent.abort()`
4. 随后 `await agent.wait_for_idle()`
5. 根据最后一条 assistant 消息的 `stop_reason` 区分是正常完成还是取消结束

适用场景：

- Web 端“停止生成”
- IDE / 桌面端“中止分析”
- API 服务端为长请求提供取消入口

接入建议：

- 把 `aborted` 和普通 `error` 分开展示，避免用户把主动取消误解为系统异常
- 如果你的工具本身也支持取消，尽量在 `execute()` 中响应 `cancel_token`
- 如果 run 是后台启动的，收尾逻辑应以 `wait_for_idle()` 为完整边界；如果你直接 `await prompt()`，它返回时通常已经完成收尾

这一模式的关键不只是“能停”，而是“停止后状态仍一致、事件仍完整、收尾仍可控”。

### 12.4 带外部状态同步的 Agent

这一模式适合把 `agentsdk` 嵌入更大的系统中，例如：

- Web 聊天服务
- 桌面端会话管理器
- 日志 / trace 采集系统
- 需要把运行过程同步到数据库、缓存或消息队列的服务端

推荐组合：

- `subscribe()`
- `agent.state`
- `AgentEndEvent.messages`
- `metadata`
- 可选的 `session_id`

典型思路是“两条线并行”：

1. 用事件推送增量变化

- `message_update` 推 UI 增量文本
- `tool_execution_update` 推进度
- `turn_end` 推回合级完成信号
- `agent_end` 作为最终收口

2. 用 `state` 读取稳定快照

- transcript 最终存档
- 当前错误信息
- pending tool calls
- 是否仍在流式中

常见实现策略：

- 事件只负责“推变化”
- 持久化或最终一致性状态，以 `agent_end` 后的 `agent.state` 为准

这样可以避免把每个中间态都当作最终事实写入外部系统。

适用场景：

- 需要和前端会话状态同步的后端
- 需要留完整运行轨迹的审计系统
- 需要把每轮对话和工具执行过程打到 trace / 日志平台的应用

使用建议：

- 外部状态同步逻辑尽量异步、可控，避免监听器过重
- 对“流式中间态”和“最终落地态”做明确区分
- 如果要跨线程或跨请求保存会话身份，可以结合 `metadata` 和上层自己的 session key 使用

## 13. 附录

### 13.1 常用类型速查

下表可作为阅读和接入时的快速索引。

| 类型 | 作用 | 常见使用位置 |
| --- | --- | --- |
| `Agent` | 对外统一入口 | 初始化、发起 run、读取状态 |
| `AgentOptions` | Agent 配置中枢 | 构造 `Agent` 时 |
| `ModelInfo` | 模型元信息 | 配置模型身份与协议 |
| `AgentContext` | 当前上下文快照 | 适配器、Hook、工具链 |
| `UserMessage` | 用户输入消息 | `prompt()`、手动构造消息 |
| `AssistantMessage` | 模型输出消息 | transcript、事件、错误收敛 |
| `ToolResultMessage` | 工具结果消息 | transcript、工具回写 |
| `TextContent` | 文本内容块 | 用户、assistant、工具结果 |
| `ImageContent` | 图片内容块 | 用户输入、工具结果 |
| `ThinkingContent` | thinking 内容块 | assistant 流式推理 |
| `ToolCallContent` | assistant 工具调用块 | assistant 消息内容 |
| `AgentTool` | 工具协议 | 注册业务工具 |
| `AgentToolResult` | 工具执行结果结构 | 工具返回值、Hook 改写 |
| `BeforeToolCallContext` | 前置 Hook 上下文 | `before_tool_call` |
| `BeforeToolCallResult` | 前置 Hook 返回结构 | 拦截工具执行 |
| `AfterToolCallContext` | 后置 Hook 上下文 | `after_tool_call` |
| `AfterToolCallResult` | 后置 Hook 返回结构 | 改写工具结果 |
| `ToolExecutionMode` | 工具执行模式枚举 | 串行 / 并行配置 |
| `ThinkingLevel` | thinking 级别枚举 | thinking 配置透传 |
| `AgentEvent` | 事件联合类型 | 订阅监听器 |
| `OpenAIChatCompletionsAdapter` | 默认 OpenAI 适配器 | 模型接入 |

如果是首次接入，优先掌握以下几类对象即可：

- `Agent`
- `AgentOptions`
- `ModelInfo`
- `UserMessage` / `AssistantMessage`
- `AgentTool`
- `OpenAIChatCompletionsAdapter`

### 13.2 事件类型速查

常用事件如下：

| 事件类型 | 事件对象 | 何时出现 | 常见用途 |
| --- | --- | --- | --- |
| `agent_start` | `AgentStartEvent` | 一次 run 开始 | 标记本轮开始 |
| `agent_end` | `AgentEndEvent` | 一次 run 完整结束 | 本轮收尾、落盘 |
| `turn_start` | `TurnStartEvent` | 新一轮 turn 开始 | 回合级 UI / 统计 |
| `turn_end` | `TurnEndEvent` | 一轮 turn 结束 | 判断本轮是否有工具结果 |
| `message_start` | `MessageStartEvent` | 一条消息开始 | 初始化消息展示 |
| `message_update` | `MessageUpdateEvent` | assistant 流式更新 | 打字机输出、增量 UI |
| `message_end` | `MessageEndEvent` | 一条消息结束 | 读取最终消息结果 |
| `tool_execution_start` | `ToolExecutionStartEvent` | 工具开始执行 | 展示开始调用某工具 |
| `tool_execution_update` | `ToolExecutionUpdateEvent` | 工具上报中间结果 | 展示进度 |
| `tool_execution_end` | `ToolExecutionEndEvent` | 工具执行结束 | 展示完成状态 |

流式 assistant 的典型消息事件顺序：

1. `message_start`
2. 零次或多次 `message_update`
3. `message_end`

工具执行的典型事件顺序：

1. `tool_execution_start`
2. 零次或多次 `tool_execution_update`
3. `tool_execution_end`
4. 与对应 `ToolResultMessage` 相关的 `message_start`
5. `message_end`

如果你主要做 UI，同步掌握这两条顺序即可。

### 13.3 `AgentOptions` 字段速查

下表按接入优先级整理了 `AgentOptions` 的主要字段。“当前状态”列与第 5 章使用同一套口径：

- “已生效”：默认运行链路已经实际消费该字段。
- “已保存、已透传”：字段会被保留并传入运行配置，但是否真正影响行为取决于适配器。
- “当前预留”：字段已建模，但默认主链路尚未消费。

| 字段 | 类型 | 默认值 | 当前状态 | 说明 |
| --- | --- | --- | --- | --- |
| `system_prompt` | `str` | `""` | 已生效；默认 OpenAI 适配器会注入请求 | 会话级系统提示 |
| `model` | `ModelInfo` | `ModelInfo()` | 已生效 | 模型身份与协议 |
| `tools` | `list[AgentTool]` | `[]` | 已生效 | 当前可用工具集 |
| `messages` | `list[AgentMessage]` | `[]` | 已生效 | 初始 transcript |
| `stream_fn` | `StreamFn \| None` | `None` | 已生效且关键 | 模型调用入口 |
| `convert_to_llm` | `ConvertToLLM \| None` | `None` | 已生效 | 协议转换层 |
| `transform_context` | `TransformContextHook \| None` | `None` | 已生效 | 上下文变换层 |
| `get_api_key` | `ApiKeyResolver \| None` | `None` | 已生效 | 动态解析 API Key |
| `api_key` | `str \| None` | `None` | 已生效 | 请求级 API Key |
| `temperature` | `float \| None` | `None` | 已生效 | 生成参数 |
| `top_p` | `float \| None` | `None` | 已生效 | 生成参数 |
| `max_tokens` | `int \| None` | `None` | 已生效 | 输出长度控制 |
| `before_tool_call` | `BeforeToolCallHook \| None` | `None` | 已生效 | 工具前置 Hook |
| `after_tool_call` | `AfterToolCallHook \| None` | `None` | 已生效 | 工具后置 Hook |
| `tool_execution` | `ToolExecutionMode` | `PARALLEL` | 已生效 | 工具串行 / 并行 |
| `steering_mode` | `MessageQueueMode` | `"one-at-a-time"` | 已生效 | steering 队列消费方式 |
| `follow_up_mode` | `MessageQueueMode` | `"one-at-a-time"` | 已生效 | follow-up 队列消费方式 |
| `metadata` | `dict[str, Any]` | `{}` | 已生效 | 请求附加元数据 |
| `thinking_level` | `ThinkingLevel` | `OFF` | 已保存、已透传 | 是否真正影响模型取决于适配器 |
| `thinking_budgets` | `Mapping[...]` | `{}` | 已保存、已透传 | 是否真正生效取决于适配器 |
| `session_id` | `str \| None` | `None` | 已保存、已透传 | 会话标识预留位 |
| `base_url` | `str \| None` | `None` | 已保存，但默认 OpenAI 适配器不直接读取它构建客户端 | 更适合作为外部客户端初始化参数 |
| `on_payload` | `PayloadListener \| None` | `None` | 当前预留 | 默认主链路未消费 |
| `max_retry_delay_ms` | `int \| None` | `None` | 当前预留 | 默认主链路未消费 |

如果只想用最小配置跑通，优先关注：

- `system_prompt`
- `model`
- `stream_fn`
- 可选的 `temperature` / `max_tokens`
- 如需工具则加 `tools`

### 13.4 示例索引

为便于按需求回看，下面给出示例索引，对应本文前面章节中的使用点。

| 示例主题 | 适合场景 | 优先阅读章节 |
| --- | --- | --- |
| 最小聊天示例 | 快速跑通第一个 Agent | 2.3、10.4 |
| 终端流式输出 | CLI / 控制台展示 | 7.1、7.6、12.1 |
| continuation 续跑 | 恢复或继续现有 transcript | 4.2 |
| steering / follow-up 入队 | 运行中插话、回合后追问 | 4.3、4.4 |
| 最小工具定义 | 给 Agent 添加外部能力 | 8.1、8.8 |
| 工具参数整形 | 模型参数不稳定时做预处理 | 8.3 |
| 工具前置拦截 | 权限控制、策略限制 | 9.1、9.3 |
| 工具结果改写 | 统一结果文案和结构 | 9.2、9.4 |
| 自定义上下文裁剪 | 长 transcript 压缩 | 10.1 |
| 自定义协议转换 | 接不同模型协议 | 10.2 |
| OpenAI 兼容接入 | 使用现成适配器 | 2.3、10.4 |
| 可中止长任务 | 停止生成、取消执行 | 4.5、12.3 |
| 外部状态同步 | Web 服务、日志、trace | 7.1、12.4 |

如果只想快速开始，建议按以下顺序阅读：

1. 第 2 章快速开始
2. 第 4 章核心方法
3. 第 7 章事件与流式输出
4. 第 8 章工具调用
5. 第 10 章模型适配
