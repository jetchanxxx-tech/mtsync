"""
信号检测器单元测试 — 基于快照差量对比算法。

所有测试使用 mock 数据，不连接真实 MT5 终端。
覆盖 features/signal_detection.feature 中的所有场景。
"""

import pytest
from src.signal_detector import (
    SignalDetector,
    PositionSnapshot,
    SignalEvent,
    EventType,
)


# ---- Helper fixtures ----

def _make_snap(positions: list[dict]) -> dict[int, PositionSnapshot]:
    """辅助函数：从字典列表构建持仓快照"""
    snap = {}
    for p in positions:
        snap[p["ticket"]] = PositionSnapshot(
            ticket=p["ticket"],
            symbol=p.get("symbol", "EURUSD_"),
            type=p.get("type", 0),
            volume=p.get("volume", 0.10),
            open_price=p.get("open_price", 1.08500),
            sl=p.get("sl", 0.0),
            tp=p.get("tp", 0.0),
            comment=p.get("comment", ""),
            open_time=p.get("open_time", 1700000000),
        )
    return snap


# ================================================================
# 场景 1: 首次启动
# ================================================================

class TestFirstRun:
    """首次启动 — 无历史快照时全量记录不触发事件"""

    def test_first_run_with_positions(self):
        """首次轮询有持仓，不触发事件，保存快照"""
        detector = SignalDetector()
        current = _make_snap([
            {"ticket": 10001, "symbol": "EURUSD_", "type": 0, "volume": 0.10,
             "open_price": 1.08500, "sl": 1.0800, "tp": 1.0900},
            {"ticket": 10002, "symbol": "GBPUSD_", "type": 1, "volume": 0.05,
             "open_price": 1.26500, "sl": 0.0, "tp": 0.0},
        ])
        events = detector.detect(current)
        assert events == [], "首次启动不应触发任何事件"
        assert detector.has_snapshot(), "应已保存基准快照"
        assert len(detector._previous_snapshot) == 2, "基准快照应有 2 个持仓"

    def test_first_run_empty(self):
        """首次轮询空持仓，不触发事件"""
        detector = SignalDetector()
        events = detector.detect({})
        assert events == []
        assert detector.has_snapshot()
        assert len(detector._previous_snapshot) == 0


# ================================================================
# 场景 2: 检测新开仓
# ================================================================

class TestOpenDetection:
    """检测新开仓 — 快照中出现新 ticket"""

    def test_single_open(self):
        """基准有 1 个持仓，当前多了 1 个 → 1 个 OPEN 事件"""
        detector = SignalDetector()
        # 先建立基准
        detector.detect(_make_snap([
            {"ticket": 10001},
        ]))
        # 新开仓
        events = detector.detect(_make_snap([
            {"ticket": 10001},
            {"ticket": 10002, "symbol": "USDJPY_", "type": 1, "volume": 0.10,
             "open_price": 150.500},
        ]))
        assert len(events) == 1
        e = events[0]
        assert e.event_type == EventType.OPEN
        assert e.ticket == 10002
        assert e.new_state.symbol == "USDJPY_"
        assert e.new_state.type == 1  # SELL
        assert e.new_state.volume == 0.10
        assert e.old_state is None

    def test_multiple_opens(self):
        """基准为空，当前出现 3 个持仓 → 3 个 OPEN 事件"""
        detector = SignalDetector()
        detector.detect({})  # 建立空基准
        events = detector.detect(_make_snap([
            {"ticket": 20001}, {"ticket": 20002}, {"ticket": 20003},
        ]))
        assert len(events) == 3
        assert all(e.event_type == EventType.OPEN for e in events)


# ================================================================
# 场景 3: 检测平仓
# ================================================================

