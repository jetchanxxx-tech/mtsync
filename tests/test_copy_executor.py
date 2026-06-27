"""
跟单执行引擎单元测试 — 覆盖比例计算、精度截断、品种映射、下单间隔、错误处理。

所有测试使用 mock MT5 接口，不连接真实账户。
"""

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import pytest

from src.signal_detector import EventType, PositionSnapshot, SignalEvent
from src.copy_executor import (
    CopyConfig,
    CopyExecutor,
    CopyResult,
    OrderMappingStore,
    ResultStatus,
)


# ---- Mock helpers ----

def _make_open_event(ticket: int, symbol: str = "EURUSD",
                     direction: int = 0, volume: float = 0.10,
                     open_price: float = 1.08500,
                     sl: float = 0.0, tp: float = 0.0) -> SignalEvent:
    """构造 OPEN 信号事件"""
    return SignalEvent(
        event_type=EventType.OPEN,
        ticket=ticket,
        old_state=None,
        new_state=PositionSnapshot(
            ticket=ticket, symbol=symbol, type=direction,
            volume=volume, open_price=open_price, sl=sl, tp=tp,
        ),
    )


def _make_close_event(ticket: int, symbol: str = "EURUSD",
                      volume: float = 0.10) -> SignalEvent:
    """构造 CLOSE 信号事件"""
    return SignalEvent(
        event_type=EventType.CLOSE,
        ticket=ticket,
        old_state=PositionSnapshot(
            ticket=ticket, symbol=symbol, type=0, volume=volume,
        ),
        new_state=None,
    )


def _make_modify_event(ticket: int, symbol: str = "EURUSD",
                       old_sl: float = 1.0800, new_sl: float = 1.0820,
                       old_tp: float = 1.0900, new_tp: float = 1.0900,
                       volume: float = 0.10) -> SignalEvent:
    """构造 MODIFY 信号事件"""
    return SignalEvent(
        event_type=EventType.MODIFY,
        ticket=ticket,
        old_state=PositionSnapshot(
            ticket=ticket, symbol=symbol, type=0, volume=volume,
            sl=old_sl, tp=old_tp,
        ),
        new_state=PositionSnapshot(
            ticket=ticket, symbol=symbol, type=0, volume=volume,
            sl=new_sl, tp=new_tp,
        ),
    )


def _make_symbol_info(volume_step: float = 0.01, volume_min: float = 0.01,
                      volume_max: float = 50.0, digits: int = 5,
                      trade_mode: int = 4) -> dict:
    """构造模拟品种信息"""
    return {
        "volume_step": volume_step,
        "volume_min": volume_min,
        "volume_max": volume_max,
        "digits": digits,
        "trade_mode": trade_mode,
    }


def _successful_sender(retcode: int = 10009, order_id: int = 90001):
    """创建成功返回的 mock order_sender"""
    def sender(request: dict) -> tuple[int, Any]:
        # 返回一个 mock result 对象
        @dataclass
        class MockResult:
            retcode: int
            order: int
        return 10009, MockResult(retcode=10009, order=order_id)
    return sender


# ================================================================
# 场景 1: 跟单开仓
# ================================================================

