"""专家编译并发度的统一读取入口。

并发旋钮存放在 system_options 管理员配置中（设置页可调），
resolve 已按 数据库值 > 环境变量 > 默认值 兜底，这里只做类型收敛。
"""

from __future__ import annotations

from yuxi.config.options import system_options


async def get_expert_concurrency(key: str, default: int) -> int:
    """读取一个专家编译并发配置，最小为 1。

    配置记录尚未初始化时（如全新环境）按默认值处理，不阻断编译任务。
    """

    try:
        config = await system_options.get()
    except ValueError:
        return max(1, default)
    return max(1, int(config.get(key, default)))


__all__ = ["get_expert_concurrency"]
