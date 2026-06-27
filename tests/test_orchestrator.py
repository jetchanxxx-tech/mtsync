"""
跟单主调度器单元测试 — 覆盖事件循环、风控集成、对账调度、停恢复。

使用 mock 组件隔离测试调度逻辑。
"""

import time
from unittest.mock import MagicMock, patch, call

import pytest

from src.signal_detector import EventType, PositionSnapshot, SignalEvent
from src.copy_executor import (
    CopyConfig, CopyExecutor, CopyResult, OrderMappingStore, ResultStatus
)
from src.risk_manager import RiskManager, RiskConfig, RiskCheckResult, RiskStatus
from src.reconciler import Reconciler, ReconcileConfig
from src.orchestrator import Orchestrator, OrchestratorState, Stats


def _mock_order_sender(request: dict) -> tuple:
    """Mock 下单回调"""
    mock_result = MagicMock()
    mock_result.retcode = 10009
    mock_result.order = 90001
    return 10009, mock_result


@pytest.fixture
def mock_components():
    """构建 mock 组件"""
    detector = MagicMock()
    detector.detect.return_value = []
    detector._previous_snapshot = {}

    executor = MagicMock()
    executor.execute.return_value = []
    executor.config = MagicMock()
    executor.config.ratio = 1.0
    executor.store = MagicMock()
    executor.store.get_all_as_dict.return_value = {}

    risk = MagicMock()
    risk.check_all.return_value = RiskCheckResult(status=RiskStatus.PASSED, reason="")

    reconciler = MagicMock()
    reconciler.reconcile.return_value = []

    return detector, executor, risk, reconciler


@pytest.fixture
def orchestrator(mock_components):
    detector, executor, risk, reconciler = mock_components
    orch = Orchestrator(
        signal_detector=detector,
        copy_executor=executor,
        risk_manager=risk,
        reconciler=reconciler,
        order_sender=_mock_order_sender,
        poll_interval_ms=100,
        reconcile_interval_s=300,
    )
    return orch


# ---- 场景 1: 正常跟单循环 ----

class TestNormalLoop:
    def test_open_signal_passes_risk_executes(self, orchestrator, mock_components):
        detector, executor, risk, _ = mock_components
        event = SignalEvent(
            event_type=EventType.OPEN, ticket=10001,
            new_state=PositionSnapshot(ticket=10001, symbol="EURUSD", type=0, volume=0.10),
        )
        detector.detect.return_value = [event]
        executor.execute.return_value = [
            CopyResult(status=ResultStatus.SUCCESS, master_ticket=10001, follower_ticket=90001, action="OPEN")
        ]

        results = orchestrator._process_cycle(lambda: {}, lambda s: {"volume_step": 0.01})

        assert len(results) == 1
        assert results[0].status == ResultStatus.SUCCESS
        risk.check_all.assert_called_once()
        executor.execute.assert_called_once()

    def test_close_signal_executes(self, orchestrator, mock_components):
        detector, executor, risk, _ = mock_components
        event = SignalEvent(
            event_type=EventType.CLOSE, ticket=10001,
            old_state=PositionSnapshot(ticket=10001, symbol="EURUSD", type=0, volume=0.10),
        )
        detector.detect.return_value = [event]
        executor.execute.return_value = [
            CopyResult(status=ResultStatus.SUCCESS, master_ticket=10001, follower_ticket=90001, action="CLOSE")
        ]

        results = orchestrator._process_cycle(lambda: {}, lambda s: {"volume_step": 0.01})
        assert len(results) == 1
        assert results[0].action == "CLOSE"

    def test_no_signals_no_execution(self, orchestrator, mock_components):
        detector, executor, risk, _ = mock_components
        detector.detect.return_value = []
        results = orchestrator._process_cycle(lambda: {}, lambda s: {})
        assert len(results) == 0
        executor.execute.assert_not_called()


# ---- 场景 2: 风控拦截 ----

class TestRiskBlock:
    def test_risk_rejects_open_skips_execution(self, orchestrator, mock_components):
        detector, executor, risk, _ = mock_components
        event = SignalEvent(
            event_type=EventType.OPEN, ticket=10001,
            new_state=PositionSnapshot(ticket=10001, symbol="EURUSD", type=0, volume=0.10),
        )
        detector.detect.return_value = [event]
        risk.check_all.return_value = RiskCheckResult(
            status=RiskStatus.REJECTED, reason="总仓位超限"
        )

        results = orchestrator._process_cycle(lambda: {}, lambda s: {"volume_step": 0.01})
        assert len(results) == 1
        assert results[0].status == ResultStatus.SKIPPED
        executor.execute.assert_not_called()

    def test_risk_skips_open_only_others_continue(self, orchestrator, mock_components):
        """风控拒绝开仓但 CLOSE 不受影响"""
        detector, executor, risk, _ = mock_components
        open_evt = SignalEvent(
            event_type=EventType.OPEN, ticket=10002,
            new_state=PositionSnapshot(ticket=10002, symbol="EURUSD", type=0, volume=0.10),
        )
        close_evt = SignalEvent(
            event_type=EventType.CLOSE, ticket=10001,
            old_state=PositionSnapshot(ticket=10001, symbol="EURUSD", type=0, volume=0.10),
        )
        detector.detect.return_value = [open_evt, close_evt]

        # 风控拒绝所有开仓
        risk.check_all.return_value = RiskCheckResult(
            status=RiskStatus.REJECTED, reason="总仓位超限"
        )
        # CLOSE 不受风控限制，executor 正常执行
        executor.execute.return_value = [
            CopyResult(status=ResultStatus.SUCCESS, master_ticket=10001, action="CLOSE"),
        ]

        results = orchestrator._process_cycle(lambda: {}, lambda s: {"volume_step": 0.01})
        assert len(results) == 2
        assert results[0].status == ResultStatus.SUCCESS  # CLOSE 成功
        assert results[1].status == ResultStatus.SKIPPED   # OPEN 被风控拒绝


