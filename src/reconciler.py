"""
持仓对账模块 — 定期比较主账户和从账户的持仓差异。

检测的差异类型:
  - MISSING: 主账户有持仓但从账户无映射
  - ORPHAN_MAPPING: 映射存在但从账户无对应持仓
  - VOLUME_MISMATCH: 从账户手数与预期不一致
  - EXTRA_POSITION: 从账户有多余的跟单持仓
  - SL_MISMATCH / TP_MISMATCH: SL/TP 值与主账户不一致

使用方式:
    config = ReconcileConfig(auto_fix=False)
    reconciler = Reconciler(config)
    results = reconciler.reconcile(lead_positions, follower_positions, mapping, ratio=1.0)
    summary = reconciler.summarize(results)
"""

import logging
from dataclasses import dataclass, field
from enum import Enum, auto

logger = logging.getLogger(__name__)


class DiffType(Enum):
    MISSING = auto()           # 主有从无映射
    ORPHAN_MAPPING = auto()     # 映射在但从持仓已消失
    VOLUME_MISMATCH = auto()    # 手数不一致
    EXTRA_POSITION = auto()     # 从账户多余持仓
    SL_MISMATCH = auto()        # 止损不一致
    TP_MISMATCH = auto()        # 止盈不一致


@dataclass
class PositionInfo:
    """通用持仓信息"""
    ticket: int
    symbol: str = ""
    direction: int = 0  # 0=BUY, 1=SELL
    volume: float = 0.0
    sl: float = 0.0
    tp: float = 0.0


@dataclass
class ReconcileConfig:
    """对账配置"""
    auto_fix: bool = False              # 是否自动修复
    volume_tolerance: float = 0.001     # 手数容忍度
    price_tolerance_pips: float = 0.5   # 价格容忍度 (pip)


@dataclass
class ReconcileResult:
    """单条对账差异"""
    diff_type: DiffType
    master_ticket: int | None = None
    follower_ticket: int | None = None
    symbol: str = ""
    expected_volume: float = 0.0
    actual_volume: float = 0.0
    expected_sl: float = 0.0
    actual_sl: float = 0.0
    expected_tp: float = 0.0
    actual_tp: float = 0.0
    auto_fix_action: str | None = None  # MODIFY_SL, MODIFY_TP, OPEN, CLOSE


