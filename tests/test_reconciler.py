"""
对账模块单元测试 — 覆盖持仓差异检测、缺失/多余/手数/SL偏差、自动修复。

使用 mock 数据模拟主从账户持仓对比。
"""

import pytest
from src.reconciler import (
    Reconciler,
    ReconcileConfig,
    ReconcileResult,
    DiffType,
    PositionInfo,
)


# ---- Helpers ----

def _make_pos(ticket: int, symbol: str = "EURUSD_", direction: int = 0,
              volume: float = 0.10, sl: float = 0.0, tp: float = 0.0) -> PositionInfo:
    return PositionInfo(
        ticket=ticket, symbol=symbol, direction=direction,
        volume=volume, sl=sl, tp=tp,
    )


@pytest.fixture
def config():
    return ReconcileConfig(auto_fix=False)


@pytest.fixture
def reconciler(config):
    return Reconciler(config)


# ---- 场景 1: 完全一致 ----

class TestMatch:
    def test_all_match(self, reconciler):
        lead_positions = {
            10001: _make_pos(10001, "EURUSD_", 0, 0.10),
            10002: _make_pos(10002, "GBPUSD_", 1, 0.05),
        }
        follower_positions = [
            _make_pos(90001, "EURUSD_", 0, 0.10),
            _make_pos(90002, "GBPUSD_", 1, 0.05),
        ]
        mapping = {
            10001: {"follower_ticket": 90001, "symbol": "EURUSD_", "volume": 0.10},
            10002: {"follower_ticket": 90002, "symbol": "GBPUSD_", "volume": 0.05},
        }
        results = reconciler.reconcile(lead_positions, follower_positions, mapping, ratio=1.0)
        assert len(results) == 0

    def test_empty_both(self, reconciler):
        results = reconciler.reconcile({}, [], {}, ratio=1.0)
        assert len(results) == 0


# ---- 场景 2: 缺失持仓 ----

class TestMissing:
    def test_no_mapping_found(self, reconciler):
        lead = {10001: _make_pos(10001, "EURUSD_", 0, 0.10)}
        results = reconciler.reconcile(lead, [], {}, ratio=1.0)
        assert len(results) == 1
        assert results[0].diff_type == DiffType.MISSING
        assert results[0].master_ticket == 10001

    def test_orphan_mapping(self, reconciler):
        """映射存在但从账户无对应持仓"""
        lead = {10001: _make_pos(10001, "EURUSD_", 0, 0.10)}
        follower = []  # 90001 不存在
        mapping = {10001: {"follower_ticket": 90001, "symbol": "EURUSD_", "volume": 0.10}}
        results = reconciler.reconcile(lead, follower, mapping, ratio=1.0)
        assert len(results) == 1
        assert results[0].diff_type == DiffType.ORPHAN_MAPPING


# ---- 场景 3: 手数偏差 ----

class TestVolumeMismatch:
    def test_volume_diff(self, reconciler):
        lead = {10001: _make_pos(10001, "EURUSD_", 0, 0.10)}
        follower = [_make_pos(90001, "EURUSD_", 0, 0.05)]
        mapping = {10001: {"follower_ticket": 90001, "symbol": "EURUSD_", "volume": 0.05}}
        results = reconciler.reconcile(lead, follower, mapping, ratio=1.0)
        assert len(results) == 1
        assert results[0].diff_type == DiffType.VOLUME_MISMATCH
        assert results[0].expected_volume == 0.10
        assert results[0].actual_volume == 0.05

    def test_volume_with_ratio(self, reconciler):
        """按比例计算预期手数"""
        lead = {10001: _make_pos(10001, "EURUSD_", 0, 0.10)}
        follower = [_make_pos(90001, "EURUSD_", 0, 0.03)]
        mapping = {10001: {"follower_ticket": 90001, "symbol": "EURUSD_", "volume": 0.03}}
        results = reconciler.reconcile(lead, follower, mapping, ratio=0.5)
        assert len(results) == 1
        assert results[0].expected_volume == 0.05  # 0.10 * 0.5

    def test_volume_tolerance(self, reconciler):
        """手数微小偏差（< 0.001）可容忍"""
        config = ReconcileConfig(auto_fix=False, volume_tolerance=0.001)
        r = Reconciler(config)
        lead = {10001: _make_pos(10001, "EURUSD_", 0, 0.10001)}
        follower = [_make_pos(90001, "EURUSD_", 0, 0.10)]
        mapping = {10001: {"follower_ticket": 90001, "symbol": "EURUSD_", "volume": 0.10}}
        results = r.reconcile(lead, follower, mapping, ratio=1.0)
        assert len(results) == 0  # 偏差在容忍范围内


# ---- 场景 4: 多余持仓 ----

