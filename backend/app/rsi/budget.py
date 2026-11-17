"""有界资源：请求计数、并发上限、单次超时。

区分两类预算，避免把本地确定性理解冒充成付费模型调用：
- `max_requests`：本轮 harness 的全部“模型/理解”调用次数上限（含 Fake）。默认值用于
  防止评测自身进入无限循环。
- `max_real_requests`：真实 GLM 供应商请求上限（用户授权最多 600 次，真实评测另需上限）。
  没有接入真实模型适配器时该计数恒为 0，报告里如实写明“未发生真实模型请求”。

预算耗尽一律抛 BudgetExhausted，由 runner 记录为 evidence insufficient，绝不重试。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


class BudgetExhausted(RuntimeError):
    def __init__(self, message: str, *, used: int, limit: int):
        super().__init__(message)
        self.used = used
        self.limit = limit


class RequestTimeout(RuntimeError):
    """单次调用超时；由调用方归因为 timeout，不重试。"""


@dataclass
class Counters:
    requests: int = 0
    real_requests: int = 0
    timeouts: int = 0
    rejected_by_budget: int = 0
    peak_concurrency: int = 0


@dataclass
class RequestBudget:
    max_requests: int = 2000
    max_real_requests: int = 600
    concurrency: int = 2
    timeout_s: float = 5.0
    counters: Counters = field(default_factory=Counters)

    def __post_init__(self) -> None:
        if self.max_requests < 1:
            raise ValueError('max_requests 必须 >= 1')
        if self.max_real_requests < 0:
            raise ValueError('max_real_requests 必须 >= 0')
        if self.concurrency < 1:
            raise ValueError('concurrency 必须 >= 1')
        if self.timeout_s <= 0:
            raise ValueError('timeout_s 必须 > 0')
        self._semaphore = asyncio.Semaphore(self.concurrency)
        self._active = 0
        self._lock = asyncio.Lock()

    @property
    def remaining(self) -> int:
        return max(0, self.max_requests - self.counters.requests)

    def exhausted(self) -> bool:
        return self.counters.requests >= self.max_requests

    async def _enter(self) -> None:
        await self._semaphore.acquire()
        async with self._lock:
            self._active += 1
            self.counters.peak_concurrency = max(self.counters.peak_concurrency, self._active)

    async def _exit(self) -> None:
        async with self._lock:
            self._active -= 1
        self._semaphore.release()

    async def call(self, factory, *, real: bool = False, label: str = ''):
        """在预算、并发与超时约束下执行一次异步调用。

        factory 为无参可调用对象，返回 coroutine。
        """
        if real and self.counters.real_requests >= self.max_real_requests:
            self.counters.rejected_by_budget += 1
            raise BudgetExhausted(
                f'真实模型请求已达上限 {self.max_real_requests}',
                used=self.counters.real_requests, limit=self.max_real_requests,
            )
        if self.counters.requests >= self.max_requests:
            self.counters.rejected_by_budget += 1
            raise BudgetExhausted(
                f'本轮请求已达上限 {self.max_requests}',
                used=self.counters.requests, limit=self.max_requests,
            )
        self.counters.requests += 1
        if real:
            self.counters.real_requests += 1
        await self._enter()
        try:
            return await asyncio.wait_for(factory(), timeout=self.timeout_s)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            self.counters.timeouts += 1
            raise RequestTimeout(f'{label or "call"} 超时 > {self.timeout_s}s') from exc
        finally:
            await self._exit()

    def snapshot(self) -> dict:
        return {
            'max_requests': self.max_requests,
            'max_real_requests': self.max_real_requests,
            'requests_used': self.counters.requests,
            'real_model_requests': self.counters.real_requests,
            'timeouts': self.counters.timeouts,
            'rejected_by_budget': self.counters.rejected_by_budget,
            'concurrency_limit': self.concurrency,
            'peak_concurrency': self.counters.peak_concurrency,
            'timeout_s': self.timeout_s,
            'exhausted': self.exhausted(),
        }
