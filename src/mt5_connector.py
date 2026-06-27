"""
MT5 终端连接器 — 封装 MetaTrader5 Python 官方库的初始化、数据获取、订单操作。

用法:
    from src.mt5_connector import MT5Connector

    conn = MT5Connector()  # 连接当前运行的 MT5 终端
    # 或指定路径
    conn = MT5Connector(path=r"C:\\Program Files\\MetaTrader 5\\terminal64.exe")

    if conn.connected:
        acc = conn.get_account_info()
        print(f"账户 {acc.login}, 余额 {acc.balance}")

    conn.shutdown()
"""

import logging
from dataclasses import dataclass
from typing import Optional

import MetaTrader5 as mt5

logger = logging.getLogger(__name__)


@dataclass
class AccountInfo:
    """MT5 账户信息"""
    login: int
    server: str
    balance: float
    equity: float
    margin: float
    margin_free: float
    leverage: int
    currency: str
    trade_mode: int       # 0=DEMO, 1=REAL, 2=CONTEST
    name: str = ""


@dataclass
class SymbolInfo:
    """MT5 品种信息"""
    name: str
    digits: int
    trade_mode: int       # 0=禁用, 1=仅多头, 2=仅空头, 4=全部允许
    spread: int
    volume_min: float
    volume_max: float
    volume_step: float


class MT5Connector:
    """MT5 终端连接器 — 封装 MetaTrader5 官方库"""

    def __init__(self, path: Optional[str] = None):
        """
        初始化并连接 MT5 终端。

        Args:
            path: MT5 终端 .exe 路径，None 则自动检测当前运行的终端
        """
        self._path = path
        self._connected = False

        if path:
            if not mt5.initialize(path=path):
                err = mt5.last_error()
                logger.error(f"MT5 初始化失败 (path={path}): {err}")
                return
        else:
            if not mt5.initialize():
                err = mt5.last_error()
                logger.error(f"MT5 初始化失败: {err}")
                return

        self._connected = True
        version = mt5.version()
        logger.info(f"MT5 已连接, 版本: {version[0]}.{version[1]} ({version[2]})")

    @property
    def connected(self) -> bool:
        return self._connected

    # ---- 账户信息 ----

    def get_account_info(self) -> Optional[AccountInfo]:
        """获取当前登录账户信息"""
        if not self._connected:
            return None
        acc = mt5.account_info()
        if acc is None:
            logger.error(f"获取账户信息失败: {mt5.last_error()}")
            return None
        return AccountInfo(
            login=acc.login,
            server=acc.server,
            balance=acc.balance,
            equity=acc.equity,
            margin=acc.margin,
            margin_free=acc.margin_free,
            leverage=acc.leverage,
            currency=acc.currency,
            trade_mode=acc.trade_mode,
            name=acc.name,
        )

    def get_terminal_info(self) -> dict:
        """获取终端信息"""
        info = mt5.terminal_info()
        if info is None:
            return {}
        return info._asdict()

    # ---- 品种信息 ----

    def get_symbols(self) -> list[SymbolInfo]:
        """获取所有可用品种"""
        symbols = mt5.symbols_get()
        if symbols is None:
            return []
        result = []
        for s in symbols:
            result.append(SymbolInfo(
                name=s.name,
                digits=s.digits,
                trade_mode=s.trade_mode,
                spread=s.spread,
                volume_min=s.volume_min,
                volume_max=s.volume_max,
                volume_step=s.volume_step,
            ))
        return result

    def get_symbol_info(self, symbol: str):
        """获取单个品种信息"""
        return mt5.symbol_info(symbol)

    def get_symbol_tick(self, symbol: str):
        """获取品种实时报价"""
        return mt5.symbol_info_tick(symbol)

    # ---- 持仓 ----

    def get_positions(self, symbol: str = ""):
        """获取当前持仓列表"""
        return mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()

    # ---- 挂单 ----

    def get_orders(self, symbol: str = ""):
        """获取当前挂单列表"""
        return mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()

    # ---- 历史 ----

    def get_history_deals(self, date_from, date_to):
        """获取历史成交"""
        return mt5.history_deals_get(date_from, date_to)

    def get_history_orders(self, date_from, date_to):
        """获取历史订单"""
        return mt5.history_orders_get(date_from, date_to)

    # ---- 下单 ----

    def order_send(self, request: dict):
        """
        发送交易指令。
        返回 (retcode, result) 元组。
        retcode 含义：10009=成功, 10004=需重新报价, 详见 MT5 文档
        """
        result = mt5.order_send(request)
        if result is None:
            err = mt5.last_error()
            logger.error(f"下单失败: {err}")
            return None, err
        return result.retcode, result

    # ---- 断开 ----

    def shutdown(self):
        """断开与 MT5 终端的连接"""
        if self._connected:
            mt5.shutdown()
            self._connected = False
            logger.info("MT5 已断开")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown()
        return False
