"""单次收发 LLM 消息，用于直接调试 Prompt。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析一次模型请求需要的参数。"""

    parser = argparse.ArgumentParser(description="直接向 LLM 发送一条消息并输出响应")
    parser.add_argument("--model", help="覆盖 .env 中的 LLM_MODEL")
    parser.add_argument("--temperature", type=float, default=0, help="模型温度，默认 0")
    message_source = parser.add_mutually_exclusive_group(required=True)
    message_source.add_argument("--message", help="直接发送的完整消息")
    message_source.add_argument("--message-file", type=Path, help="包含完整消息的 UTF-8 文件")
    message_source.add_argument("--stdin", action="store_true", help="从标准输入读取完整消息")
    return parser.parse_args(argv)


def read_message(args: argparse.Namespace) -> str:
    """读取调用者提供的一条完整消息。"""

    if args.message is not None:
        message = args.message
    elif args.message_file is not None:
        message = args.message_file.read_text(encoding="utf-8")
    else:
        message = sys.stdin.read()

    if not message.strip():
        raise ValueError("消息不能为空")
    return message


def read_config(model_override: str | None) -> tuple[str, str, str]:
    """读取直接调用 LLM 所需的环境配置。"""

    load_dotenv(Path(__file__).parents[2] / ".env")
    base_url = os.getenv("LLM_BASE_URL", "").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    model = (model_override or os.getenv("LLM_MODEL", "")).strip()

    missing = [
        name
        for name, value in (("LLM_BASE_URL", base_url), ("LLM_API_KEY", api_key), ("LLM_MODEL", model))
        if not value
    ]
    if missing:
        raise ValueError(f"缺少 LLM 调试配置: {', '.join(missing)}")
    return base_url, api_key, model


def send_message(
    client: Any,
    *,
    model: str,
    message: str,
    temperature: float,
) -> str:
    """向 OpenAI 兼容端点发送一条用户消息并返回响应。"""

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": message}],
        temperature=temperature,
    )
    content = response.choices[0].message.content
    if content is None:
        raise ValueError("LLM 未返回文本响应")
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def main(argv: Sequence[str] | None = None) -> None:
    """执行一次直接 LLM 调用。"""

    args = parse_args(argv)
    base_url, api_key, model = read_config(args.model)
    client = OpenAI(base_url=base_url, api_key=api_key)
    print(
        send_message(
            client,
            model=model,
            message=read_message(args),
            temperature=args.temperature,
        )
    )


if __name__ == "__main__":
    main()
