# -*- coding: utf-8 -*-
"""美股行情网关：新浪 gb_ 批量接口（免费、实时、含盘前盘后最新价）。stdlib only。

字段（gb_aapl 观察确认）：
[0]名称 [1]现价 [2]涨跌幅% [3]行情时间(北京) [4]涨跌额 [5]开盘 [6]最高 [7]最低
[8]52周高 [9]52周低 [10]成交量(股) ... [26附近]昨收（不稳，用 price/(1+pct/100) 反推）
"""
import re
import threading
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
      "Referer": "https://finance.sina.com.cn"}


def norm_us(raw):
    """AAPL / aapl / usAAPL / gb_aapl -> 'AAPL'（大写ticker）。"""
    if not raw:
        return None
    s = str(raw).strip().upper().lstrip("GB").lstrip("US").replace("_", "")
    if s != s.upper():
        return None
    if re.fullmatch(r"[A-Z.\-]{1,8}", s):
        return s
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class USMarketData:
    def __init__(self, ttl=3.0):
        self.ttl = ttl
        self._cache = {}
        self._lock = threading.Lock()

    def quotes(self, symbols):
        """批量行情；返回 {SYM: quote}。"""
        out = {}
        todo = []
        now = time.time()
        with self._lock:
            for s in symbols:
                s = norm_us(s)
                if not s:
                    continue
                hit = self._cache.get(s)
                if hit and hit[0] > now:
                    out[s] = hit[1]
                else:
                    todo.append(s)
        if not todo:
            return out
        url = "https://hq.sinajs.cn/list=" + ",".join("gb_" + s.lower() for s in todo)
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=8) as r:
                text = r.read().decode("gbk", "replace")
            for line in text.splitlines():
                m = re.match(r'var hq_str_gb_(\w+)="(.*)";?\s*$', line.strip())
                if not m:
                    continue
                sym = m.group(1).upper()
                p = m.group(2).split(",")
                if len(p) < 11:
                    continue
                price, pct = _num(p[1]), _num(p[2])
                if price is None:
                    continue
                prev = price / (1 + pct / 100) if pct is not None else None
                q = {"code": sym, "name": p[0] or sym, "price": price, "pct": pct,
                     "change": _num(p[4]), "open": _num(p[5]), "high": _num(p[6]),
                     "low": _num(p[7]), "prev_close": round(prev, 4) if prev else None,
                     "volume": _num(p[10]), "ts": p[3] if len(p) > 3 else ""}
                out[sym] = q
        except Exception:
            pass
        expire = time.time() + self.ttl
        with self._lock:
            for s, q in out.items():
                self._cache[s] = (expire, q)
        return out


if __name__ == "__main__":
    md = USMarketData()
    for s, q in md.quotes(["AAPL", "MSFT", "NVDA", "TSLA", "QQQ"]).items():
        print(s, q["name"], q["price"], str(q["pct"]) + "%", q["ts"], "prev=", q["prev_close"])
