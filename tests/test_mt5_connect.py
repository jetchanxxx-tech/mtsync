"""
MT5 连接验证测试 — 验证与 MT5 终端的基础交互功能。

运行方式:
    pytest tests/test_mt5_connect.py -v
    或
    python -m pytest tests/test_mt5_connect.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import MetaTrader5 as mt5
from src.mt5_connector import MT5Connector


@pytest.fixture
def conn():
    """创建 MT5 连接 fixture，测试结束后自动关闭"""
    c = MT5Connector()
    yield c
    c.shutdown()


class TestMT5Connection:
    """MT5 连接测试"""

    def test_initialize(self, conn):
        """测试 MT5 初始化连接"""
        assert conn.connected, "MT5 应成功连接（请确认 MT5 终端已运行）"

    def test_terminal_version(self, conn):
        """测试获取终端版本"""
        info = conn.get_terminal_info()
        assert info, "应能获取终端信息"
        assert "community_account" in info, "终端信息应包含 community_account"

    def test_account_info(self, conn):
        """测试获取账户信息"""
        acc = conn.get_account_info()
        assert acc is not None, "应能获取账户信息"
        assert acc.login > 0, f"登录账号应 > 0，实际: {acc.login}"
        assert acc.balance > 0, f"余额应 > 0，实际: {acc.balance}"
        assert acc.currency == "USD", f"币种应为 USD，实际: {acc.currency}"
        print(f"\n  账号: {acc.login}, 服务器: {acc.server}, 余额: {acc.balance}")

    def _get_eurusd_symbol(self, conn) -> str:
        """自适应查找 EURUSD 品种名（不同经纪商可能有不同后缀如 EURUSD_ / EURUSD.pro / EURUSD）"""
        symbols = conn.get_symbols()
        symbol_names = {s.name for s in symbols}
        # 按优先级查找
        for candidate in ["EURUSD", "EURUSD_", "EURUSD.pro"]:
            if candidate in symbol_names:
                return candidate
        # 前缀匹配
        for name in symbol_names:
            if name.startswith("EURUSD"):
                return name
        pytest.skip("未找到 EURUSD 相关品种")

    def test_get_symbols(self, conn):
        """测试获取品种列表"""
        symbols = conn.get_symbols()
        assert len(symbols) > 0, "品种列表不应为空"
        print(f"\n  可用品种数: {len(symbols)}")
        # 检查主要品种（自适应后缀）
        eurusd = self._get_eurusd_symbol(conn)
        assert eurusd is not None
        print(f"  EURUSD 品种名: {eurusd}")

    def test_get_tick(self, conn):
        """测试获取实时报价"""
        sym = self._get_eurusd_symbol(conn)
        tick = conn.get_symbol_tick(sym)
        assert tick is not None, f"应能获取 {sym} 报价"
        assert tick.bid > 0, f"bid 应 > 0，实际: {tick.bid}"
        assert tick.ask > 0, f"ask 应 > 0，实际: {tick.ask}"
        assert tick.ask >= tick.bid, f"ask({tick.ask}) 应 >= bid({tick.bid})"
        print(f"\n  {sym} bid={tick.bid}, ask={tick.ask}, spread={tick.ask - tick.bid}")

    def test_symbol_info(self, conn):
        """测试获取品种规格"""
        sym = self._get_eurusd_symbol(conn)
        info = conn.get_symbol_info(sym)
        assert info is not None, f"应能获取 {sym} 品种信息"
        assert info.digits >= 3, f"digits 应 >= 3，实际: {info.digits}"
        assert info.volume_step >= 0.001, f"volume_step 应 >= 0.001"
        print(f"\n  {sym}: digits={info.digits}, vol_min={info.volume_min}, "
              f"vol_max={info.volume_max}, vol_step={info.volume_step}")

    def test_positions_empty(self, conn):
        """测试获取持仓（预期空仓）"""
        positions = conn.get_positions()
        assert positions is not None, "positions_get() 不应返回 None"
        pos_count = len(positions) if positions else 0
        print(f"\n  当前持仓数: {pos_count}")


class TestMT5OrderValidation:
    """MT5 订单请求结构验证（不实际下单）"""

    def test_build_market_buy_request(self, conn):
        """测试构造市价买单请求"""
        request = {
            "action": mt5.TRADE_ACTION_DEAL,  # type: ignore
            "symbol": "EURUSD_",
            "volume": 0.01,
            "type": mt5.ORDER_TYPE_BUY,        # type: ignore
            "price": 0.0,                       # 市价单填 0
            "deviation": 10,
            "magic": 123456,
            "comment": "copy_trade_test",
            "type_time": mt5.ORDER_TIME_GTC,   # type: ignore
            "type_filling": mt5.ORDER_FILLING_IOC,  # type: ignore
        }
        assert request["action"] == 1  # mt5.TRADE_ACTION_DEAL
        assert request["symbol"] == "EURUSD_"
        assert request["volume"] == 0.01

    # 使用 pytest.skip 跳过实际下单测试
    @pytest.mark.skip(reason="实际下单测试需手动开启")
    def test_real_market_order(self, conn):
        """⚠️ 实际市价下单测试 — 需要手动解除 skip"""
        import MetaTrader5 as mt5
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": "EURUSD_",
            "volume": 0.01,
            "type": mt5.ORDER_TYPE_BUY,
            "price": 0.0,
            "deviation": 10,
            "magic": 999888,
            "comment": "copy_trade_test_real",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        retcode, result = conn.order_send(request)
        print(f"\n  下单结果: retcode={retcode}")
        assert retcode == 10009, f"市价单应成功 (10009)，实际: {retcode}"


# ---- 独立运行 ----
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
