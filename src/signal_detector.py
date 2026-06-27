"""
信号检测器 — 基于快照差量对比检测主账户持仓变化。

核心算法:
  1. 每次轮询获取当前持仓快照
  2. 对比内部缓存的上一轮快照
  3. 差量分析：
     - 新增 ticket → OPEN 信号
     - 消失 ticket → CLOSE 信号
     - ticket 相同但 SL/TP/Volume 变化 → MODIFY 信号
  4. 事件排序: CLOSE → MODIFY → OPEN（先平后开）

使用方式:
    detector = SignalDetector()
    current = get_positions_from_mt5()  # dict[ticket, PositionSnapshot]
    events = detector.detect(current)   # list[SignalEvent]
"""

import hashlib
import logging
import time
from dataclasses import dataclass
from enum import Enum, auto

logger = logging.getLogger(__name__)


class EventType(Enum):
    """信号事件类型"""
    OPEN = auto()     # 新开仓
    CLOSE = auto()    # 平仓
    MODIFY = auto()   # 修改（SL/TP/Volume）


@dataclass
class PositionSnapshot:
    """
    持仓快照 — 某一时刻单个持仓的完整状态。

    注意：不包含 price_current（当前价格），因为它始终变化，
    不作为操盘信号。MT5 的 position 中 ticket 是持仓唯一标识。
    """
    ticket: int
    symbol: str = ""
    type: int = 0           # 0=BUY, 1=SELL (MT5 POSITION_TYPE)
    volume: float = 0.0
    open_price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    comment: str = ""
    open_time: int = 0      # Unix timestamp

    def fingerprint_fields(self) -> str:
        """返回用于快照指纹的关键字段字符串"""
        return f"{self.ticket}:{self.volume}:{self.sl}:{self.tp}"


@dataclass
class SignalEvent:
    """
    信号事件 — 一次检测到的持仓变化。

    - OPEN:  old_state=None, new_state=新持仓
    - CLOSE: old_state=被平持仓, new_state=None
    - MODIFY: old_state=修改前, new_state=修改后
    """
    event_type: EventType
    ticket: int
    old_state: PositionSnapshot | None = None
    new_state: PositionSnapshot | None = None
    timestamp: float = 0.0   # 事件发生时间（由 detect 填充）


class SignalDetector:
    """
    主账户信号检测器。

    内部维护上一轮持仓快照，每次 detect() 执行差量对比。
    """

    def __init__(self):
        self._previous_snapshot: dict[int, PositionSnapshot] = {}
        self._previous_fingerprint: str = "empty"  # 与空快照指纹一致
        self._is_first_run: bool = True

    # ---- 公共 API ----

    def detect(self, current: dict[int, PositionSnapshot]) -> list[SignalEvent]:
        """
        对比当前快照与基准快照，返回变更事件列表。

        Args:
            current: 当前持仓快照，key=ticket, value=PositionSnapshot

        Returns:
            事件列表，顺序: CLOSE → MODIFY → OPEN（平仓优先）

        Raises:
            不抛出异常 — 所有异常内部捕获并记录日志
        """
        try:
            events = self._detect_impl(current, time.time())
            # 更新基准快照
            self._previous_snapshot = current
            self._previous_fingerprint = self._compute_fingerprint(current)
            self._is_first_run = False
            return events
        except Exception as e:
            logger.error(f"信号检测异常: {e}", exc_info=True)
            return []

    def has_snapshot(self) -> bool:
        """是否已建立基准快照"""
        return not self._is_first_run

    def reset(self) -> None:
        """重置检测器状态，丢弃所有历史快照"""
        self._previous_snapshot.clear()
        self._previous_fingerprint = "empty"
        self._is_first_run = True
        logger.info("检测器已重置")

    # ---- 内部实现 ----

    def _detect_impl(self, current: dict[int, PositionSnapshot],
                     ts: float) -> list[SignalEvent]:
        """检测实现，与状态更新解耦"""

        # 首次启动：不触发事件，仅建立基准
        if self._is_first_run:
            logger.info(f"首次快照已建立，持仓数: {len(current)}")
            return []

        # 快照指纹相同 → 跳过详细对比（性能优化）
        current_fp = self._compute_fingerprint(current)
        if current_fp == self._previous_fingerprint:
            return []

        # 逐 ticket 对比
        events: list[SignalEvent] = []
        old = self._previous_snapshot
        new = current

        old_tickets = set(old.keys())
        new_tickets = set(new.keys())

        # 1. 检测平仓（消失的 ticket）— CLOSE
        for ticket in old_tickets - new_tickets:
            events.append(SignalEvent(
                event_type=EventType.CLOSE,
                ticket=ticket,
                old_state=old[ticket],
                new_state=None,
                timestamp=ts,
            ))

        # 2. 检测新开仓（新增的 ticket）— OPEN
        for ticket in new_tickets - old_tickets:
            events.append(SignalEvent(
                event_type=EventType.OPEN,
                ticket=ticket,
                old_state=None,
                new_state=new[ticket],
                timestamp=ts,
            ))

        # 3. 检测修改（仍在的 ticket 但字段变化）— MODIFY
        for ticket in old_tickets & new_tickets:
            old_pos = old[ticket]
            new_pos = new[ticket]
            if self._has_changed(old_pos, new_pos):
                events.append(SignalEvent(
                    event_type=EventType.MODIFY,
                    ticket=ticket,
                    old_state=old_pos,
                    new_state=new_pos,
                    timestamp=ts,
                ))

        return events

    # ---- 辅助方法 ----

    @staticmethod
    def _compute_fingerprint(snapshot: dict[int, PositionSnapshot]) -> str:
        """计算快照指纹，用于快速判断是否有变化"""
        if not snapshot:
            return "empty"
        parts = sorted(
            pos.fingerprint_fields()
            for ticket, pos in snapshot.items()
        )
        raw = "|".join(parts)
        return hashlib.md5(raw.encode()).hexdigest()

    @staticmethod
    def _has_changed(old: PositionSnapshot, new: PositionSnapshot) -> bool:
        """检查两个同名持仓的关键字段是否有变化"""
        return (
            old.volume != new.volume
            or old.sl != new.sl
            or old.tp != new.tp
        )
