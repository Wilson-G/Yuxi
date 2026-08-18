"""Prime Agent ACP 客户端测试。"""

import sys
from pathlib import Path

import pytest

from yuxi.expert.prime_acp import run_prime_acp


@pytest.mark.asyncio
async def test_run_prime_acp_performs_initialize_new_and_prompt(tmp_path: Path):
    peer = tmp_path / "acp_peer.py"
    peer.write_text(
        """
import json
import sys

for line in sys.stdin:
    request = json.loads(line)

    def emit(value):
        print(json.dumps(value), flush=True)

    if request[\"method\"] == \"initialize\":
        response = {
            \"jsonrpc\": \"2.0\",
            \"id\": request[\"id\"],
            \"result\": {\"protocolVersion\": 1},
        }
    elif request[\"method\"] == \"session/new\":
        response = {
            \"jsonrpc\": \"2.0\",
            \"id\": request[\"id\"],
            \"result\": {\"sessionId\": \"session-test\"},
        }
    elif request[\"method\"] == \"session/prompt\":
        for text in (\"PRIME_\", \"ACP_OK\"):
            emit({
                \"jsonrpc\": \"2.0\",
                \"method\": \"session/update\",
                \"params\": {\"update\": {
                    \"sessionUpdate\": \"agent_message_chunk\",
                    \"content\": {\"type\": \"text\", \"text\": text},
                }},
            })
        response = {
            \"jsonrpc\": \"2.0\",
            \"id\": request[\"id\"],
            \"result\": {\"stopReason\": \"end_turn\"},
        }
    else:
        response = {
            \"jsonrpc\": \"2.0\",
            \"id\": request[\"id\"],
            \"error\": {\"code\": -32601, \"message\": \"unknown\"},
        }
    emit(response)
""",
        encoding="utf-8",
    )

    result = await run_prime_acp(
        cwd=tmp_path,
        session_dir=tmp_path / "session",
        prompt="返回测试文本",
        command=f"{sys.executable} {peer}",
    )

    assert result.session_id == "session-test"
    assert result.text == "PRIME_ACP_OK"