class TestCopyOpen:
    """按比例跟单开仓"""

    def test_copy_open_1to1(self):
        """1:1 比例跟单开仓"""
        config = CopyConfig(ratio=1.0, symbol_mapping={"EURUSD": "EURUSD_"})
        store = OrderMappingStore()
        executor = CopyExecutor(config, store)

        captured_requests = []
        def sender(req):
            captured_requests.append(req)
            return 10009, type("R", (), {"retcode": 10009, "order": 90001})()

        event = _make_open_event(ticket=10001, symbol="EURUSD", volume=0.10)
        results = executor.execute([event], sender, lambda s: _make_symbol_info())

        assert len(results) == 1
        assert results[0].status == ResultStatus.SUCCESS
        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert req["symbol"] == "EURUSD_"  # 品种映射
        assert req["volume"] == 0.10
        assert req["action"] == 1  # TRADE_ACTION_DEAL
        assert req["type"] == 0   # ORDER_TYPE_BUY
        assert req["magic"] == config.magic
        # 验证映射已记录
        assert store.get(10001) is not None
        assert store.get(10001)["follower_ticket"] == 90001

    def test_copy_open_ratio_0_5(self):
        """0.5 比例跟单，手数减半"""
        config = CopyConfig(ratio=0.5)
        store = OrderMappingStore()
        executor = CopyExecutor(config, store)

        captured = []
        def sender(req):
            captured.append(req)
            return 10009, type("R", (), {"retcode": 10009, "order": 90002})()

        event = _make_open_event(ticket=20001, symbol="GBPUSD", direction=1, volume=0.20)
        executor.execute([event], sender, lambda s: _make_symbol_info())

        assert captured[0]["volume"] == 0.10

    def test_copy_open_volume_truncation(self):
        """手数精度截断 — 0.33 * 0.10 = 0.033 → trunc → 0.03"""
        config = CopyConfig(ratio=0.33)
        store = OrderMappingStore()
        executor = CopyExecutor(config, store)

        captured = []
        def sender(req):
            captured.append(req)
            return 10009, type("R", (), {"retcode": 10009, "order": 90003})()

        event = _make_open_event(ticket=30001, symbol="EURUSD", volume=0.10)
        executor.execute([event], sender, lambda s: _make_symbol_info(volume_step=0.01))

        assert captured[0]["volume"] == 0.03


class TestVolumeLimits:
    """手数上下限处理"""

    def test_below_min_lot_skipped(self):
        """计算手数 < min_lot → 跳过"""
        config = CopyConfig(ratio=0.01, min_lot=0.01)
        store = OrderMappingStore()
        executor = CopyExecutor(config, store)

        captured = []
        event = _make_open_event(ticket=40001, symbol="EURUSD", volume=0.10)
        results = executor.execute([event],
            lambda req: (captured.append(req) or (10009, None)) and (10009, type("R", (), {"retcode": 10009, "order": 0})()),
            lambda s: _make_symbol_info())

        assert len(results) == 1
        assert results[0].status == ResultStatus.SKIPPED
        assert "低于最小手数" in results[0].error

    def test_above_max_lot_capped(self):
        """计算手数 > max_lot → 截断到上限"""
        config = CopyConfig(ratio=10.0, max_lot=50.0)
        store = OrderMappingStore()
        executor = CopyExecutor(config, store)

        captured = []
        def sender(req):
            captured.append(req)
            return 10009, type("R", (), {"retcode": 10009, "order": 90005})()

        event = _make_open_event(ticket=50001, symbol="EURUSD", volume=10.0)
        executor.execute([event], sender, lambda s: _make_symbol_info())

        assert captured[0]["volume"] == 50.0


# ================================================================
# 场景 2: 品种映射
# ================================================================

class TestSymbolMapping:
    """品种名映射"""

    def test_symbol_mapped(self):
        """配置了映射 → 使用映射后的名称"""
        config = CopyConfig(symbol_mapping={"EURUSD": "EURUSD_", "GBPUSD": "GBPUSD_"})
        store = OrderMappingStore()
        executor = CopyExecutor(config, store)

        captured = []
        def sender(req):
            captured.append(req)
            return 10009, type("R", (), {"retcode": 10009, "order": 90010})()

        event = _make_open_event(ticket=10001, symbol="GBPUSD")
        executor.execute([event], sender, lambda s: _make_symbol_info())
        assert captured[0]["symbol"] == "GBPUSD_"

    def test_symbol_no_mapping_uses_original(self):
        """无映射 → 使用原品种名"""
        config = CopyConfig(symbol_mapping={})
        store = OrderMappingStore()
        executor = CopyExecutor(config, store)

        captured = []
        def sender(req):
            captured.append(req)
            return 10009, type("R", (), {"retcode": 10009, "order": 90011})()

        event = _make_open_event(ticket=10001, symbol="USDJPY")
        executor.execute([event], sender, lambda s: _make_symbol_info())
        assert captured[0]["symbol"] == "USDJPY"


# ================================================================
# 场景 3: 跟单平仓
# ================================================================

