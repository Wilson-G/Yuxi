"""直接 LLM 调试脚本的行为测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def llm_message_module():
    """加载独立脚本模块。"""

    script_path = Path(__file__).parents[2] / "scripts" / "send_llm_message.py"
    spec = importlib.util.spec_from_file_location("send_llm_message", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCompletions:
    """记录请求并返回预设文本。"""

    def __init__(self) -> None:
        self.request = None

    def create(self, **kwargs):
        self.request = kwargs
        message = type("Message", (), {"content": "模型响应"})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()


class FakeClient:
    """提供 OpenAI chat.completions 调用外观。"""

    def __init__(self) -> None:
        self.completions = FakeCompletions()
        self.chat = type("Chat", (), {"completions": self.completions})()


def test_send_message_forwards_one_complete_user_message(llm_message_module) -> None:
    client = FakeClient()

    response = llm_message_module.send_message(
        client,
        model="test-model",
        message="完整上下文\n与原材料",
        temperature=0.2,
    )

    assert response == "模型响应"
    assert client.completions.request == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "完整上下文\n与原材料"}],
        "temperature": 0.2,
    }


def test_read_config_requires_all_environment_values(llm_message_module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_message_module, "load_dotenv", lambda *_: None)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    with pytest.raises(ValueError, match="LLM_BASE_URL, LLM_API_KEY, LLM_MODEL"):
        llm_message_module.read_config(None)