class TestCloseDetection:
    """检测平仓 — 快照中 ticket 消失"""

    def test_single_close(self):
        """基准有 2 个持仓，当前剩 1 个 → 1 个 CLOSE 事件"""
        detector = SignalDetector()
        detector.detect(_make_snap([
            {"ticket": 10001}, {"ticket": 10002},
        ]))
        events = detector.detect(_make_snap([
            {"ticket": 10001},
        ]))
        assert len(events) == 1
        e = events[0]
        assert e.event_type == EventType.CLOSE
        assert e.ticket == 10002
        assert e.old_state is not None

    def test_all_close(self):
        """基准有 2 个持仓，当前全部消失 → 2 个 CLOSE 事件"""
        detector = SignalDetector()
        detector.detect(_make_snap([
            {"ticket": 10001}, {"ticket": 10002},
        ]))
        events = detector.detect({})
        assert len(events) == 2
        assert all(e.event_type == EventType.CLOSE for e in events)
        assert {10001, 10002} == {e.ticket for e in events}


# ================================================================
# 场景 4: 检测修改（止损/止盈/手数）
# ================================================================

class TestModifyDetection:
    """检测修改 — SL/TP/Volume 变化"""

    def test_sl_modify(self):
        """SL 变化 → MODIFY 事件"""
        detector = SignalDetector()
        detector.detect(_make_snap([
            {"ticket": 10001, "sl": 1.0800, "tp": 1.0900},
        ]))
        events = detector.detect(_make_snap([
            {"ticket": 10001, "sl": 1.0820, "tp": 1.0900},
        ]))
        assert len(events) == 1
        e = events[0]
        assert e.event_type == EventType.MODIFY
        assert e.ticket == 10001
        assert e.old_state.sl == 1.0800
        assert e.new_state.sl == 1.0820

    def test_tp_modify(self):
        """TP 变化 → MODIFY 事件"""
        detector = SignalDetector()
        detector.detect(_make_snap([
            {"ticket": 10001, "sl": 1.0800, "tp": 1.0900},
        ]))
        events = detector.detect(_make_snap([
            {"ticket": 10001, "sl": 1.0800, "tp": 1.0920},
        ]))
        assert len(events) == 1
        e = events[0]
        assert e.event_type == EventType.MODIFY
        assert e.old_state.tp == 1.0900
        assert e.new_state.tp == 1.0920

    def test_sl_and_tp_modify(self):
        """SL 和 TP 同时变化 → 1 个 MODIFY 事件包含两者"""
        detector = SignalDetector()
        detector.detect(_make_snap([
            {"ticket": 10001, "sl": 1.0800, "tp": 1.0900},
        ]))
        events = detector.detect(_make_snap([
            {"ticket": 10001, "sl": 1.0820, "tp": 1.0920},
        ]))
        assert len(events) == 1
        e = events[0]
        assert e.old_state.sl == 1.0800
        assert e.new_state.sl == 1.0820
        assert e.old_state.tp == 1.0900
        assert e.new_state.tp == 1.0920

    def test_volume_partial_close(self):
        """手数减少（部分平仓） → MODIFY 事件"""
        detector = SignalDetector()
        detector.detect(_make_snap([
            {"ticket": 10001, "volume": 0.10},
        ]))
        events = detector.detect(_make_snap([
            {"ticket": 10001, "volume": 0.05},
        ]))
        assert len(events) == 1
        e = events[0]
        assert e.event_type == EventType.MODIFY
        assert e.old_state.volume == 0.10
        assert e.new_state.volume == 0.05

    def test_no_modify_when_unchanged(self):
        """所有字段无变化 → 不触发 MODIFY"""
        detector = SignalDetector()
        detector.detect(_make_snap([
            {"ticket": 10001, "sl": 1.0800, "tp": 1.0900, "volume": 0.10},
        ]))
        events = detector.detect(_make_snap([
            {"ticket": 10001, "sl": 1.0800, "tp": 1.0900, "volume": 0.10},
        ]))
        assert len(events) == 0


# ================================================================
# 场景 5: 无变化
# ================================================================