class TestCopyClose:
    """跟单平仓"""

    def test_close_with_mapping(self):
        """有映射 → 平仓 mapped ticket"""
        config = CopyConfig()
        store = OrderMappingStore()
        store.add(10001, 90001, "EURUSD_", 0.10)
        executor = CopyExecutor(config, store)

        captured = []
        def sender(req):
            captured.append(req)
            return 10009, type("R", (), {"retcode": 10009, "order": 0})()

        event = _make_close_event(ticket=10001)
        results = executor.execute([event], sender, lambda s: _make_symbol_info())

        assert len(results) == 1
        assert results[0].status == ResultStatus.SUCCESS
        assert results[0].action == "CLOSE"
        req = captured[0]
        assert req["action"] == 1  # DEAL
        assert req["type"] == 1   # CLOSE position
        assert req["position"] == 90001
        # 平仓后映射应被删除
        assert store.get(10001) is None

    def test_close_without_mapping_skipped(self):
        """无映射 → 跳过并记录错误"""
        config = CopyConfig()
        store = OrderMappingStore()
        executor = CopyExecutor(config, store)

        captured = []
        event = _make_close_event(ticket=99999)
        results = executor.execute([event],
            lambda req: (captured.append(req), (10009, None)),
            lambda s: _make_symbol_info())

        assert len(results) == 1
        assert results[0].status == ResultStatus.SKIPPED
        assert len(captured) == 0  # 未发送下单


# ================================================================
# 场景 4: 跟单修改
# ================================================================

class TestCopyModify:
    """跟单修改 SL/TP"""

    def test_modify_sl(self):
        """修改止损"""
        config = CopyConfig(copy_sl_tp=True)
        store = OrderMappingStore()
        store.add(10001, 90001, "EURUSD_", 0.10)
        executor = CopyExecutor(config, store)

        captured = []
        def sender(req):
            captured.append(req)
            return 10009, type("R", (), {"retcode": 10009, "order": 0})()

        event = _make_modify_event(ticket=10001, old_sl=1.0800, new_sl=1.0820)
        executor.execute([event], sender, lambda s: _make_symbol_info())

        req = captured[0]
        assert req["action"] == 6  # MODIFY
        assert req["position"] == 90001
        assert req["sl"] == 1.0820
        # TP 未变，仍为原值
        assert req["tp"] == 1.0900

    def test_modify_tp(self):
        """修改止盈"""
        config = CopyConfig(copy_sl_tp=True)
        store = OrderMappingStore()
        store.add(10001, 90001, "EURUSD_", 0.10)
        executor = CopyExecutor(config, store)

        captured = []
        def sender(req):
            captured.append(req)
            return 10009, type("R", (), {"retcode": 10009, "order": 0})()

        event = _make_modify_event(ticket=10001, old_sl=1.0800, new_sl=1.0800,
                                    old_tp=1.0900, new_tp=1.0920)
        executor.execute([event], sender, lambda s: _make_symbol_info())

        req = captured[0]
        assert req["sl"] == 1.0800
        assert req["tp"] == 1.0920

    def test_modify_without_mapping_skipped(self):
        """无映射的修改 → 跳过"""
        config = CopyConfig()
        store = OrderMappingStore()
        executor = CopyExecutor(config, store)

        captured = []
        event = _make_modify_event(ticket=99999)
        results = executor.execute([event],
            lambda req: (captured.append(req), (10009, None)),
            lambda s: _make_symbol_info())

        assert results[0].status == ResultStatus.SKIPPED
        assert len(captured) == 0

    def test_modify_disabled_by_config(self):
        """配置关闭 SL/TP 同步 → 跳过 MODIFY"""
        config = CopyConfig(copy_sl_tp=False)
        store = OrderMappingStore()
        store.add(10001, 90001, "EURUSD_", 0.10)
        executor = CopyExecutor(config, store)

        captured = []
        event = _make_modify_event(ticket=10001)
        results = executor.execute([event],
            lambda req: (captured.append(req), (10009, None)),
            lambda s: _make_symbol_info())

        assert results[0].status == ResultStatus.SKIPPED
        assert "未启用" in results[0].error


# ================================================================
# 场景 5: 下单间隔控制
# ================================================================

