"""
风控管理器单元测试 — 覆盖仓位限制、回撤、单品种上限、单日亏损。

所有测试使用 mock 数据，不依赖真实账户。
"""

import pytest
from src.risk_manager import RiskManager, RiskConfig, RiskCheckResult, RiskStatus


@pytest.fixture
def default_config():
    return RiskConfig(
        max_positions=10,
        max_position_pct=80.0,
        max_drawdown_pct=20.0,
        max_daily_loss=500.0,
        max_single_symbol_lot=1.0,
    )


@pytest.fixture
def risk(default_config):
    return RiskManager(default_config)


# ---- 场景 1: 仓位数量检查 ----

class TestPositionCount:
    def test_under_limit_passes(self, risk):
        result = risk.check_open(current_positions=5)
        assert result.status == RiskStatus.PASSED

    def test_at_limit_rejects(self, risk):
        result = risk.check_open(current_positions=10)
        assert result.status == RiskStatus.REJECTED
        assert "持仓数已达上限" in result.reason

    def test_over_limit_rejects(self, risk):
        result = risk.check_open(current_positions=12)
        assert result.status == RiskStatus.REJECTED

    def test_close_not_limited(self, risk):
        result = risk.check_close(current_positions=10)
        assert result.status == RiskStatus.PASSED

    def test_modify_not_limited(self, risk):
        result = risk.check_modify(current_positions=10)
        assert result.status == RiskStatus.PASSED


# ---- 场景 2: 总仓位占比 ----

class TestMarginUsage:
    def test_under_limit_passes(self, risk):
        result = risk.check_open(
            current_positions=5,
            balance=10000.0,
            current_margin=5000.0,
            new_margin=2000.0,
        )
        assert result.status == RiskStatus.PASSED

    def test_over_limit_rejects(self, risk):
        result = risk.check_open(
            current_positions=5,
            balance=10000.0,
            current_margin=7500.0,
            new_margin=1000.0,
        )
        assert result.status == RiskStatus.REJECTED
        assert "85.0%" in result.reason

    def test_zero_balance_handled_safely(self, risk):
        """余额为 0 时直接拒绝"""
        result = risk.check_open(
            current_positions=0,
            balance=0.0,
            current_margin=0.0,
            new_margin=100.0,
        )
        assert result.status == RiskStatus.REJECTED

    def test_no_balance_no_margin_passes(self, risk):
        """不提供余额和保证金数据时跳过仓位占比检查"""
        result = risk.check_open(current_positions=1)
        assert result.status == RiskStatus.PASSED


# ---- 场景 3: 回撤检查 ----

class TestDrawdown:
    def test_under_limit_passes(self, risk):
        result = risk.check_open(
            current_positions=1,
            equity=10500.0,
            peak_equity=12000.0,
        )
        assert result.status == RiskStatus.PASSED

    def test_over_limit_rejects(self, risk):
        result = risk.check_open(
            current_positions=1,
            equity=9000.0,
            peak_equity=12000.0,
        )
        assert result.status == RiskStatus.REJECTED
        assert "回撤" in result.reason and "25.0%" in result.reason

    def test_no_peak_equity_skips_check(self, risk):
        """无峰值数据时跳过回撤检查"""
        result = risk.check_open(current_positions=1, equity=10000.0)
        assert result.status == RiskStatus.PASSED

    def test_peak_below_equity_passes(self, risk):
        """当前净值高于峰值 - 无回撤"""
        result = risk.check_open(
            current_positions=1,
            equity=15000.0,
            peak_equity=12000.0,
        )
        assert result.status == RiskStatus.PASSED


# ---- 场景 4: 单品种持仓限制 ----

class TestSingleSymbolLimit:
    def test_under_limit_passes(self, risk):
        result = risk.check_open(
            current_positions=1,
            symbol="EURUSD_",
            new_volume=0.3,
            current_symbol_volume={"EURUSD_": 0.5},
        )
        assert result.status == RiskStatus.PASSED

    def test_over_limit_rejects(self, risk):
        result = risk.check_open(
            current_positions=1,
            symbol="EURUSD_",
            new_volume=0.3,
            current_symbol_volume={"EURUSD_": 0.8},
        )
        assert result.status == RiskStatus.REJECTED
        assert "EURUSD_" in result.reason

    def test_no_symbol_volume_data_skips_check(self, risk):
        """无品种数据时跳过单品种检查"""
        result = risk.check_open(current_positions=1, symbol="EURUSD_", new_volume=0.5)
        assert result.status == RiskStatus.PASSED


# ---- 场景 5: 单日亏损限制 ----

class TestDailyLoss:
    def test_under_limit_passes(self, risk):
        result = risk.check_open(
            current_positions=1,
            daily_loss=200.0,
        )
        assert result.status == RiskStatus.PASSED

    def test_at_limit_rejects(self, risk):
        result = risk.check_open(
            current_positions=1,
            daily_loss=500.0,
        )
        assert result.status == RiskStatus.REJECTED
        assert "单日亏损" in result.reason

    def test_over_limit_rejects(self, risk):
        result = risk.check_open(
            current_positions=1,
            daily_loss=600.0,
        )
        assert result.status == RiskStatus.REJECTED

    def test_no_daily_loss_data_passes(self, risk):
        result = risk.check_open(current_positions=1)
        assert result.status == RiskStatus.PASSED


# ---- 场景 6: 风控报告 ----

class TestRiskReport:
    def test_report_fields(self, risk):
        risk.update_state(
            positions=5,
            margin_usage_pct=50.0,
            drawdown_pct=12.5,
            daily_loss=200.0,
        )
        report = risk.get_report()
        assert report["total_positions"] == 5
        assert report["position_limit"] == 10
        assert report["margin_usage_pct"] == 50.0
        assert report["drawdown_pct"] == 12.5
        assert report["daily_loss"] == 200.0
        assert report["daily_loss_limit"] == 500.0
        assert report["drawdown_limit_pct"] == 20.0

    def test_report_initial_state(self, risk):
        report = risk.get_report()
        assert report["total_positions"] == 0
        assert report["margin_usage_pct"] == 0.0


# ---- 综合检查 (便捷方法) ----

class TestCheckAll:
    def test_all_pass(self, risk):
        result = risk.check_all(
            current_positions=5,
            balance=10000.0,
            current_margin=5000.0,
            new_margin=2000.0,
            equity=10500.0,
            peak_equity=12000.0,
            daily_loss=200.0,
            symbol="EURUSD_",
            new_volume=0.3,
            current_symbol_volume={"EURUSD_": 0.5},
        )
        assert result.status == RiskStatus.PASSED

    def test_first_failure_stops_checking(self, risk):
        """第一个失败的检查即停止，返回该失败原因"""
        result = risk.check_all(
            current_positions=10,  # 已达上限
            balance=10000.0,
            current_margin=7500.0,
            new_margin=1000.0,
        )
        assert result.status == RiskStatus.REJECTED
        assert "持仓数" in result.reason

    def test_second_check_fails(self, risk):
        """持仓数通过但仓位占比失败"""
        result = risk.check_all(
            current_positions=5,
            balance=10000.0,
            current_margin=7500.0,
            new_margin=1000.0,
        )
        assert result.status == RiskStatus.REJECTED
        assert "85.0%" in result.reason or "仓位" in result.reason