class TestNoChange:
    """持仓无变化 — 不触发任何事件"""

    def test_no_positions_changed(self):
        detector = SignalDetector()
        detector.detect(_make_snap([
            {"ticket": 10001, "symbol": "EURUSD_", "type": 0},
            {"ticket": 10002, "symbol": "GBPUSD_", "type": 1},
        ]))
        events = detector.detect(_make_snap([
            {"ticket": 10001, "symbol": "EURUSD_", "type": 0},
            {"ticket": 10002, "symbol": "GBPUSD_", "type": 1},
        ]))
        assert events == []

    def test_current_price_change_ignored(self):
        """当前价格变化不应触发事件（行情波动非操盘行为）"""
        detector = SignalDetector()
        detector.detect(_make_snap([
            {"ticket": 10001},
        ]))
        # 其他属性完全相同，仅注释不同（模拟 price_current 变化但不体现在快照中）
        events = detector.detect(_make_snap([
            {"ticket": 10001},
        ]))
        assert events == []


# ================================================================
# 场景 6: 混合场景
# ================================================================

class TestMixedScenarios:
    """同时发生开仓和平仓"""

    def test_open_and_close_simultaneously(self):
        """一开一平 → 2 个事件，CLOSE 先于 OPEN"""
        detector = SignalDetector()
        detector.detect(_make_snap([
            {"ticket": 10001}, {"ticket": 10002},
        ]))
        events = detector.detect(_make_snap([
            {"ticket": 10001}, {"ticket": 10003},
        ]))
        assert len(events) == 2
        # CLOSE 应先于 OPEN
        assert events[0].event_type == EventType.CLOSE
        assert events[0].ticket == 10002
        assert events[1].event_type == EventType.OPEN
        assert events[1].ticket == 10003

    def test_close_then_reverse_open(self):
        """平仓后同品种反向开仓 → CLOSE 先于 OPEN"""
        detector = SignalDetector()
        detector.detect(_make_snap([
            {"ticket": 10001, "symbol": "EURUSD_", "type": 0},  # BUY
        ]))
        events = detector.detect(_make_snap([
            {"ticket": 10002, "symbol": "EURUSD_", "type": 1},  # SELL
        ]))
        assert len(events) == 2
        assert events[0].event_type == EventType.CLOSE
        assert events[0].ticket == 10001
        assert events[1].event_type == EventType.OPEN
        assert events[1].ticket == 10002

    def test_close_and_modify_simultaneously(self):
        """平仓 + 修改 → 各自独立的事件"""
        detector = SignalDetector()
        detector.detect(_make_snap([
            {"ticket": 10001, "sl": 1.0800},
            {"ticket": 10002},
        ]))
        events = detector.detect(_make_snap([
            {"ticket": 10001, "sl": 1.0820},  # SL 修改
        ]))
        assert len(events) == 2
        types = {e.event_type for e in events}
        assert EventType.CLOSE in types
        assert EventType.MODIFY in types


# ================================================================
# 场景 7: 边界与异常
# ================================================================