# ---- 场景 3: 对账调度 ----

class TestReconcileScheduling:
    def test_reconcile_triggered_when_interval_elapsed(self, orchestrator, mock_components):
        _, _, _, reconciler = mock_components
        orchestrator._last_reconcile_time = time.time() - 400
        orchestrator._check_reconcile(lambda: [])
        assert orchestrator._reconcile_count >= 1

    def test_reconcile_not_triggered_when_not_elapsed(self, orchestrator, mock_components):
        _, _, _, reconciler = mock_components
        orchestrator._last_reconcile_time = time.time()
        call_count_before = orchestrator._reconcile_count
        orchestrator._check_reconcile(lambda: [])
        assert orchestrator._reconcile_count == call_count_before


# ---- 场景 4: 停止与恢复 ----

class TestStopResume:
    def test_stop_stops_loop(self, orchestrator):
        orchestrator.stop()
        assert orchestrator.state == OrchestratorState.STOPPED

    def test_resume_after_stop(self, orchestrator):
        orchestrator.stop()
        orchestrator.resume()
        assert orchestrator.state == OrchestratorState.RUNNING

    def test_emergency_stop_discards_pending(self, orchestrator, mock_components):
        detector, _, _, _ = mock_components
        detector.detect.return_value = [
            SignalEvent(event_type=EventType.OPEN, ticket=1,
                        new_state=PositionSnapshot(ticket=1, symbol="EURUSD")),
        ]
        orchestrator.emergency_stop()
        assert orchestrator.state == OrchestratorState.EMERGENCY_STOPPED

    def test_stop_then_resume_preserves_state(self, orchestrator):
        orchestrator.stop()
        stats_before = orchestrator.stats
        orchestrator.resume()
        # 统计不变
        assert orchestrator.stats == stats_before


# ---- 场景 5: 统计追踪 ----

class TestStats:
    def test_stats_initial_state(self, orchestrator):
        s = orchestrator.stats
        assert s.total_cycles == 0
        assert s.success_count == 0
        assert s.failed_count == 0
        assert s.skipped_count == 0

    def test_stats_updated_after_cycle(self, orchestrator, mock_components):
        detector, executor, risk, _ = mock_components
        open_evt = SignalEvent(
            event_type=EventType.OPEN, ticket=10001,
            new_state=PositionSnapshot(ticket=10001, symbol="EURUSD", type=0, volume=0.10),
        )
        detector.detect.return_value = [open_evt]
        executor.execute.return_value = [
            CopyResult(status=ResultStatus.SUCCESS, master_ticket=10001, action="OPEN")
        ]

        orchestrator._process_cycle(lambda: {}, lambda s: {"volume_step": 0.01})
        assert orchestrator.stats.total_cycles == 1
        assert orchestrator.stats.success_count == 1

    def test_stats_mixed_results(self, orchestrator, mock_components):
        detector, executor, risk, _ = mock_components
        events = [
            SignalEvent(event_type=EventType.OPEN, ticket=1,
                        new_state=PositionSnapshot(ticket=1, symbol="EURUSD")),
            SignalEvent(event_type=EventType.OPEN, ticket=2,
                        new_state=PositionSnapshot(ticket=2, symbol="GBPUSD")),
        ]
        detector.detect.return_value = events
        executor.execute.return_value = [
            CopyResult(status=ResultStatus.SUCCESS, master_ticket=1, action="OPEN"),
            CopyResult(status=ResultStatus.FAILED, master_ticket=2, action="OPEN", error="错误"),
        ]

        orchestrator._process_cycle(lambda: {}, lambda s: {"volume_step": 0.01})
        assert orchestrator.stats.success_count == 1
        assert orchestrator.stats.failed_count == 1


# ---- 场景 6: 状态查询 ----

class TestStatusQuery:
    def test_get_status(self, orchestrator):
        status = orchestrator.get_status()
        assert status["state"] == "stopped"
        assert "stats" in status
        assert "last_reconcile" in status
        assert "poll_interval_ms" in status

    def test_get_status_after_start(self, orchestrator):
        orchestrator.state = OrchestratorState.RUNNING
        status = orchestrator.get_status()
        assert status["state"] == "running"


# ---- 场景 7: 异常容错 ----

class TestFaultTolerance:
    def test_detector_exception_does_not_crash(self, orchestrator, mock_components):
        detector, _, _, _ = mock_components
        detector.detect.side_effect = Exception("MT5 连接异常")
        # 不应抛出异常
        results = orchestrator._process_cycle(lambda: {}, lambda s: {})
        assert len(results) == 0
        assert orchestrator.stats.total_cycles == 1

    def test_executor_exception_does_not_crash(self, orchestrator, mock_components):
        detector, executor, _, _ = mock_components
        event = SignalEvent(
            event_type=EventType.OPEN, ticket=10001,
            new_state=PositionSnapshot(ticket=10001, symbol="EURUSD", type=0, volume=0.10),
        )
        detector.detect.return_value = [event]
        executor.execute.side_effect = Exception("下单失败")

        results = orchestrator._process_cycle(lambda: {}, lambda s: {"volume_step": 0.01})
        assert len(results) == 1
        assert results[0].status == ResultStatus.FAILED
