from __future__ import annotations

"""W2 并发执行层：单进程有界容量闸门 + 线程池 + 同幂等键 single-flight 去重。

全部标准库（R6）。这是与 W1 串行基准**互不相交**的第二条路径：
- 不产出 off/on 对照数字，不与归档相减或拼曲线；
- 只证明「并发下计数不丢、状态不坏、背压生效」；
- 墙钟耗时不进任何归档字段。

W1 的 GatewayEngine.handle() 一行未改；并发安全来自各组件内部的 RLock，
上游调用发生在锁外，因此 I/O 是真并行，不是一把大锁把并发度压成 1。

实现注记：worker **不是常驻循环**。每个请求作为 ThreadPoolExecutor 的一个短任务
提交，容量由 BoundedTaskQueue 的信号量闸门限制「在途请求数」。常驻循环会一直占住
executor 线程，解释器退出时 _python_exit 投进内部队列的关闭哨兵无人消费，join 会
永久阻塞——这是 W2 实现中实测并修正过的一个真实缺陷。
"""

from concurrent.futures import Future, ThreadPoolExecutor
import atexit
import threading
import time

from .core import ChatRequest, Decision
from .engine import GatewayEngine


class BoundedTaskQueue:
    """有界容量闸门：限制「处理中 + 等待调度」的在途请求数，满则立即拒绝。

    真正的排队由 ThreadPoolExecutor 的内部队列承担；这里只做背压与计数，
    避免常驻 worker 循环导致解释器退出时无法收尾。
    """

    def __init__(self, capacity: int):
        self.capacity = max(1, capacity)
        self._slots = threading.BoundedSemaphore(self.capacity)
        self._lock = threading.RLock()
        self.submitted = 0
        self.rejected = 0
        self._inflight = 0

    def try_put(self, item=None) -> bool:
        if not self._slots.acquire(blocking=False):
            with self._lock:
                self.rejected += 1
            return False
        with self._lock:
            self.submitted += 1
            self._inflight += 1
        return True

    def task_done(self) -> None:
        self._slots.release()
        with self._lock:
            self._inflight -= 1

    def empty(self) -> bool:
        with self._lock:
            return self._inflight == 0

    def __len__(self) -> int:
        with self._lock:
            return self._inflight


class SingleFlight:
    """同一 idempotency_key 的并发请求只放行一个 leader 到引擎链路。

    waiter 等 leader 完成后重走 engine.handle，由幂等表拿到首次结果（不重复计费）。
    没有 key 的请求互不阻塞。
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._inflight: dict[str, threading.Event] = {}
        self.calls = 0
        self.waited = 0

    def acquire(self, key: str) -> tuple[bool, threading.Event | None]:
        with self._lock:
            if not key:
                self.calls += 1
                return True, None
            event = self._inflight.get(key)
            if event is None:
                self._inflight[key] = threading.Event()
                self.calls += 1
                return True, None
            self.waited += 1
            return False, event

    def release(self, key: str) -> None:
        if not key:
            return
        with self._lock:
            event = self._inflight.pop(key, None)
            if event is not None:
                event.set()


class ConcurrentGateway:
    """把请求提交到有界容量闸门，由固定大小线程池并发执行。

    submit 返回 Future；容量满时返回已完成的 Future，其 Decision 为 429
    且 reasons=("queue_full",)。被拒绝的请求**不进台账**（它不是已完成的请求）。
    """

    def __init__(
        self,
        engine: GatewayEngine,
        workers: int = 4,
        queue_capacity: int = 64,
        wait_timeout_s: float = 5.0,
    ):
        self.engine = engine
        self.workers = max(1, workers)
        self.queue = BoundedTaskQueue(queue_capacity)
        self.single_flight = SingleFlight()
        self.wait_timeout_s = max(0.01, wait_timeout_s)
        self._lock = threading.RLock()
        self._start_lock = threading.Lock()
        self.completed = 0
        self._stop = threading.Event()
        # 懒启动：import gateway.app 不会产生线程副作用。
        self._executor: ThreadPoolExecutor | None = None
        atexit.register(self.shutdown)

    def _ensure_started(self) -> None:
        with self._start_lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self.workers, thread_name_prefix="gw-worker"
                )

    def _rejected_decision(self, request: ChatRequest, reason: str = "queue_full") -> Decision:
        return Decision(
            status=429, reply=None, index=request.index, caller=request.caller,
            model=request.model, route="", simulated_latency_ms=0.0,
            reasons=(reason,),
        )

    def submit(self, request: ChatRequest) -> Future:
        if self._stop.is_set():
            future: Future = Future()
            future.set_result(self._rejected_decision(request, "gateway_shutdown"))
            return future
        if not self.queue.try_put():
            future = Future()
            future.set_result(self._rejected_decision(request))
            return future
        self._ensure_started()
        assert self._executor is not None
        return self._executor.submit(self._process_and_release, request)

    def submit_many(self, requests) -> list[Future]:
        return [self.submit(r) for r in requests]

    def _process(self, request: ChatRequest) -> Decision:
        should_call, event = self.single_flight.acquire(request.idempotency_key)
        if not should_call:
            assert event is not None
            event.wait(timeout=self.wait_timeout_s)
            return self.engine.handle(request)
        try:
            return self.engine.handle(request)
        finally:
            self.single_flight.release(request.idempotency_key)

    def _process_and_release(self, request: ChatRequest) -> Decision:
        try:
            return self._process(request)
        finally:
            self.queue.task_done()
            with self._lock:
                self.completed += 1

    def collect(self, futures, timeout_s: float = 10.0) -> list[Decision]:
        """等待全部 Future，按 request.index 排序后返回（仅为稳定输出顺序）。"""
        decisions = []
        for future in futures:
            decisions.append(future.result(timeout=timeout_s))
        return sorted(decisions, key=lambda d: d.index)

    def drain(self, timeout_s: float = 10.0) -> None:
        """等待所有在途请求落地（不依赖墙钟作为指标，只用于测试/收尾同步）。"""
        deadline = time.monotonic() + max(0.0, timeout_s)
        while not self.queue.empty() and time.monotonic() < deadline:
            time.sleep(0.005)

    def stats(self) -> dict:
        with self._lock:
            return {
                "workers": self.workers,
                "queue_capacity": self.queue.capacity,
                "queue_submitted": self.queue.submitted,
                "queue_rejected": self.queue.rejected,
                "completed": self.completed,
                "single_flight_calls": self.single_flight.calls,
                "single_flight_waited": self.single_flight.waited,
            }

    def shutdown(self, wait: bool = True) -> None:
        """幂等。atexit、lifespan、with 三条路径都可能调用。"""
        self._stop.set()
        with self._start_lock:
            executor = self._executor
        if executor is not None:
            executor.shutdown(wait=wait)

    def __enter__(self) -> ConcurrentGateway:
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown(wait=True)
