# -*- coding: utf-8 -*-
"""离线单元测试：换源闸门/断代/适配器加载。运行：python3 -m unittest discover -s tests -v"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "exchange"))

import unittest

os.environ["PAPERLAB_ADAPTER"] = os.path.join(
    ROOT, "examples", "adapters", "tiny_exchange_adapter.py")

from sources import SourceManager, load_custom_adapter  # noqa: E402


class TestAdapterLoader(unittest.TestCase):
    def test_load_example(self):
        src, err = load_custom_adapter(os.environ["PAPERLAB_ADAPTER"])
        self.assertIsNone(err)
        self.assertEqual(src.capabilities().get("affects_price"), True)
        self.assertEqual(src.capabilities().get("trading"), True)
        self.assertEqual(src.capabilities().get("funds_model"), "platform")

    def test_load_missing(self):
        src, err = load_custom_adapter("/nonexistent/adapter.py")
        self.assertIsNone(src)
        self.assertIn("不存在", err)


class TestSwitchGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pl_src_")
        self.sm = SourceManager(state_path=os.path.join(self.tmp, "state.json"))

    def test_refuse_with_positions(self):
        r = self.sm.switch("hk", lambda: {"positions": 2, "open_orders": 0})
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "positions_open")
        self.assertEqual(self.sm.current, "cn")  # 状态未变

    def test_refuse_with_open_orders(self):
        r = self.sm.switch("hk", lambda: {"positions": 0, "open_orders": 1})
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "positions_open")

    def test_unknown_source(self):
        r = self.sm.switch("mars", lambda: {"positions": 0, "open_orders": 0})
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "unknown_source")

    def test_flat_switch_and_era(self):
        r = self.sm.switch("hk", lambda: {"positions": 0, "open_orders": 0})
        self.assertTrue(r["ok"])
        self.assertEqual(self.sm.current, "hk")
        eras = self.sm.eras()
        self.assertEqual(len(eras), 1)
        self.assertEqual(eras[0]["from"], "cn")
        self.assertEqual(eras[0]["to"], "hk")
        # 状态持久化：新实例读到同一状态
        sm2 = SourceManager(state_path=os.path.join(self.tmp, "state.json"))
        self.assertEqual(sm2.current, "hk")

    def test_custom_source_in_status(self):
        st = self.sm.status()
        self.assertIn("custom", st["available"])
        self.assertEqual(st["available"]["custom"]["caps"].get("affects_price"), True)


if __name__ == "__main__":
    unittest.main()
