#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例适配器：自包含迷你交易所（演示"交易型数据源"的完整契约实现）。

特点：
- affects_price=True —— 你的每一笔市价单都会真实推动价格（按成交量加权冲击），
  这就是"完整体验"与"只看行情的模拟"的本质区别；
- funds_model='platform' —— 资金由源侧账户决定（本示例内置账户，初始 1,000,000）；
- 零外部依赖、零网络请求：纯本地随机游走 + 订单冲击，适合作为适配器开发模板。

使用：PAPERLAB_ADAPTER=examples/adapters/tiny_exchange_adapter.py python3 exchange/server.py
规格说明：docs/ADAPTER-SPEC.md（本文件即该规格的参考实现）。
"""
import random
import threading
import time

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "exchange"))
from sources import MarketSource  # noqa: E402


class TinyExchangeSource(MarketSource):
    id = "tiny"
    label = "迷你交易所（示例：订单会推动价格）"

    SYMBOLS = {  # 代码: (名称, 起始价, 日波动率)
        "T1001": ("示例能源", 25.0, 0.03),
        "T1002": ("示例科技", 68.0, 0.045),
        "T1003": ("示例消费", 12.5, 0.02),
    }

    def __init__(self, initial_cash=1_000_000.0, seed=7):
        self.id = "tiny"
        self.label = "迷你交易所（示例：订单会推动价格）"
        self._lock = threading.Lock()
        self._rng = random.Random(seed)
        self._cash = float(initial_cash)
        self._initial_cash = float(initial_cash)
        self._px = {c: cfg[1] for c, cfg in self.SYMBOLS.items()}
        self._pos = {}   # code -> shares（可负=做空）
        self._orders = {}
        self._trades = []
        self._oid = 0

    # ---- 契约：能力声明 ----
    def capabilities(self):
        return {"affects_price": True,          # 关键：订单推动价格
                "funds_model": "platform",      # 资金由源侧账户决定
                "trading": True,
                "sessions": "7x24（示例源不休市）",
                "builtin": False, "example": True}

    # ---- 契约：行情 ----
    def quotes(self, codes):
        with self._lock:
            for c in self._px:                 # 每次询价让价格随机游走一步
                self._px[c] = round(
                    max(0.5, self._px[c] * (1 + self._rng.gauss(0, self.SYMBOLS[c][2] / 30))),
                    3)
            return {c: self._quote(c) for c in codes if c in self._px}

    def kline(self, code, limit=120):
        code = str(code).upper()
        if code not in self.SYMBOLS:
            return []
        px, vol = self.SYMBOLS[code][1], self.SYMBOLS[code][2]
        rng = random.Random(hash(code) & 0xFFFF)
        rows, prev = [], None
        for i in range(int(limit)):
            day = time.strftime("%Y-%m-%d", time.localtime(
                time.time() - 86400 * (int(limit) - i)))
            px = max(0.5, px * (1 + rng.gauss(0.0003, vol)))
            o, c = round(px * (1 - abs(rng.gauss(0, vol / 3))), 3), round(px, 3)
            rows.append({"date": day, "open": o, "close": c,
                         "high": round(max(o, c) * (1 + abs(rng.gauss(0, vol / 4))), 3),
                         "low": round(min(o, c) * (1 - abs(rng.gauss(0, vol / 4))), 3),
                         "volume": rng.randint(10, 90) * 10000,
                         "amount": None, "pct": None if prev is None else
                         round((c / prev - 1) * 100, 2)})
            prev = c
        return rows

    # ---- 契约：交易（trading=True 必须实现）----
    def place_order(self, side, code, shares, price=None):
        side, code, shares = str(side).lower(), str(code).upper(), int(shares)
        with self._lock:
            if code not in self._px:
                return {"ok": False, "error": "unknown_code"}
            if shares <= 0:
                return {"ok": False, "error": "bad_shares"}
            px = self._px[code]
            fill = px if not price else min(max(float(price), px * 0.95), px * 1.05)
            impact = 0.0004 * (shares / 1000.0)   # 每1000股推动4bp（买向上/卖向下）
            if side == "buy":
                if fill * shares > self._cash + 1e-6:
                    return {"ok": False, "error": "insufficient_cash",
                            "detail": "示例源不允许透支"}
                self._cash -= fill * shares
                self._pos[code] = self._pos.get(code, 0) + shares
                self._px[code] = round(fill * (1 + impact), 3)
            elif side in ("sell", "short"):
                self._cash += fill * shares
                self._pos[code] = self._pos.get(code, 0) - shares
                self._px[code] = round(fill * (1 - impact), 3)
            else:
                return {"ok": False, "error": "bad_side"}
            self._oid += 1
            tr = {"oid": self._oid, "side": side, "code": code, "shares": shares,
                  "price": fill, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "px_after": self._px[code]}
            self._trades.append(tr)
            return {"ok": True, **tr}

    def cancel_order(self, oid):
        return {"ok": True, "note": "示例源即时成交，无挂单可撤"}

    def positions(self):
        with self._lock:
            return [{"code": c, "name": self.SYMBOLS[c][0], "shares": s,
                     "price": self._px[c], "market_value": round(s * self._px[c], 2)}
                    for c, s in self._pos.items() if s]

    def account(self):
        with self._lock:
            mv = sum(s * self._px[c] for c, s in self._pos.items())
            return {"cash": round(self._cash, 2), "market_value": round(mv, 2),
                    "total": round(self._cash + mv, 2),
                    "initial_cash": self._initial_cash,
                    "pnl": round(self._cash + mv - self._initial_cash, 2)}

    # ---- 内部 ----
    def _quote(self, c):
        prev = round(self._px[c] / (1 + self._rng.gauss(0, 0.004)), 3)
        return {"code": c, "name": self.SYMBOLS[c][0], "price": self._px[c],
                "pct": round((self._px[c] / prev - 1) * 100, 2), "change": None,
                "open": prev, "high": self._px[c], "low": prev,
                "prev_close": prev, "volume": self._rng.randint(1, 99) * 1000,
                "amount": None, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}


SOURCE = TinyExchangeSource()

if __name__ == "__main__":  # 自测：下单→价格被推动
    s = TinyExchangeSource()
    print("账户:", s.account())
    q0 = s.quotes(["T1002"])["T1002"]["price"]
    for i in range(3):
        r = s.place_order("buy", "T1002", 5000)
        print("买入5000股 @", r.get("price"), "→ 推动后价", r.get("px_after"))
    q1 = s.quotes(["T1002"])["T1002"]["price"]
    print("价格: %.3f → %.3f (订单冲击可见: %+.2f%%)" % (q0, q1, (q1 / q0 - 1) * 100))
    print("持仓:", s.positions())
    print("账户:", s.account())
    print("K线:", len(s.kline("T1001", 10)), "根")
