"""
跟单执行引擎 — 将信号事件转换为 MT5 订单操作。

核心流程:
  1. 对事件列表按 CLOSE → MODIFY → OPEN 排序
  2. 对每个事件：
     a. 查品种信息、执行品种映射
     b. OPEN: 计算比例手数 → 精度截断 → 检查上下限 → 市价下单
     c. CLOSE: 查映射 → 平仓对应从订单
     d. MODIFY: 查映射 → 修改 SL/TP
  3. 每次下单前检查间隔（≥ 500ms），不足则 sleep
  4. 记录主→从订单映射，平仓后清除

使用方式:
    config = CopyConfig(ratio=1.0, symbol_mapping={"EURUSD": "EURUSD_"})
    store = OrderMappingStore()
    executor = CopyExecutor(config, store)

    events = signal_detector.detect(current_positions)
    results = executor.execute(events, mt5_connector.order_send, mt5_connector.get_symbol_info)
"""

import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable

from src.signal_detector import EventType, SignalEvent

logger = logging.getLogger(__name__)


# ---- MT5 常量 ----
TRADE_ACTION_DEAL = 1
TRADE_ACTION_MODIFY = 6
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1


class ResultStatus(Enum):
    """跟单操作结果状态"""
    SUCCESS = auto()
    FAILED = auto()
    SKIPPED = auto()


@dataclass
class CopyConfig:
    """跟单配置"""
    ratio: float = 1.0                      # 跟单比例
    min_lot: float = 0.01                   # 最小跟单手数
    max_lot: float = 100.0                  # 最大跟单手数
    symbol_mapping: dict[str, str] = field(default_factory=dict)  # 品种名映射
    copy_direction: str = "both"            # both | long_only | short_only
    copy_sl_tp: bool = True                 # 是否同步止损止盈
    min_order_interval_ms: int = 500        # 最小下单间隔（毫秒）
    magic: int = 888000                     # 跟单系统专用 magic number


@dataclass
class CopyResult:
    """单次跟单操作的结果"""
    status: ResultStatus
    master_ticket: int
    follower_ticket: int | None = None
    action: str = ""
    error: str | None = None


class OrderMappingStore:
    """
    主订单 → 从订单映射存储器。

    在生产环境中应替换为 PostgreSQL + Redis 实现，
    当前为内存版本用于开发和测试。
    """

    def __init__(self):
        self._mappings: dict[int, dict] = {}

    def add(self, master_ticket: int, follower_ticket: int,
            symbol: str, volume: float) -> None:
        """添加映射"""
        self._mappings[master_ticket] = {
            "master_ticket": master_ticket,
            "follower_ticket": follower_ticket,
            "symbol": symbol,
            "volume": volume,
        }

    def get(self, master_ticket: int) -> dict | None:
        """查询映射"""
        return self._mappings.get(master_ticket)

    def remove(self, master_ticket: int) -> None:
        """删除映射"""
        self._mappings.pop(master_ticket, None)

    def get_all(self) -> list[dict]:
        """获取所有映射"""
        return list(self._mappings.values())

    def get_all_as_dict(self) -> dict[int, dict]:
        """获取所有映射（以 master_ticket 为 key）"""
        return dict(self._mappings)

    def clear(self) -> None:
        """清空所有映射"""
        self._mappings.clear()


