# -*- coding: utf-8 -*-
"""离线单元测试：账本T+1语义与交易所submit守卫（monkeypatch行情，零网络）。
运行：python3 -m unittest discover -s tests -v"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "exchange"))

import unittest

from ledger import Ledger


class TestLedgerT1(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(prefix="pl_led_"), "t.db")
        self.led = Ledger(self.db, initial_cash=1_000_000.0, t_plus_one=True)

    def test_buy_creates_pending(self):
        cash_after = 1_000_000 - 100 * 1000.0 - 5.0
        self.led.execute("buy", "sh600519", "测试", 100, 1000.0, 5.0, 0.0, cash_after)
        pos = self.led.position("sh600519")
        self.assertEqual(pos["shares"], 100)
        self.assertEqual(pos["available"], 0)     # T+1：当日不可卖
        self.assertEqual(pos["pending"], 100)

    def test_settle_same_day_is_zero(self):
        cash_after = 1_000_000 - 100 * 1000.0 - 5.0
        self.led.execute("buy", "sh600519", "测试", 100, 1000.0, 5.0, 0.0, cash_after)
        self.assertEqual(self.led.settle_t1(), 0)  # 当日结算为0

    def test_t0_mode_available_immediately(self):
        led = Ledger(os.path.join(tempfile.mkdtemp(prefix="pl_led0_"), "t0.db"),
                     initial_cash=1_000_000.0, t_plus_one=False)
        led.execute("buy", "sh600519", "测试", 100, 1000.0, 5.0, 0.0,
                    1_000_000 - 100 * 1000.0 - 5.0)
        pos = led.position("sh600519")
        self.assertEqual(pos["available"], 100)
        self.assertEqual(pos["pending"], 0)

    def test_account_initial(self):
        acct = self.led.account()
        self.assertEqual(acct["cash"], 1_000_000.0)


class TestServerSubmitGuard(unittest.TestCase):
    """T+1拒绝与t_plus_one=false放行：Exchange.submit层（monkeypatch行情）。"""

    def _exchange(self, t1):
        import server
        old = server.DEFAULT_CONFIG.get("t_plus_one")
        server.DEFAULT_CONFIG["t_plus_one"] = t1  # config在__init__读取→Ledger
        from server import Exchange
        ex = Exchange(db_path=os.path.join(
            tempfile.mkdtemp(prefix="pl_srv_"), "srv.db"))
        if old is not None:
            server.DEFAULT_CONFIG["t_plus_one"] = old
        fake = {"sh600519": {"code": "sh600519", "name": "测试", "price": 1000.0,
                             "pct": 0.0, "prev_close": 1000.0}}
        ex.md.quotes = lambda codes: {c: fake[c] for c in codes if c in fake}
        return ex

    def test_t1_same_day_sell_refused(self):
        ex = self._exchange(True)
        ok1, _, _ = ex.submit({"action": "buy", "code": "600519", "shares": 100})
        self.assertTrue(ok1)
        ok2, payload, _ = ex.submit({"action": "sell", "code": "600519", "shares": 100})
        self.assertFalse(ok2)
        self.assertIn("可卖", str(payload))

    def test_t0_same_day_sell_allowed(self):
        ex = self._exchange(False)
        ok1, _, _ = ex.submit({"action": "buy", "code": "600519", "shares": 100})
        self.assertTrue(ok1)
        ok2, payload2, _ = ex.submit({"action": "sell", "code": "600519", "shares": 100})
        self.assertTrue(ok2, payload2)


if __name__ == "__main__":
    unittest.main()
