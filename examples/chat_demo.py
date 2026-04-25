"""
Paimon SDK 简单聊天程序示例

功能：
- 使用 OpenAI `chat.completions` 或 `responses` API 进行对话
- 支持流式输出（打字机效果）
- 支持多轮对话（保留上下文）
- 配置从 examples/config.json 读取（模型、base_url、system_prompt等）
- API Key 优先从 examples/.env 读取，找不到时回退到仓库根 .env
- 输入 'exit' 或 'quit' 退出

使用方式：
    1. uv sync --extra examples
    2. 复制 examples/.env.example 为 examples/.env 并填入 OPENAI_API_KEY
    3. 修改 examples/config.json 配置模型参数
    4. uv run python examples/chat_demo.py
"""

import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

from paimonsdk import Agent, AgentOptions, ModelInfo, TextContent, AssistantMessage
from paimonsdk.adapters import OpenAIAdapter, OpenAIRequestConfig


def load_config():
    """从 config.json 加载配置"""
    config_path = Path(__file__).parent / "config.json"

    if not config_path.exists():
        raise FileNotFoundError(
            f"配置文件不存在: {config_path}\n"
            "请创建 config.json 文件"
        )

    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_api_key():
    """从 examples/.env 或仓库根 .env 加载 API Key"""
    env_paths = [
        Path(__file__).parent / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]

    for env_path in env_paths:
        if env_path.exists():
            load_dotenv(env_path)
            break

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or api_key == "your-api-key-here":
        raise ValueError(
            "请设置 OPENAI_API_KEY\n"
            "1. 复制 .env.example 为 .env\n"
            "2. 在 .env 文件中填入你的 API Key"
        )

    return api_key


def create_model_info(config: dict) -> ModelInfo:
    """从配置创建 ModelInfo"""
    model_config = config.get("model", {})

    return ModelInfo(
        id=model_config.get("id", "gpt-4o-mini"),
        provider=model_config.get("provider", "openai"),
        api=model_config.get("api", "chat.completions"),
        base_url=model_config.get("base_url", ""),
    )


async def main():
    # 1. 加载配置
    try:
        config = load_config()
        print(f"✓ 已加载配置: config.json")
    except FileNotFoundError as e:
        print(f"✗ 错误: {e}")
        return

    # 2. 加载 API Key
    try:
        api_key = load_api_key()
        print(f"✓ 已加载 API Key")
    except ValueError as e:
        print(f"✗ 错误: {e}")
        return

    # 3. 获取模型配置
    model_info = create_model_info(config)
    print(f"✓ 使用模型: {model_info.id}")
    print(f"✓ Base URL: {model_info.base_url or '默认'}")

    # 4. 创建 OpenAI 客户端
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=model_info.base_url if model_info.base_url else None
    )

    # 5. 创建适配器
    adapter = OpenAIAdapter(
        client,
        request_config=OpenAIRequestConfig(
            temperature=config.get("temperature", 0.7),
            max_tokens=config.get("max_tokens", None),
        ),
    )

    # 6. 配置并创建 Agent
    options = AgentOptions(
        system_prompt=config.get("system_prompt", "你是一个有帮助的助手。"),
        model=model_info,
        stream_fn=adapter.stream_message,
    )

    agent = Agent(options=options)

    # 7. 订阅事件以处理流式输出
    current_text_length = 0

    def on_event(event, cancel_token):
        """处理 Agent 事件，实现打字机效果"""
        nonlocal current_text_length

        # 调试：打印所有事件类型
        # print(f"[DEBUG] 事件类型: {event.type}", flush=True)

        # 消息开始：重置计数器
        if event.type == "message_start":
            current_text_length = 0
            # print(f"[DEBUG] message_start: {event.message}", flush=True)

        # 消息更新：打印新增的内容
        elif event.type == "message_update":
            # 从 MessageUpdateEvent 获取消息
            message = event.message
            # print(f"[DEBUG] message_update content blocks: {len(message.content)}", flush=True)
            for i, block in enumerate(message.content):
                # print(f"[DEBUG] block[{i}]: {type(block).__name__}", flush=True)
                if isinstance(block, TextContent):
                    full_text = block.text
                    # print(f"[DEBUG] text length: {len(full_text)}, current: {current_text_length}", flush=True)
                    # 只打印新增的部分
                    if len(full_text) > current_text_length:
                        new_text = full_text[current_text_length:]
                        print(new_text, end='', flush=True)
                        current_text_length = len(full_text)

        # 消息结束：重置计数器，检查是否有错误
        elif event.type == "message_end":
            current_text_length = 0
            # 检查消息是否有错误
            msg = event.message
            if isinstance(msg, AssistantMessage) and msg.error_message:
                print(f"\n[错误: {msg.error_message}]")
            # print(f"[DEBUG] message_end: stop_reason={msg.stop_reason if isinstance(msg, AssistantMessage) else 'N/A'}", flush=True)

        # 回合结束：检查停止原因
        elif event.type == "turn_end":
            msg = event.message
            if isinstance(msg, AssistantMessage):
                if msg.stop_reason == "error" and msg.error_message:
                    print(f"\n[调用错误: {msg.error_message}]")
                # print(f"[DEBUG] turn_end: stop_reason={msg.stop_reason}", flush=True)

    agent.subscribe(on_event)

    # 8. 开始交互式对话
    print()
    print("=" * 50)
    print("Paimon SDK 聊天程序")
    print("输入 'exit' 或 'quit' 退出")
    print("=" * 50)
    print()

    while True:
        # 获取用户输入
        try:
            user_input = input("\n👤 你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n👋 再见！")
            break

        # 检查退出命令
        if user_input.lower() in ("exit", "quit", "退出"):
            print("\n👋 再见！")
            break

        if not user_input:
            continue

        # 显示 AI 回复前缀
        print("\n🤖 AI: ", end='', flush=True)

        # 发送消息给 Agent
        try:
            await agent.prompt(user_input)
            # 等待 Agent 完成回复
            await agent.wait_for_idle()
        except Exception as e:
            print(f"\n[异常: {e}]")
            import traceback
            traceback.print_exc()

        print()  # 换行


if __name__ == "__main__":
    asyncio.run(main())
