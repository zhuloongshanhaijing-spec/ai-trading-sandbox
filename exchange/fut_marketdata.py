# -*- coding: utf-8 -*-
"""国内期货行情网关：新浪公开接口（实时 nf_ + 日K InnerFutures），带内存缓存。仅标准库。

代码规范：内部统一大写连续合约符号，如 'RB0'（螺纹主连）、'CU0'（铜主连）；
兼容 'rb0' / 'nf_rb0' 输入。常见主连：RB0 HC0 I0 M0 TA0 MA0 PP0 EG0
SC0(原油) FU0 LU0 AU0 AG0 CU0 AL0 ZN0 NI0 SN0 P0 OI0 RM0 CF0 SR0 JD0 AP0等。
数据源：hq.sinajs.cn（实时）、stock2.finance.sina.com.cn（日K）。
字段（nf_RB0 实测）：0名称 1时间 2开 3高 4低 5买 6卖 7最新 8结算 9昨结 12持仓 13成交量。
"""
import json
import re
import threading
import time
import urllib.request

UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://finance.sina.com.cn",
}

SYMBOL_ALIASES = {  # 常见中文/俗称 -> 主连符号
    "螺纹": "RB0", "热卷": "HC0", "铁矿": "I0", "豆粕": "M0", "pta": "TA0",
    "甲醇": "MA0", "原油": "SC0", "燃油": "FU0", "黄金": "AU0", "白银": "AG0",
    "铜": "CU0", "铝": "AL0", "锌": "ZN0", "镍": "NI0", "棕榈": "P0",
    "菜油": "OI0", "菜粕": "RM0", "棉花": "CF0", "白糖": "SR0", "鸡蛋": "JD0",
}


def norm_fut(raw):
    """各种输入 -> 'RB0' 风格主连符号；无效返回 None。"""
    if not raw:
        return None
    s = str(raw).strip().lower().replace(" ", "").replace("_", "")
    if s.startswith("nf"):
        s = s[2:]
    if s in SYMBOL_ALIASES:
        return SYMBOL_ALIASES[s]
    m = re.fullmatch(r"([a-z]{1,2})0", s)
    if m:
        return m.group(1).upper() + "0"
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class FutMarketData:
    def __init__(self, quote_ttl=3.0, kline_ttl=300.0):
        self.quote_ttl, self.kline_ttl = quote_ttl, kline_ttl
        self._qcache, self._kcache = {}, {}
        self._lock = threading.Lock()

    def quotes(self, codes):
        """批量实时（新浪 nf_）；返回 {RB0: quote_dict}。"""
        out, todo, now = {}, [], time.time()
        with self._lock:
            for c in codes:
                c = norm_fut(c)
                if not c:
                    continue
                hit = self._qcache.get(c)
                if hit and hit[0] > now:
                    out[c] = hit[1]
                else:
                    todo.append(c)
        if todo:
            try:
                url = "https://hq.sinajs.cn/list=" + ",".join("nf_" + c for c in todo)
                req = urllib.request.Request(url, headers=UA)
                with urllib.request.urlopen(req, timeout=6) as resp:
                    text = resp.read().decode("gbk", "replace")
                for line in text.splitlines():
                    m = re.match(r'var hq_str_nf_(\w+)="(.*)"', line.strip())
                    if not m:
                        continue
                    sym, payload = m.group(1).upper(), m.group(2)
                    p = payload.split(",")
                    if len(p) < 18 or _num(p[7]) is None:
                        continue
                    price, prev = _num(p[7]), _num(p[9])  # 最新 / 昨结
                    out[sym] = {
                        "code": sym, "name": p[0] or sym, "price": price,
                        "pct": round((price / prev - 1) * 100, 2) if prev else None,
                        "change": round(price - prev, 3) if prev else None,
                        "open": _num(p[2]), "high": _num(p[3]), "low": _num(p[4]),
                        "prev_close": prev,
                        "volume": _num(p[13]),          # 成交量（手，双边计）
                        "amount": None,
                        "open_interest": _num(p[12]),   # 持仓量
                        "settle": _num(p[8]),           # 今日结算（盘中为动态）
                        "ts": str(p[17]) if p[17] else time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
            except Exception:
                pass
        expire = time.time() + self.quote_ttl
        with self._lock:
            for c, q in out.items():
                self._qcache[c] = (expire, q)
        return out

    def kline(self, code, limit=120):
        """日K（新浪 InnerFutures，主连）；返回升序列表。"""
        code = norm_fut(code)
        if not code:
            return []
        now = time.time()
        with self._lock:
            hit = self._kcache.get(code)
            if hit and hit[0] > now:
                return hit[1]
        rows = []
        try:
            url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
                   "var%20t=/InnerFuturesNewService.getDailyKLine?symbol=" + code)
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                        timeout=8) as resp:
                text = resp.read().decode("utf-8", "replace")
            m = re.search(r"var t=\(([\s\S]*)\);?\s*$", text.strip())
            if m:
                for it in json.loads(m.group(1)):
                    close = _num(it.get("c"))
                    rows.append({"date": str(it.get("d", ""))[:10],
                                 "open": _num(it.get("o")), "close": close,
                                 "high": _num(it.get("h")), "low": _num(it.get("l")),
                                 "volume": _num(it.get("v")), "amount": None,
                                 "pct": None})
                if limit and len(rows) > limit:
                    rows = rows[-int(limit):]
        except Exception:
            rows = []
        with self._lock:
            self._kcache[code] = (now + self.kline_ttl, rows)
        return rows


if __name__ == "__main__":  # 简单自测
    md = FutMarketData()
    qs = md.quotes(["RB0", "螺纹", "cu0", "SC0"])
    for k, v in qs.items():
        print(k, v["name"], v["price"], "昨结=", v["prev_close"], "pct=", v["pct"],
              "持仓=", v["open_interest"])
    k = md.kline("RB0", 5)
    print("kline rows:", len(k), k[-1] if k else None)
