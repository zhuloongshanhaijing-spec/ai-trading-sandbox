# -*- coding: utf-8 -*-
"""离线单元测试：示例交易型源（订单冲击/禁透支/账户守恒）。运行：python3 -m unittest discover -s tests -v"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "exchange"))
sys.path.insert(0, os.path.join(ROOT, "examples", "adapters"))

import unittest

from tiny_exchange_adapter import TinyExchangeSource


class TestTinyExchange(unittest.TestCase):
    def setUp(self):
        self.src = TinyExchangeSource(initial_cash=1_000_000, seed=7)

    def test_buy_pushes_price_up(self):
        q0 = self.src.quotes(["T1002"])["T1002"]["price"]
        last = None
        for _ in range(3):
            r = self.src.place_order("buy", "T1002", 3000)
            self.assertTrue(r["ok"])
            last = r
        q1 = self.src.quotes(["T1002"])["T1002"]["price"]
        self.assertGreater(q1, q0)          # 买盘冲击向上
        self.assertGreater(last["px_after"], last["price"] or q0)

    def test_no_overdraft(self):
        r = self.src.place_order("buy", "T1002", 10_000_000)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "insufficient_cash")

    def test_account_conservation(self):
        a0 = self.src.account()
        r = self.src.place_order("buy", "T1001", 1000)
        a1 = self.src.account()
        # 现金减少额 == 成交名义额（无费用模型）
        self.assertAlmostEqual(a0["cash"] - a1["cash"], 1000 * r["price"], places=1)
        pos = self.src.positions()
        self.assertEqual(len(pos), 1)
        self.assertEqual(pos[0]["shares"], 1000)
        # 买入冲击推高价格 → 总资产不降（纸面瞬时浮盈）
        self.assertGreaterEqual(a1["total"], a0["total"] - 0.01)

    def test_sell_reduces_position(self):
        self.src.place_order("buy", "T1003", 500)
        r = self.src.place_order("sell", "T1003", 500)
        self.assertTrue(r["ok"])
        self.assertEqual(self.src.positions(), [])

    def test_kline_shape(self):
        rows = self.src.kline("T1001", 30)
        self.assertEqual(len(rows), 30)
        for row in rows:
            for k in ("date", "open", "close", "high", "low", "volume"):
                self.assertIn(k, row)
        self.assertEqual(rows, sorted(rows, key=lambda x: x["date"]))  # 升序


if __name__ == "__main__":
    unittest.main()
