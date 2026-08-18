"""Prime Agent ACP stdio 客户端。"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PrimeAcpError(RuntimeError):
    """表示 Prime Agent ACP 会话未能完成。"""


@dataclass(frozen=True)
class PrimeAcpResponse:
    """Prime ACP 单轮编译返回的文本与会话标识。"""

    text: str
    session_id: str


class _PrimeAcpConnection:
    """维护一次 Prime Agent ACP 子进程的 JSON-RPC 通信。"""

    def __init__(self, process: asyncio.subprocess.Process):
        self._process = process
        self._next_request_id = 1
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._text_chunks: list[str] = []
        self._stderr: list[str] = []
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """开始消费 Prime Agent 的标准输出与错误输出。"""

        self._stdout_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """发送一条 JSON-RPC 请求并等待对应响应。"""

        request_id = self._next_request_id
        self._next_request_id += 1
        loop = asyncio.get_running_loop()
        response = loop.create_future()
        self._pending[str(request_id)] = response
        await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        payload = await response
        if error := payload.get("error"):
            raise PrimeAcpError(f"Prime ACP {method} 失败: {error}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise PrimeAcpError(f"Prime ACP {method} 返回无效响应")
        return result

    async def close(self) -> None:
        """停止 Prime Agent 子进程并释放读取任务。"""

        if self._process.stdin is not None:
            self._process.stdin.close()

        if self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()

        for task in (self._stdout_task, self._stderr_task):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(task for task in (self._stdout_task, self._stderr_task) if task), return_exceptions=True)

    async def _read_stdout(self) -> None:
        assert self._process.stdout is not None
        try:
            while line := await self._process.stdout.readline():
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                if "method" in payload and "id" in payload:
                    await self._handle_agent_request(payload)
                elif "id" in payload:
                    pending = self._pending.pop(str(payload["id"]), None)
                    if pending is not None and not pending.done():
                        pending.set_result(payload)
                else:
                    self._record_notification(payload)
        finally:
            error = PrimeAcpError(self._exit_error_message())
            for pending in self._pending.values():
                if not pending.done():
                    pending.set_exception(error)
            self._pending.clear()

    async def _read_stderr(self) -> None:
        assert self._process.stderr is not None
        while line := await self._process.stderr.readline():
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                self._stderr.append(text)

    async def _handle_agent_request(self, payload: dict[str, Any]) -> None:
        method = payload.get("method")
        request_id = payload.get("id")
        if method != "session/request_permission" or request_id is None:
            await self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"不支持的 ACP 回调: {method}"},
                }
            )
            return

        params = payload.get("params")
        options = params.get("options") if isinstance(params, dict) else None
        allowed = next(
            (
                option
                for option in options or []
                if isinstance(option, dict) and option.get("kind") in {"allow_always", "allow_once"}
            ),
            None,
        )
        outcome = (
            {"outcome": "selected", "optionId": allowed["optionId"]}
            if isinstance(allowed, dict) and isinstance(allowed.get("optionId"), str)
            else {"outcome": "cancelled"}
        )
        await self._send({"jsonrpc": "2.0", "id": request_id, "result": {"outcome": outcome}})

    def _record_notification(self, payload: dict[str, Any]) -> None:
        if payload.get("method") != "session/update":
            return
        params = payload.get("params")
        update = params.get("update") if isinstance(params, dict) else None
        content = update.get("content") if isinstance(update, dict) else None
        if isinstance(content, dict) and content.get("type") == "text" and isinstance(content.get("text"), str):
            self._text_chunks.append(content["text"])

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._process.stdin is None:
            raise PrimeAcpError("Prime ACP 标准输入不可用")
        self._process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        await self._process.stdin.drain()

    def output_text(self) -> str:
        """返回 `session/update` 中收集的最终文本。"""

        return "".join(self._text_chunks).strip()

    def _exit_error_message(self) -> str:
        detail = self._stderr[-1] if self._stderr else ""
        return f"Prime ACP 子进程意外退出{f': {detail}' if detail else ''}"


async def run_prime_acp(
    *,
    cwd: Path,
    session_dir: Path,
    prompt: str,
    command: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> PrimeAcpResponse:
    """启动 Prime Agent ACP 会话并执行一次专家编译提示。"""

    command_parts = shlex.split(command or os.getenv("PRIME_AGENT_COMMAND", "prime-agent"))
    if not command_parts:
        raise PrimeAcpError("PRIME_AGENT_COMMAND 不能为空")

    cwd = cwd.resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        *command_parts,
        "--mode",
        "acp",
        "--offline",
        "--cwd",
        str(cwd),
        "--session-dir",
        str(session_dir.resolve()),
        *( ["--provider", provider] if provider else [] ),
        *( ["--model", model] if model else [] ),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    connection = _PrimeAcpConnection(process)
    await connection.start()
    try:
        await connection.request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
                "clientInfo": {"name": "yuxi", "version": "0.7.2"},
            },
        )
        session = await connection.request("session/new", {"cwd": str(cwd), "mcpServers": []})
        session_id = session.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise PrimeAcpError("Prime ACP 未返回 sessionId")
        await connection.request(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": prompt}]},
        )
        text = connection.output_text()
        if not text:
            raise PrimeAcpError("Prime ACP 未返回编译文本")
        return PrimeAcpResponse(text=text, session_id=session_id)
    finally:
        await connection.close()


__all__ = ["PrimeAcpError", "PrimeAcpResponse", "run_prime_acp"]