class TestOrderInterval:
    """下单间隔 ≥ 500ms"""

    def test_interval_enforced_when_too_fast(self):
        """两次下单间隔 < 500ms → 自动等待"""
        config = CopyConfig(min_order_interval_ms=500)
        store = OrderMappingStore()
        executor = CopyExecutor(config, store)

        timestamps = []
        def sender(req):
            timestamps.append(time.perf_counter())
            return 10009, type("R", (), {"retcode": 10009, "order": 90020 + len(timestamps)})()

        event1 = _make_open_event(ticket=1)
        event2 = _make_open_event(ticket=2)

        # 注意：测试中 executor._last_order_time 初始为 0，不会有间隔限制
        # 我们直接验证 _enforce_interval 逻辑
        t0 = time.perf_counter()
        executor.execute([event1, event2], sender, lambda s: _make_symbol_info())
        elapsed = time.perf_counter() - t0

        # 两次执行 + 间隔等待，总时间 ≥ 500ms
        if len(timestamps) >= 2:
            gap = timestamps[1] - timestamps[0]
            assert gap >= 0.5 - 0.05, f"两次下单间隔应为 ≥ 500ms, 实际: {gap*1000:.0f}ms"

    def test_no_wait_when_interval_sufficient(self, monkeypatch):
        """间隔足够 → 无等待"""
        config = CopyConfig(min_order_interval_ms=500)
        store = OrderMappingStore()
        executor = CopyExecutor(config, store)

        # 手动设上次下单时间为 2 秒前
        executor._last_order_time = time.perf_counter() - 2.0

        t0 = time.perf_counter()
        def sender(req):
            return 10009, type("R", (), {"retcode": 10009, "order": 90030})()

        event = _make_open_event(ticket=1)
        executor.execute([event], sender, lambda s: _make_symbol_info())
        elapsed = time.perf_counter() - t0

        # 不应有显著等待
        assert elapsed < 0.1, f"无需等待时不应延迟, 实际耗时: {elapsed:.2f}s"


# ================================================================
# 场景 6: 批量事件处理顺序
# ================================================================

class TestBatchOrdering:
    """批量事件 CLOSE → MODIFY → OPEN 排序"""

    def test_events_sorted_close_first(self):
        """验证 execute 方法将事件按 CLOSE→MODIFY→OPEN 排序"""
        config = CopyConfig()
        store = OrderMappingStore()
        # 预先添加映射以便 CLOSE 和 MODIFY 能找到
        store.add(1, 90001, "EURUSD_", 0.10)
        store.add(2, 90002, "EURUSD_", 0.10)

        executor = CopyExecutor(config, store)

        execution_order = []
        def sender(req):
            # 记录执行动作
            if "position" in req and req.get("type") != 1:
                execution_order.append(("MODIFY", req.get("position")))
            elif req.get("type") == 1 and req.get("position", 0) > 0:
                execution_order.append(("CLOSE", req.get("position")))
            else:
                execution_order.append(("OPEN", req.get("symbol")))
            return 10009, type("R", (), {"retcode": 10009, "order": 90099})()

        events = [
            _make_open_event(ticket=3, symbol="USDJPY"),       # OPEN
            _make_close_event(ticket=1),                        # CLOSE
            _make_modify_event(ticket=2),                       # MODIFY
        ]
        executor.execute(events, sender, lambda s: _make_symbol_info())

        actions = [a for a, _ in execution_order]
        assert actions[0] == "CLOSE", f"第一个应为 CLOSE, 实际: {actions[0]}"
        assert actions[1] == "MODIFY", f"第二个应为 MODIFY, 实际: {actions[1]}"
        assert actions[2] == "OPEN", f"第三个应为 OPEN, 实际: {actions[2]}"


# ================================================================
# 场景 7: 错误处理
# ================================================================