class Reconciler:
    """持仓对账器"""

    # 不同品种的 pip 值（5-digit: 0.0001, 3-digit: 0.01）
    _DEFAULT_PIP = 0.0001

    def __init__(self, config: ReconcileConfig):
        self.config = config

    # ---- 公共 API ----

    def reconcile(
        self,
        lead_positions: dict[int, PositionInfo],
        follower_positions: list[PositionInfo],
        mapping: dict[int, dict],
        ratio: float = 1.0,
    ) -> list[ReconcileResult]:
        """
        对比主从账户持仓，返回差异列表。

        Args:
            lead_positions: 主账户持仓, key=master_ticket
            follower_positions: 从账户持仓列表
            mapping: master_ticket → follower 映射
            ratio: 跟单比例

        Returns:
            差异列表，空列表表示完全一致
        """
        results: list[ReconcileResult] = []

        # 构建从账户查找：follower_ticket → PositionInfo
        follower_by_ticket: dict[int, PositionInfo] = {
            p.ticket: p for p in follower_positions
        }

        # 1. 检查主账户每个持仓
        for master_ticket, lead_pos in lead_positions.items():
            map_entry = mapping.get(master_ticket)

            if map_entry is None:
                # 主账户有持仓但无映射 → MISSING
                results.append(ReconcileResult(
                    diff_type=DiffType.MISSING,
                    master_ticket=master_ticket,
                    symbol=lead_pos.symbol,
                    expected_volume=lead_pos.volume,
                ))
                continue

            follower_ticket = map_entry["follower_ticket"]
            follower_pos = follower_by_ticket.get(follower_ticket)

            if follower_pos is None:
                # 映射存在但从账户无此持仓 → ORPHAN_MAPPING
                results.append(ReconcileResult(
                    diff_type=DiffType.ORPHAN_MAPPING,
                    master_ticket=master_ticket,
                    follower_ticket=follower_ticket,
                    symbol=lead_pos.symbol,
                ))
                continue

            # 2. 检查手数
            expected_vol = round(lead_pos.volume * ratio, 8)
            vol_diff = abs(follower_pos.volume - expected_vol)
            if vol_diff > self.config.volume_tolerance:
                results.append(ReconcileResult(
                    diff_type=DiffType.VOLUME_MISMATCH,
                    master_ticket=master_ticket,
                    follower_ticket=follower_ticket,
                    symbol=lead_pos.symbol,
                    expected_volume=expected_vol,
                    actual_volume=follower_pos.volume,
                ))

            # 3. 检查 SL
            pip_val = self._get_pip_value(lead_pos.symbol)
            sl_diff = abs(follower_pos.sl - lead_pos.sl)
            # SL 为 0 表示未设置，两个都是 0 则跳过
            if not (lead_pos.sl == 0.0 and follower_pos.sl == 0.0):
                if sl_diff > self.config.price_tolerance_pips * pip_val:
                    r = ReconcileResult(
                        diff_type=DiffType.SL_MISMATCH,
                        master_ticket=master_ticket,
                        follower_ticket=follower_ticket,
                        symbol=lead_pos.symbol,
                        expected_sl=lead_pos.sl,
                        actual_sl=follower_pos.sl,
                    )
                    if self.config.auto_fix:
                        r.auto_fix_action = "MODIFY_SL"
                    results.append(r)

            # 4. 检查 TP
            tp_diff = abs(follower_pos.tp - lead_pos.tp)
            if not (lead_pos.tp == 0.0 and follower_pos.tp == 0.0):
                if tp_diff > self.config.price_tolerance_pips * pip_val:
                    r = ReconcileResult(
                        diff_type=DiffType.TP_MISMATCH,
                        master_ticket=master_ticket,
                        follower_ticket=follower_ticket,
                        symbol=lead_pos.symbol,
                        expected_tp=lead_pos.tp,
                        actual_tp=follower_pos.tp,
                    )
                    if self.config.auto_fix:
                        r.auto_fix_action = "MODIFY_TP"
                    results.append(r)

        # 5. 检查从账户多余持仓
        mapped_follower_tickets = {
            m["follower_ticket"] for m in mapping.values()
        }
        for fp in follower_positions:
            if fp.ticket not in mapped_follower_tickets:
                results.append(ReconcileResult(
                    diff_type=DiffType.EXTRA_POSITION,
                    follower_ticket=fp.ticket,
                    symbol=fp.symbol,
                    actual_volume=fp.volume,
                ))

        return results

    # ---- 结果汇总 ----

    def summarize(self, results: list[ReconcileResult]) -> dict:
        """生成差异汇总报告"""
        counts = {t: 0 for t in DiffType}
        for r in results:
            counts[r.diff_type] += 1

        return {
            "total_diffs": len(results),
            "by_type": {t.name: c for t, c in counts.items()},
            "status": "MATCH" if len(results) == 0 else "MISMATCH",
            "details": [
                {
                    "type": r.diff_type.name,
                    "master_ticket": r.master_ticket,
                    "follower_ticket": r.follower_ticket,
                    "symbol": r.symbol,
                    "auto_fix": r.auto_fix_action,
                }
                for r in results
            ],
        }

    # ---- 辅助 ----

    @staticmethod
    def _get_pip_value(symbol: str) -> float:
        """根据品种名推断 pip 值"""
        # JPY 类品种: 1 pip = 0.01
        if "JPY" in symbol.upper():
            return 0.01
        # 黄金/贵金属: 1 pip = 0.01 (取决于经纪商)
        if any(x in symbol.upper() for x in ["XAU", "XAG"]):
            return 0.01
        # 加密货币: 1 pip = 1.0
        if any(x in symbol.upper() for x in ["BTC", "ETH", "CRYPTO"]):
            return 1.0
        # 默认外汇: 1 pip = 0.0001 (5-digit)
        return 0.0001
