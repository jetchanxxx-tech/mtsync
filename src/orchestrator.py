"""
跟单主调度器 — 串联信号检测、风控、执行、对账的事件循环。

核心循环:
  1. 信号检测器轮询主账户持仓变化
  2. 对每个 OPEN 信号，先过风控再调用执行器
  3. CLOSE/MODIFY 信号直接执行（不受开仓风控限制）
  4. 定期触发对账

使用方式:
    orch = Orchestrator(
        signal_detector=detector,
        copy_executor=executor,
        risk_manager=risk,
        reconciler=reconciler,
    )
    # 单次循环
    results = orch._process_cycle(get_lead_positions, get_symbol_info)
    # 或启动事件循环:
    await orch.run_loop(get_lead_positions, get_symbol_info, get_follower_positions)
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable

from src.signal_detector import EventType, PositionSnapshot, SignalDetector, SignalEvent
from src.copy_executor import (
    CopyConfig, CopyExecutor, CopyResult, OrderMappingStore, ResultStatus,
)
from src.risk_manager import RiskManager, RiskCheckResult, RiskStatus
from src.reconciler import Reconciler, ReconcileResult

logger = logging.getLogger(__name__)


class OrchestratorState(Enum):
    STOPPED = auto()
    RUNNING = auto()
    EMERGENCY_STOPPED = auto()


@dataclass
class Stats:
    """调度器统计"""
    total_cycles: int = 0
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    last_cycle_time: float = 0.0
    last_reconcile_time: float = 0.0


class Orchestrator:
    """
    跟单主调度器。

    负责：
    - 串联信号检测 → 风控 → 执行
    - 定期触发对账
    - 状态管理与统计
    """

    def __init__(
        self,
        signal_detector: SignalDetector,
        copy_executor: CopyExecutor,
        risk_manager: RiskManager,
        reconciler: Reconciler,
        order_sender: Callable[[dict], tuple[int, Any]] | None = None,
        poll_interval_ms: int = 300,
        reconcile_interval_s: int = 300,
    ):
        self.detector = signal_detector
        self.executor = copy_executor
        self.risk = risk_manager
        self.reconciler = reconciler
        self.order_sender = order_sender
        self.poll_interval_ms = poll_interval_ms
        self.reconcile_interval_s = reconcile_interval_s

        self.state: OrchestratorState = OrchestratorState.STOPPED
        self.stats = Stats()
        self._last_reconcile_time: float = 0.0
        self._reconcile_count: int = 0

    # ---- 状态管理 ----

    def stop(self) -> None:
        self.state = OrchestratorState.STOPPED
        logger.info("调度器已停止")

    def resume(self) -> None:
        self.state = OrchestratorState.RUNNING
        logger.info("调度器已恢复")

    def emergency_stop(self) -> None:
        self.state = OrchestratorState.EMERGENCY_STOPPED
        logger.warning("⚠️ 跟单已紧急停止！所有未执行订单已丢弃")

    def get_status(self) -> dict:
        return {
            "state": self.state.name.lower(),
            "stats": {
                "total_cycles": self.stats.total_cycles,
                "success_count": self.stats.success_count,
                "failed_count": self.stats.failed_count,
                "skipped_count": self.stats.skipped_count,
            },
            "last_reconcile": time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(self.stats.last_reconcile_time)
            ) if self.stats.last_reconcile_time > 0 else "never",
            "poll_interval_ms": self.poll_interval_ms,
            "reconcile_interval_s": self.reconcile_interval_s,
        }

    # ---- 单次处理循环 ----

    def _process_cycle(
        self,
        get_lead_positions: Callable[[], dict[int, PositionSnapshot]],
        get_symbol_info: Callable[[str], Any],
    ) -> list[CopyResult]:
        """
        执行一次完整的信号检测 → 执行循环。

        Args:
            get_lead_positions: 获取主账户当前持仓的函数
            get_symbol_info: 获取品种信息的函数

        Returns:
            本周期所有跟单结果
        """
        if self.state == OrchestratorState.EMERGENCY_STOPPED:
            return []

        cycle_start = time.perf_counter()
        self.stats.total_cycles += 1
        all_results: list[CopyResult] = []

        # 1. 检测信号
        try:
            current_positions = get_lead_positions()
            events = self.detector.detect(current_positions)
        except Exception as e:
            logger.error(f"信号检测异常: {e}", exc_info=True)
            return []

        if not events:
            return []

        # 2. 按事件类型分组处理
        open_events = [e for e in events if e.event_type == EventType.OPEN]
        non_open_events = [e for e in events if e.event_type != EventType.OPEN]

        # CLOSE 和 MODIFY 直接执行（不受开仓风控限制）
        if non_open_events:
            non_open_results = self._execute_events(non_open_events, get_symbol_info)
            all_results.extend(non_open_results)

        # OPEN 需要过风控
        approved_opens = []
        for event in open_events:
            new_pos = event.new_state
            risk_result = self.risk.check_all(
                current_positions=len(current_positions),
                symbol=new_pos.symbol,
                new_volume=new_pos.volume,
            )
            if risk_result.status == RiskStatus.REJECTED:
                logger.info(f"风控拒绝 ticket={event.ticket}: {risk_result.reason}")
                all_results.append(CopyResult(
                    status=ResultStatus.SKIPPED,
                    master_ticket=event.ticket,
                    action="OPEN",
                    error=f"风控拒绝: {risk_result.reason}",
                ))
                self.stats.skipped_count += 1
            else:
                approved_opens.append(event)

        # 执行通过风控的开仓信号
        if approved_opens:
            open_results = self._execute_events(approved_opens, get_symbol_info)
            all_results.extend(open_results)

        # 更新统计
        for r in all_results:
            if r.status == ResultStatus.SUCCESS:
                self.stats.success_count += 1
            elif r.status == ResultStatus.FAILED:
                self.stats.failed_count += 1
            elif r.status == ResultStatus.SKIPPED:
                self.stats.skipped_count += 1

        self.stats.last_cycle_time = time.perf_counter() - cycle_start
        return all_results

    # ---- 对账调度 ----

    def _check_reconcile(
        self,
        get_follower_positions: Callable[[], list[Any]],
    ) -> list | None:
        """检查是否需要触发对账，如果是则执行"""
        now = time.time()
        if self._last_reconcile_time > 0:
            elapsed = now - self._last_reconcile_time
            if elapsed < self.reconcile_interval_s:
                return None

        logger.info("触发定期对账...")
        try:
            # 获取主账户持仓（通过 detector 的内部快照）
            lead = self.detector._previous_snapshot
            follower = get_follower_positions()
            mapping = self.executor.store.get_all_as_dict()

            results = self.reconciler.reconcile(
                lead, follower, mapping, self.executor.config.ratio,
            )
            self._last_reconcile_time = now
            self._reconcile_count += 1
            self.stats.last_reconcile_time = now
            logger.info(f"对账完成: {len(results)} 条差异")
            return results
        except Exception as e:
            logger.error(f"对账异常: {e}", exc_info=True)
            return None

    # ---- 内部方法 ----

    def _execute_events(
        self,
        events: list[SignalEvent],
        get_symbol_info: Callable[[str], Any],
    ) -> list[CopyResult]:
        """执行信号事件列表"""
        if self.order_sender is None:
            raise RuntimeError("order_sender 未设置，无法执行下单")
        try:
            return self.executor.execute(
                events,
                order_sender=self.order_sender,
                symbol_info_provider=get_symbol_info,
            )
        except Exception as e:
            logger.error(f"执行信号异常: {e}", exc_info=True)
            return [
                CopyResult(
                    status=ResultStatus.FAILED,
                    master_ticket=evt.ticket,
                    action=evt.event_type.name,
                    error=str(e),
                )
                for evt in events
            ]
