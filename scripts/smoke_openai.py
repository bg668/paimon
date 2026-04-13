"""
测试脚本：直接验证 API 调用是否正常工作
"""

import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI


def load_config():
    """从 config.json 加载配置"""
    config_path = Path(__file__).resolve().parent.parent / "examples" / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_api_key():
    """从 .env 文件加载 API Key"""
    env_paths = [
        Path(__file__).parent / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    for env_path in env_paths:
        if env_path.exists():
            load_dotenv(env_path)
            break
    return os.environ.get("OPENAI_API_KEY")


async def test_api():
    """直接测试 OpenAI API 调用"""
    print("=" * 50)
    print("API 测试脚本")
    print("=" * 50)

    # 加载配置
    config = load_config()
    api_key = load_api_key()
    model_config = config.get("model", {})

    model_id = model_config.get("id", "gpt-4o-mini")
    base_url = model_config.get("base_url", None)

    print(f"\n配置信息:")
    print(f"  Model: {model_id}")
    print(f"  Base URL: {base_url or '默认'}")
    print(f"  API Key: {'已设置' if api_key else '未设置'}")

    # 创建客户端
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url if base_url else None
    )

    # 测试非流式调用
    print("\n" + "-" * 50)
    print("测试 1: 非流式调用")
    print("-" * 50)

    try:
        response = await client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": "你是一个有帮助的助手。"},
                {"role": "user", "content": "Hello, how are you?"}
            ],
            temperature=0.7,
        )
        print(f"✓ 调用成功!")
        print(f"  Model: {response.model}")
        print(f"  Content: {response.choices[0].message.content}")
    except Exception as e:
        print(f"✗ 调用失败: {e}")
        import traceback
        traceback.print_exc()

    # 测试流式调用
    print("\n" + "-" * 50)
    print("测试 2: 流式调用")
    print("-" * 50)

    try:
        stream = await client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": "你是一个有帮助的助手。"},
                {"role": "user", "content": "Say 'Hello World'"}
            ],
            temperature=0.7,
            stream=True,
        )

        print("  Response: ", end='', flush=True)
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                print(chunk.choices[0].delta.content, end='', flush=True)
        print()
        print("✓ 流式调用成功!")
    except Exception as e:
        print(f"✗ 流式调用失败: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 50)
    print("测试完成")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(test_api())
