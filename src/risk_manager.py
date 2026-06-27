"""
风控管理器 — 在每次跟单操作前检查风险指标。

检查维度:
  1. 持仓数量限制
  2. 总仓位占比（保证金使用率）
  3. 最大回撤
  4. 单品种持仓手数上限
  5. 单日最大亏损

使用方式:
    config = RiskConfig(max_positions=10, max_position_pct=80.0, ...)
    rm = RiskManager(config)
    result = rm.check_all(current_positions=5, balance=10000, ...)
    if result.status == RiskStatus.PASSED:
        executor.execute(...)
"""

from dataclasses import dataclass
from enum import Enum, auto


class RiskStatus(Enum):
    PASSED = auto()
    REJECTED = auto()


@dataclass
class RiskCheckResult:
    status: RiskStatus
    reason: str = ""


@dataclass
class RiskConfig:
    """风控参数配置"""
    max_positions: int = 10              # 最大持仓数
    max_position_pct: float = 80.0       # 最大总仓位占比 (%)
    max_drawdown_pct: float = 20.0       # 最大回撤 (%)
    max_daily_loss: float = 500.0        # 单日最大亏损 (USD)
    max_single_symbol_lot: float = 1.0   # 单品种最大手数


class RiskManager:
    """
    风控管理器 — 多维度风险检查。

    每个 check_* 方法独立检查一个维度，返回 RiskCheckResult。
    check_all() 汇总所有检查，遇到第一个失败即停止。
    """

    def __init__(self, config: RiskConfig):
        self.config = config
        self._current_state: dict = {
            "positions": 0,
            "margin_usage_pct": 0.0,
            "drawdown_pct": 0.0,
            "daily_loss": 0.0,
        }

    # ---- 独立检查方法 ----

    def check_open(
        self,
        current_positions: int = 0,
        balance: float | None = None,
        current_margin: float | None = None,
        new_margin: float | None = None,
        equity: float | None = None,
        peak_equity: float | None = None,
        daily_loss: float | None = None,
        symbol: str = "",
        new_volume: float = 0.0,
        current_symbol_volume: dict[str, float] | None = None,
    ) -> RiskCheckResult:
        """
        检查是否允许开仓。

        各参数按需传入，未传入的维度将被跳过。
        """
        cfg = self.config

        # 1. 持仓数量
        if current_positions >= cfg.max_positions:
            return RiskCheckResult(
                RiskStatus.REJECTED,
                f"持仓数已达上限 {cfg.max_positions} (当前 {current_positions})"
            )

        # 2. 总仓位占比
        if balance is not None and current_margin is not None and new_margin is not None:
            if balance <= 0:
                return RiskCheckResult(RiskStatus.REJECTED, "账户余额为 0")
            new_usage_pct = (current_margin + new_margin) / balance * 100
            if new_usage_pct > cfg.max_position_pct:
                return RiskCheckResult(
                    RiskStatus.REJECTED,
                    f"总仓位占比 {new_usage_pct:.1f}% 超过上限 {cfg.max_position_pct}%"
                )

        # 3. 回撤
        if equity is not None and peak_equity is not None and peak_equity > 0:
            dd_pct = (peak_equity - equity) / peak_equity * 100
            if dd_pct > cfg.max_drawdown_pct:
                return RiskCheckResult(
                    RiskStatus.REJECTED,
                    f"当前回撤 {dd_pct:.1f}% 超过上限 {cfg.max_drawdown_pct}%"
                )

        # 4. 单品种持仓
        if symbol and new_volume > 0 and current_symbol_volume is not None:
            current_vol = current_symbol_volume.get(symbol, 0.0)
            if current_vol + new_volume > cfg.max_single_symbol_lot:
                return RiskCheckResult(
                    RiskStatus.REJECTED,
                    f"{symbol} 持仓手数 {current_vol + new_volume:.2f} 超过上限 {cfg.max_single_symbol_lot}"
                )

        # 5. 单日亏损
        if daily_loss is not None and daily_loss >= cfg.max_daily_loss:
            return RiskCheckResult(
                RiskStatus.REJECTED,
                f"单日亏损已达上限 {cfg.max_daily_loss} USD"
            )

        return RiskCheckResult(status=RiskStatus.PASSED)

    def check_close(self, current_positions: int = 0) -> RiskCheckResult:
        """检查是否允许平仓（平仓不受持仓数限制）"""
        return RiskCheckResult(status=RiskStatus.PASSED)

    def check_modify(self, current_positions: int = 0) -> RiskCheckResult:
        """检查是否允许修改（修改不受持仓数限制）"""
        return RiskCheckResult(status=RiskStatus.PASSED)

    # ---- 综合检查 ----

    def check_all(
        self,
        current_positions: int = 0,
        balance: float | None = None,
        current_margin: float | None = None,
        new_margin: float | None = None,
        equity: float | None = None,
        peak_equity: float | None = None,
        daily_loss: float | None = None,
        symbol: str = "",
        new_volume: float = 0.0,
        current_symbol_volume: dict[str, float] | None = None,
    ) -> RiskCheckResult:
        """综合检查所有维度，第一个失败即返回"""
        return self.check_open(
            current_positions=current_positions,
            balance=balance,
            current_margin=current_margin,
            new_margin=new_margin,
            equity=equity,
            peak_equity=peak_equity,
            daily_loss=daily_loss,
            symbol=symbol,
            new_volume=new_volume,
            current_symbol_volume=current_symbol_volume,
        )

    # ---- 状态管理 ----

    def update_state(
        self,
        positions: int | None = None,
        margin_usage_pct: float | None = None,
        drawdown_pct: float | None = None,
        daily_loss: float | None = None,
    ) -> None:
        """更新风控内部状态"""
        if positions is not None:
            self._current_state["positions"] = positions
        if margin_usage_pct is not None:
            self._current_state["margin_usage_pct"] = margin_usage_pct
        if drawdown_pct is not None:
            self._current_state["drawdown_pct"] = drawdown_pct
        if daily_loss is not None:
            self._current_state["daily_loss"] = daily_loss

    def get_report(self) -> dict:
        """获取当前风控状态报告"""
        return {
            "total_positions": self._current_state["positions"],
            "position_limit": self.config.max_positions,
            "margin_usage_pct": self._current_state["margin_usage_pct"],
            "margin_limit_pct": self.config.max_position_pct,
            "drawdown_pct": self._current_state["drawdown_pct"],
            "drawdown_limit_pct": self.config.max_drawdown_pct,
            "daily_loss": self._current_state["daily_loss"],
            "daily_loss_limit": self.config.max_daily_loss,
            "single_symbol_lot_limit": self.config.max_single_symbol_lot,
        }