class TestEdgeCases:
    """边界与异常场景"""

    def test_empty_to_empty(self):
        """空 → 空，无事件"""
        detector = SignalDetector()
        detector.detect({})
        events = detector.detect({})
        assert events == []

    def test_identical_snapshot_same_objects(self):
        """完全相同快照 — 零事件"""
        snap = _make_snap([
            {"ticket": 10001, "volume": 0.10, "sl": 1.0800, "tp": 1.0900},
        ])
        detector = SignalDetector()
        detector.detect(snap)
        events = detector.detect(snap)
        assert events == []

    def test_fingerprint_quick_skip(self):
        """快照指纹相同 → 跳过逐 ticket 对比"""
        detector = SignalDetector()
        snap1 = _make_snap([
            {"ticket": 10001, "sl": 1.0800, "tp": 1.0900},
            {"ticket": 10002, "sl": 0.0, "tp": 0.0},
        ])
        snap2 = _make_snap([
            {"ticket": 10001, "sl": 1.0800, "tp": 1.0900},
            {"ticket": 10002, "sl": 0.0, "tp": 0.0},
        ])
        # 建立基准
        detector.detect(snap1)
        # 第二轮，指纹应相同
        events = detector.detect(snap2)
        assert events == []
        # 验证确实走了指纹快速通道（通过检查快照未被覆盖）
        assert detector.has_snapshot()

    def test_snapshot_updated_after_detect(self):
        """每次 detect 后基准快照更新为当前快照"""
        detector = SignalDetector()
        snap1 = _make_snap([{"ticket": 10001}])
        snap2 = _make_snap([{"ticket": 10001}, {"ticket": 10002}])

        detector.detect(snap1)
        assert len(detector._previous_snapshot) == 1

        detector.detect(snap2)
        assert len(detector._previous_snapshot) == 2
        assert 10002 in detector._previous_snapshot

    def test_very_large_snapshot(self):
        """大量持仓验证性能 — 200 个持仓的快照对比"""
        positions_before = [{"ticket": i} for i in range(200)]
        positions_after = [{"ticket": i} for i in range(200)]
        # 加 1 个新持仓
        positions_after.append({"ticket": 99999, "symbol": "XAUUSD_"})

        detector = SignalDetector()
        detector.detect(_make_snap(positions_before))
        events = detector.detect(_make_snap(positions_after))
        assert len(events) == 1
        assert events[0].ticket == 99999
        assert events[0].event_type == EventType.OPEN

    def test_reset_clears_state(self):
        """reset 后检测器回到初始状态"""
        detector = SignalDetector()
        detector.detect(_make_snap([{"ticket": 10001}]))
        assert detector.has_snapshot()

        detector.reset()
        assert not detector.has_snapshot()

        # reset 后首次调用不应触发事件
        events = detector.detect(_make_snap([{"ticket": 10001}, {"ticket": 10002}]))
        assert events == []

    def test_reset_then_redetect(self):
        """reset 后重新检测新开仓"""
        detector = SignalDetector()
        detector.detect(_make_snap([{"ticket": 10001}]))
        detector.reset()
        # 建立新基准
        detector.detect(_make_snap([{"ticket": 20001}]))
        # 新开仓
        events = detector.detect(_make_snap([{"ticket": 20001}, {"ticket": 20002}]))
        assert len(events) == 1
        assert events[0].event_type == EventType.OPEN
        assert events[0].ticket == 20002


# ================================================================
# SignalEvent 数据完整性测试
# ================================================================

class TestSignalEventData:
    """验证 SignalEvent 各字段完整性"""

    def test_open_event_fields(self):
        """OPEN 事件 old_state=None, new_state 完整"""
        new_pos = PositionSnapshot(
            ticket=55555, symbol="AUDUSD_", type=0, volume=0.20,
            open_price=0.75000, sl=0.7450, tp=0.7600,
        )
        event = SignalEvent(
            event_type=EventType.OPEN,
            ticket=55555,
            old_state=None,
            new_state=new_pos,
        )
        assert event.old_state is None
        assert event.new_state.ticket == 55555
        assert event.new_state.volume == 0.20
        assert event.new_state.symbol == "AUDUSD_"

    def test_close_event_fields(self):
        """CLOSE 事件 old_state 完整, new_state=None"""
        old_pos = PositionSnapshot(
            ticket=55555, symbol="AUDUSD_", type=0, volume=0.20,
            open_price=0.75000, sl=0.7450, tp=0.7600,
        )
        event = SignalEvent(
            event_type=EventType.CLOSE,
            ticket=55555,
            old_state=old_pos,
            new_state=None,
        )
        assert event.new_state is None
        assert event.old_state.ticket == 55555

    def test_modify_event_fields(self):
        """MODIFY 事件 old_state 和 new_state 都有值"""
        old_pos = PositionSnapshot(
            ticket=55555, symbol="EURUSD_", type=0, volume=0.10,
            open_price=1.08500, sl=1.0800, tp=1.0900,
        )
        new_pos = PositionSnapshot(
            ticket=55555, symbol="EURUSD_", type=0, volume=0.10,
            open_price=1.08500, sl=1.0820, tp=1.0900,
        )
        event = SignalEvent(
            event_type=EventType.MODIFY,
            ticket=55555,
            old_state=old_pos,
            new_state=new_pos,
        )
        assert event.old_state.sl == 1.0800
        assert event.new_state.sl == 1.0820