class TestExtraPosition:
    def test_follower_has_untracked_position(self, reconciler):
        """从账户有跟单 magic 的持仓但无主账户映射"""
        lead = {}
        follower = [_make_pos(90099, "XAUUSD_", 0, 0.10)]
        results = reconciler.reconcile(lead, follower, {}, ratio=1.0)
        assert len(results) == 1
        assert results[0].diff_type == DiffType.EXTRA_POSITION
        assert results[0].follower_ticket == 90099


# ---- 场景 5: SL/TP 偏差 ----

class TestSLTPMismatch:
    def test_sl_diff(self, reconciler):
        lead = {10001: _make_pos(10001, "EURUSD_", 0, 0.10, sl=1.0800)}
        follower = [_make_pos(90001, "EURUSD_", 0, 0.10, sl=1.0750)]
        mapping = {10001: {"follower_ticket": 90001, "symbol": "EURUSD_", "volume": 0.10}}
        results = reconciler.reconcile(lead, follower, mapping, ratio=1.0)
        assert len(results) == 1
        assert results[0].diff_type == DiffType.SL_MISMATCH
        assert results[0].expected_sl == 1.0800
        assert results[0].actual_sl == 1.0750

    def test_tp_diff(self, reconciler):
        lead = {10001: _make_pos(10001, "EURUSD_", 0, 0.10, tp=1.0900)}
        follower = [_make_pos(90001, "EURUSD_", 0, 0.10, tp=1.0850)]
        mapping = {10001: {"follower_ticket": 90001, "symbol": "EURUSD_", "volume": 0.10}}
        results = reconciler.reconcile(lead, follower, mapping, ratio=1.0)
        assert len(results) == 1
        assert results[0].diff_type == DiffType.TP_MISMATCH

    def test_sl_tp_tolerance(self, reconciler):
        """SL 差值 < 0.5 个 pip 可容忍"""
        config = ReconcileConfig(auto_fix=False, price_tolerance_pips=0.5)
        r = Reconciler(config)
        lead = {10001: _make_pos(10001, "EURUSD_", 0, 0.10, sl=1.08005)}
        follower = [_make_pos(90001, "EURUSD_", 0, 0.10, sl=1.08000)]
        mapping = {10001: {"follower_ticket": 90001, "symbol": "EURUSD_", "volume": 0.10}}
        results = r.reconcile(lead, follower, mapping, ratio=1.0)
        assert len(results) == 0  # 5e-5 < 5e-5 tolerance


# ---- 场景 6: 自动修复 ----

class TestAutoFix:
    def test_auto_fix_enabled_returns_fix_action(self, reconciler):
        config = ReconcileConfig(auto_fix=True)
        r = Reconciler(config)
        lead = {10001: _make_pos(10001, "EURUSD_", 0, 0.10, sl=1.0800)}
        follower = [_make_pos(90001, "EURUSD_", 0, 0.10, sl=1.0750)]
        mapping = {10001: {"follower_ticket": 90001, "symbol": "EURUSD_", "volume": 0.10}}
        results = r.reconcile(lead, follower, mapping, ratio=1.0)
        assert len(results) == 1
        assert results[0].auto_fix_action is not None
        assert results[0].auto_fix_action == "MODIFY_SL"

    def test_auto_fix_disabled_no_action(self, reconciler):
        lead = {10001: _make_pos(10001, "EURUSD_", 0, 0.10, sl=1.0800)}
        follower = [_make_pos(90001, "EURUSD_", 0, 0.10, sl=1.0750)]
        mapping = {10001: {"follower_ticket": 90001, "symbol": "EURUSD_", "volume": 0.10}}
        results = reconciler.reconcile(lead, follower, mapping, ratio=1.0)
        assert len(results) == 1
        assert results[0].auto_fix_action is None


# ---- 结果摘要 ----

class TestSummary:
    def test_summary_counts_by_type(self, reconciler):
        """摘要应按差异类型统计"""
        # 造一个混合场景
        lead = {
            10001: _make_pos(10001, "EURUSD_", 0, 0.10, sl=1.0800),   # 手数+SL 双重差异
            10002: _make_pos(10002, "GBPUSD_", 1, 0.05),               # 正常
        }
        follower = [
            _make_pos(90001, "EURUSD_", 0, 0.05, sl=1.0750),
            _make_pos(90002, "GBPUSD_", 1, 0.05),
            _make_pos(90099, "XAUUSD_", 0, 0.10),  # 多余
        ]
        mapping = {
            10001: {"follower_ticket": 90001, "symbol": "EURUSD_", "volume": 0.05},
            10002: {"follower_ticket": 90002, "symbol": "GBPUSD_", "volume": 0.05},
        }
        results = reconciler.reconcile(lead, follower, mapping, ratio=1.0)
        summary = reconciler.summarize(results)
        assert summary["total_diffs"] >= 3  # 手数+SL+多余
        assert summary["status"] in ("MATCH", "MISMATCH")
