# -*- coding: utf-8 -*-
"""离线单元测试：港股/期货符号规范化。运行：python3 -m unittest discover -s tests -v"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "exchange"))

import unittest

from hk_marketdata import norm_hk, to_secid
from fut_marketdata import norm_fut


class TestNormHK(unittest.TestCase):
    def test_variants(self):
        for raw in ("hk00700", "00700", "0700", "116.00700", "HK00700", " hk00700 "):
            self.assertEqual(norm_hk(raw), "hk00700", raw)

    def test_invalid(self):
        for raw in ("", "sh600519", "600519", "123456", "hk", "abc", None):
            self.assertIsNone(norm_hk(raw), raw)

    def test_zfill_semantics(self):
        # 1-5位纯数字统一左补零到5位（口语"700"=hk00700）
        self.assertEqual(norm_hk("700"), "hk00700")
        self.assertEqual(norm_hk("0070"), "hk00070")

    def test_secid(self):
        self.assertEqual(to_secid("hk00700"), "116.00700")
        self.assertEqual(to_secid("hk09988"), "116.09988")


class TestNormFut(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(norm_fut("RB0"), "RB0")
        self.assertEqual(norm_fut("rb0"), "RB0")
        self.assertEqual(norm_fut("nf_rb0"), "RB0")
        self.assertEqual(norm_fut("cu0"), "CU0")

    def test_alias(self):
        self.assertEqual(norm_fut("螺纹"), "RB0")
        self.assertEqual(norm_fut("原油"), "SC0")

    def test_invalid(self):
        for raw in ("", "RB1", "RB", "600519", None):
            self.assertIsNone(norm_fut(raw), raw)

    def test_generic_letters(self):
        # 契约通用：任意1-2字母主连都被规范化（查询不到数据属正常）
        self.assertEqual(norm_fut("xx0"), "XX0")


if __name__ == "__main__":
    unittest.main()