class TestErrorHandling:
    """MT5 错误处理"""

    def test_mt5_error_returns_failed(self):
        """MT5 返回错误 → CopyResult FAILED"""
        config = CopyConfig()
        store = OrderMappingStore()
        executor = CopyExecutor(config, store)

        def failing_sender(req):
            return 10016, type("R", (), {"retcode": 10016, "comment": "Invalid volume"})()

        event = _make_open_event(ticket=10001)
        results = executor.execute([event], failing_sender, lambda s: _make_symbol_info())

        assert results[0].status == ResultStatus.FAILED
        assert "10016" in results[0].error

    def test_partial_failure_continues(self):
        """部分失败不影响后续执行"""
        config = CopyConfig()
        store = OrderMappingStore()
        store.add(1, 90001, "EURUSD_", 0.10)
        executor = CopyExecutor(config, store)

        call_count = [0]
        def mixed_sender(req):
            call_count[0] += 1
            if call_count[0] == 2:
                # 第 2 个请求失败
                return 10016, type("R", (), {"retcode": 10016, "comment": "Error"})()

            return 10009, type("R", (), {"retcode": 10009, "order": 90900 + call_count[0]})()

        events = [
            _make_open_event(ticket=1),      # 成功
            _make_open_event(ticket=2),      # 失败
            _make_open_event(ticket=3),      # 成功
        ]
        results = executor.execute(events, mixed_sender, lambda s: _make_symbol_info())

        assert len(results) == 3
        assert results[0].status == ResultStatus.SUCCESS
        assert results[1].status == ResultStatus.FAILED
        assert results[2].status == ResultStatus.SUCCESS


# ================================================================
# 场景 8: 方向过滤
# ================================================================

class TestDirectionFilter:
    """方向过滤"""

    def test_long_only_skips_sell(self):
        """long_only → 过滤 SELL 信号"""
        config = CopyConfig(copy_direction="long_only")
        store = OrderMappingStore()
        executor = CopyExecutor(config, store)

        captured = []
        event = _make_open_event(ticket=10001, direction=1, volume=0.10)  # SELL
        results = executor.execute([event],
            lambda req: (captured.append(req), (10009, None)),
            lambda s: _make_symbol_info())

        assert results[0].status == ResultStatus.SKIPPED
        assert len(captured) == 0

    def test_short_only_skips_buy(self):
        """short_only → 过滤 BUY 信号"""
        config = CopyConfig(copy_direction="short_only")
        store = OrderMappingStore()
        executor = CopyExecutor(config, store)

        captured = []
        event = _make_open_event(ticket=10001, direction=0, volume=0.10)  # BUY
        results = executor.execute([event],
            lambda req: (captured.append(req), (10009, None)),
            lambda s: _make_symbol_info())

        assert results[0].status == ResultStatus.SKIPPED

    def test_both_copies_all(self):
        """both → 全部通过"""
        config = CopyConfig(copy_direction="both")
        store = OrderMappingStore()
        executor = CopyExecutor(config, store)

        captured = []
        def sender(req):
            captured.append(req)
            return 10009, type("R", (), {"retcode": 10009, "order": 90040 + len(captured)})()

        events = [
            _make_open_event(ticket=1, direction=0),  # BUY
            _make_open_event(ticket=2, direction=1),  # SELL
        ]
        results = executor.execute(events, sender, lambda s: _make_symbol_info())

        assert all(r.status == ResultStatus.SUCCESS for r in results)


# ================================================================
# OrderMappingStore 测试
# ================================================================

class TestOrderMappingStore:
    """订单映射存储"""

    def test_add_and_get(self):
        store = OrderMappingStore()
        store.add(master_ticket=10001, follower_ticket=90001,
                  symbol="EURUSD_", volume=0.10)
        m = store.get(10001)
        assert m["master_ticket"] == 10001
        assert m["follower_ticket"] == 90001
        assert m["symbol"] == "EURUSD_"
        assert m["volume"] == 0.10

    def test_remove(self):
        store = OrderMappingStore()
        store.add(10001, 90001, "EURUSD_", 0.10)
        store.remove(10001)
        assert store.get(10001) is None

    def test_get_all(self):
        store = OrderMappingStore()
        store.add(1, 11, "EURUSD_", 0.10)
        store.add(2, 22, "GBPUSD_", 0.05)
        all_mappings = store.get_all()
        assert len(all_mappings) == 2

    def test_duplicate_add_overwrites(self):
        store = OrderMappingStore()
        store.add(1, 11, "EURUSD_", 0.10)
        store.add(1, 99, "EURUSD_", 0.20)
        assert store.get(1)["follower_ticket"] == 99
        assert store.get(1)["volume"] == 0.20

    def test_get_nonexistent(self):
        store = OrderMappingStore()
        assert store.get(99999) is None

    def test_clear(self):
        store = OrderMappingStore()
        store.add(1, 11, "EURUSD_", 0.10)
        store.clear()
        assert len(store.get_all()) == 0