class CopyExecutor:
    """
    跟单执行引擎。

    Args:
        config: 跟单配置（比例、品种映射、方向过滤等）
        mapping_store: 主→从订单映射存储
    """

    # 事件排序优先级
    _EVENT_PRIORITY = {
        EventType.CLOSE: 0,
        EventType.MODIFY: 1,
        EventType.OPEN: 2,
    }

    def __init__(self, config: CopyConfig, mapping_store: OrderMappingStore):
        self.config = config
        self.store = mapping_store
        self._last_order_time: float = 0.0

    # ---- 公共 API ----

    def execute(
        self,
        events: list[SignalEvent],
        order_sender: Callable[[dict], tuple[int, Any]],
        symbol_info_provider: Callable[[str], Any],
    ) -> list[CopyResult]:
        """
        执行跟单操作。

        Args:
            events: 信号事件列表（将被排序为 CLOSE → MODIFY → OPEN）
            order_sender: 下单回调，签名为 (request: dict) -> (retcode, result)
            symbol_info_provider: 品种信息回调，签名为 (symbol: str) -> SymbolInfo/dict

        Returns:
            每个事件对应一个 CopyResult
        """
        # 按优先级排序：CLOSE → MODIFY → OPEN
        sorted_events = sorted(
            events, key=lambda e: self._EVENT_PRIORITY.get(e.event_type, 99)
        )

        results = []
        for event in sorted_events:
            try:
                result = self._execute_one(event, order_sender, symbol_info_provider)
            except Exception as e:
                logger.error(f"执行跟单异常 ticket={event.ticket}: {e}", exc_info=True)
                result = CopyResult(
                    status=ResultStatus.FAILED,
                    master_ticket=event.ticket,
                    action=event.event_type.name,
                    error=str(e),
                )
            results.append(result)

        return results

    # ---- 单事件处理 ----

    def _execute_one(
        self,
        event: SignalEvent,
        order_sender: Callable[[dict], tuple[int, Any]],
        symbol_info_provider: Callable[[str], Any],
    ) -> CopyResult:
        """处理单个信号事件"""

        if event.event_type == EventType.OPEN:
            return self._execute_open(event, order_sender, symbol_info_provider)
        elif event.event_type == EventType.CLOSE:
            return self._execute_close(event, order_sender)
        elif event.event_type == EventType.MODIFY:
            return self._execute_modify(event, order_sender)
        else:
            return CopyResult(
                status=ResultStatus.SKIPPED,
                master_ticket=event.ticket,
                error=f"未知事件类型: {event.event_type}",
            )

    # ---- 开仓 ----

    def _execute_open(
        self,
        event: SignalEvent,
        order_sender: Callable,
        symbol_info_provider: Callable,
    ) -> CopyResult:
        new_pos = event.new_state

        # 方向过滤
        if self._should_skip_direction(new_pos.type):
            direction_name = "做多" if new_pos.type == 0 else "做空"
            logger.info(
                f"信号已跳过 ticket={event.ticket} ({direction_name}), "
                f"方向过滤: {self.config.copy_direction}"
            )
            return CopyResult(
                status=ResultStatus.SKIPPED,
                master_ticket=event.ticket,
                action="OPEN",
                error=f"{'买' if new_pos.type == 0 else '卖'}信号已跳过（方向过滤: {self.config.copy_direction}）",
            )

        # 品种映射
        mapped_symbol = self._map_symbol(new_pos.symbol)

        # 获取品种信息
        sym_info = symbol_info_provider(mapped_symbol)
        if sym_info is None:
            return CopyResult(
                status=ResultStatus.FAILED,
                master_ticket=event.ticket,
                action="OPEN",
                error=f"无法获取品种信息: {mapped_symbol}",
            )

        # 获取品种参数（兼容 dict 和对象两种返回格式）
        vol_step = self._get_attr(sym_info, "volume_step", 0.01)
        vol_min = self._get_attr(sym_info, "volume_min", 0.01)
        vol_max = self._get_attr(sym_info, "volume_max", 100.0)

        # 计算跟单手数
        volume = self._calculate_volume(new_pos.volume, vol_step, vol_min, vol_max)

        # 手数过低跳过
        if volume < vol_min:
            logger.info(
                f"手数 {volume} 低于最小手数 {vol_min}, 已跳过 "
                f"ticket={event.ticket}"
            )
            return CopyResult(
                status=ResultStatus.SKIPPED,
                master_ticket=event.ticket,
                action="OPEN",
                error=f"手数 {volume} 低于最小手数 {vol_min}，已跳过",
            )

        # 方向：0=BUY, 1=SELL → MT5 ORDER_TYPE
        order_type = ORDER_TYPE_BUY if new_pos.type == 0 else ORDER_TYPE_SELL

        # 构建下单请求
        request = {
            "action": TRADE_ACTION_DEAL,
            "symbol": mapped_symbol,
            "volume": volume,
            "type": order_type,
            "price": 0.0,           # 市价单
            "deviation": 10,
            "magic": self.config.magic,
            "comment": f"copy_ticket_{event.ticket}",
            "type_time": 0,         # GTC
            "type_filling": 1,      # IOC
        }

        # 如果配置同步 SL/TP 且有值
        if self.config.copy_sl_tp and new_pos.sl > 0:
            request["sl"] = new_pos.sl
        if self.config.copy_sl_tp and new_pos.tp > 0:
            request["tp"] = new_pos.tp

        # 下单间隔控制
        self._enforce_interval()

        # 发送订单
        retcode, result = order_sender(request)
        self._last_order_time = time.perf_counter()

        if retcode == 10009:  # TRADE_RETCODE_DONE
            follower_ticket = self._get_attr(result, "order", 0)
            # 记录映射
            self.store.add(
                master_ticket=event.ticket,
                follower_ticket=follower_ticket,
                symbol=mapped_symbol,
                volume=volume,
            )
            logger.info(
                f"跟单开仓成功: master_ticket={event.ticket} → "
                f"follower_ticket={follower_ticket}, "
                f"symbol={mapped_symbol}, volume={volume}"
            )
            return CopyResult(
                status=ResultStatus.SUCCESS,
                master_ticket=event.ticket,
                follower_ticket=follower_ticket,
                action="OPEN",
            )
        else:
            error_msg = f"下单失败, retcode={retcode}"
            logger.error(f"{error_msg}, ticket={event.ticket}, request={request}")
            return CopyResult(
                status=ResultStatus.FAILED,
                master_ticket=event.ticket,
                action="OPEN",
                error=error_msg,
            )

    # ---- 平仓 ----

    def _execute_close(
        self,
        event: SignalEvent,
        order_sender: Callable,
    ) -> CopyResult:
        mapping = self.store.get(event.ticket)
        if mapping is None:
            return CopyResult(
                status=ResultStatus.SKIPPED,
                master_ticket=event.ticket,
                action="CLOSE",
                error=f"未找到主订单 {event.ticket} 的映射",
            )

        follower_ticket = mapping["follower_ticket"]
        symbol = mapping["symbol"]
        volume = mapping["volume"]
        old_pos = event.old_state

        # 平仓方向：原 BUY → SELL 平，原 SELL → BUY 平
        close_type = ORDER_TYPE_SELL if old_pos.type == 0 else ORDER_TYPE_BUY

        request = {
            "action": TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": close_type,
            "position": follower_ticket,
            "price": 0.0,
            "deviation": 10,
            "magic": self.config.magic,
            "comment": f"copy_close_{event.ticket}",
            "type_time": 0,
            "type_filling": 1,
        }

        self._enforce_interval()
        retcode, result = order_sender(request)
        self._last_order_time = time.perf_counter()

        if retcode == 10009:
            self.store.remove(event.ticket)
            logger.info(
                f"跟单平仓成功: master_ticket={event.ticket}, "
                f"follower_ticket={follower_ticket}"
            )
            return CopyResult(
                status=ResultStatus.SUCCESS,
                master_ticket=event.ticket,
                follower_ticket=follower_ticket,
                action="CLOSE",
            )
        else:
            error_msg = f"平仓失败, retcode={retcode}"
            logger.error(f"{error_msg}, ticket={event.ticket}")
            return CopyResult(
                status=ResultStatus.FAILED,
                master_ticket=event.ticket,
                action="CLOSE",
                error=error_msg,
            )

    # ---- 修改 ----

    def _execute_modify(
        self,
        event: SignalEvent,
        order_sender: Callable,
    ) -> CopyResult:
        if not self.config.copy_sl_tp:
            return CopyResult(
                status=ResultStatus.SKIPPED,
                master_ticket=event.ticket,
                action="MODIFY",
                error="SL/TP 同步未启用",
            )

        mapping = self.store.get(event.ticket)
        if mapping is None:
            return CopyResult(
                status=ResultStatus.SKIPPED,
                master_ticket=event.ticket,
                action="MODIFY",
                error=f"未找到主订单 {event.ticket} 的映射",
            )

        follower_ticket = mapping["follower_ticket"]
        symbol = mapping["symbol"]

        request = {
            "action": TRADE_ACTION_MODIFY,
            "symbol": symbol,
            "position": follower_ticket,
            "sl": event.new_state.sl,
            "tp": event.new_state.tp,
        }

        self._enforce_interval()
        retcode, result = order_sender(request)
        self._last_order_time = time.perf_counter()

        if retcode == 10009:
            logger.info(
                f"跟单修改成功: master_ticket={event.ticket}, "
                f"follower_ticket={follower_ticket}, "
                f"SL={event.new_state.sl}, TP={event.new_state.tp}"
            )
            return CopyResult(
                status=ResultStatus.SUCCESS,
                master_ticket=event.ticket,
                follower_ticket=follower_ticket,
                action="MODIFY",
            )
        else:
            error_msg = f"修改失败, retcode={retcode}"
            logger.error(f"{error_msg}, ticket={event.ticket}")
            return CopyResult(
                status=ResultStatus.FAILED,
                master_ticket=event.ticket,
                action="MODIFY",
                error=error_msg,
            )

    # ---- 辅助方法 ----

    def _calculate_volume(
        self, master_volume: float, vol_step: float,
        vol_min: float, vol_max: float,
    ) -> float:
        """计算跟单手数并截断精度"""
        raw = master_volume * self.config.ratio
        # 按 volume_step 向下截断
        truncated = math.floor(raw / vol_step) * vol_step
        # 保留合理精度
        truncated = round(truncated, 8)
        # 上限截断
        if truncated > vol_max:
            truncated = vol_max
        return truncated

    def _map_symbol(self, symbol: str) -> str:
        """品种名映射"""
        return self.config.symbol_mapping.get(symbol, symbol)

    def _should_skip_direction(self, position_type: int) -> bool:
        """检查方向是否应被过滤"""
        direction = self.config.copy_direction
        if direction == "both":
            return False
        if direction == "long_only" and position_type == 1:  # SELL
            return True
        if direction == "short_only" and position_type == 0:  # BUY
            return True
        return False

    def _enforce_interval(self) -> None:
        """确保两次下单间隔 ≥ min_order_interval_ms"""
        if self._last_order_time == 0.0:
            return
        elapsed_ms = (time.perf_counter() - self._last_order_time) * 1000
        required_ms = self.config.min_order_interval_ms
        if elapsed_ms < required_ms:
            sleep_ms = required_ms - elapsed_ms
            time.sleep(sleep_ms / 1000.0)

    @staticmethod
    def _get_attr(obj: Any, attr: str, default: Any = 0.0) -> Any:
        """安全获取对象属性或字典键"""
        if isinstance(obj, dict):
            return obj.get(attr, default)
        return getattr(obj, attr, default)
