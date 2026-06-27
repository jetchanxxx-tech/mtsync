"""
双终端管理器 — 同时管理信号源和跟单目标两个 MT5 终端。

MetaTrader5 Python 包一次只能连接一个终端，本模块封装了
连接切换逻辑，每个轮询周期自动在信号源和跟单终端之间切换。

使用方式:
    mgr = DualTerminalManager(
        lead_path=r"C:\Program Files\MetaTrader 5\terminal64.exe",
        follower_path=r"D:\MetaTrader 5\terminal64.exe",
    )
    # 读信号源
    positions = mgr.get_lead_positions()
    mgr.disconnect()

    # 执行跟单
    mgr.connect_follower()
    mgr.send_order(request)
    mgr.disconnect()
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional

import MetaTrader5 as mt5

logger = logging.getLogger(__name__)


@dataclass
class TerminalInfo:
    """MT5 终端信息"""
    path: str
    login: int = 0
    server: str = ""
    balance: float = 0.0
    currency: str = "USD"
    connected: bool = False


class DualTerminalManager:
    """
    双终端管理器。

    管理信号源（lead）和跟单目标（follower）两个 MT5 终端，
    在两者之间切换连接。
    """

    def __init__(self, lead_path: str, follower_path: str):
        self.lead = TerminalInfo(path=lead_path)
        self.follower = TerminalInfo(path=follower_path)
        self._active: Optional[str] = None  # "lead" | "follower" | None

    # ---- 信号源（只读） ----

    def connect_lead(self) -> bool:
        """连接信号源终端"""
        return self._connect(self.lead)

    def get_lead_positions(self) -> list:
        """获取信号源当前持仓"""
        if not self._ensure_connected(self.lead):
            return []
        positions = mt5.positions_get()
        return list(positions) if positions else []

    def get_lead_account_info(self) -> dict:
        """获取信号源账户信息"""
        if not self._ensure_connected(self.lead):
            return {}
        acc = mt5.account_info()
        if acc is None:
            return {}
        return {
            "login": acc.login, "server": acc.server,
            "balance": acc.balance, "equity": acc.equity,
            "currency": acc.currency, "leverage": acc.leverage,
        }

    # ---- 跟单目标（读写） ----

    def connect_follower(self) -> bool:
        """连接跟单目标终端"""
        return self._connect(self.follower)

    def get_follower_positions(self) -> list:
        """获取跟单目标当前持仓"""
        if not self._ensure_connected(self.follower):
            return []
        positions = mt5.positions_get()
        return list(positions) if positions else []

    def get_follower_account_info(self) -> dict:
        """获取跟单目标账户信息"""
        if not self._ensure_connected(self.follower):
            return {}
        acc = mt5.account_info()
        if acc is None:
            return {}
        return {
            "login": acc.login, "server": acc.server,
            "balance": acc.balance, "equity": acc.equity,
            "currency": acc.currency, "leverage": acc.leverage,
        }

    def send_order(self, request: dict) -> tuple[int, Any]:
        """在跟单终端发送订单"""
        if not self._ensure_connected(self.follower):
            return -1, None
        result = mt5.order_send(request)
        if result is None:
            err = mt5.last_error()
            logger.error(f"下单失败: {err}")
            return -1, err
        return result.retcode, result

    def order_check(self, request: dict):
        """验证订单参数（不实际下单）"""
        if not self._ensure_connected(self.follower):
            return None
        return mt5.order_check(request)

    def get_symbol_info(self, symbol: str):
        """获取品种信息（当前连接的终端）"""
        return mt5.symbol_info(symbol)

    def get_symbols(self) -> list:
        """获取品种列表（当前连接的终端）"""
        symbols = mt5.symbols_get()
        return list(symbols) if symbols else []

    # ---- 连接管理 ----

    def disconnect(self) -> None:
        """断开当前连接"""
        if self._active is not None:
            mt5.shutdown()
            self.lead.connected = False
            self.follower.connected = False
            self._active = None

    def _connect(self, terminal: TerminalInfo) -> bool:
        """连接到指定终端（自动断开旧连接）"""
        if self._active is not None:
            mt5.shutdown()
            self.lead.connected = False
            self.follower.connected = False

        if not mt5.initialize(path=terminal.path):
            err = mt5.last_error()
            logger.error(f"无法连接 MT5 终端 {terminal.path}: {err}")
            terminal.connected = False
            self._active = None
            return False

        # 读取账户信息
        acc = mt5.account_info()
        if acc is not None:
            terminal.login = acc.login
            terminal.server = acc.server
            terminal.balance = acc.balance
            terminal.currency = acc.currency

        terminal.connected = True
        self._active = "lead" if terminal is self.lead else "follower"
        logger.info(f"已连接 {terminal.path} → 账户 {terminal.login}")
        return True

    def _ensure_connected(self, terminal: TerminalInfo) -> bool:
        """确保已连接到指定终端，未连接则自动连接"""
        if self._active is not None and terminal.connected:
            return True
        return self._connect(terminal)

    def get_status(self) -> dict:
        """获取双终端状态"""
        return {
            "active": self._active,
            "lead": {
                "path": self.lead.path,
                "login": self.lead.login,
                "server": self.lead.server,
                "connected": self.lead.connected,
            },
            "follower": {
                "path": self.follower.path,
                "login": self.follower.login,
                "server": self.follower.server,
                "connected": self.follower.connected,
            },
        }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False
