# -*- coding: utf-8 -*-
"""港股行情网关：东方财富公开接口（实时+日K），带内存缓存。仅标准库。

代码规范：内部统一 'hk00700'（5位数字）；兼容 '00700'/'700'/大写输入。
数据源：push2.eastmoney.com（实时，secid=116.XXXXX）、push2his（日K）。
"""
import re
import threading
import time
import urllib.request

UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://quote.eastmoney.com/",
}
_FIELDS = "f43,f44,f45,f46,f47,f48,f57,f58,f60,f170"  # 与A股网关同字段语义


def norm_hk(raw):
    """各种输入 -> 'hk00700'；无效返回 None。"""
    if not raw:
        return None
    s = str(raw).strip().lower().replace(" ", "").replace("_", "")
    if s.startswith("hk"):
        s = s[2:]
    if s.startswith("116."):
        s = s[4:]
    if re.fullmatch(r"\d{1,5}", s):
        return "hk" + s.zfill(5)
    return None


def to_secid(code):
    return "116." + code[2:]


def _num(v):
    if v in ("-", "", None):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _http_json(url, timeout=6):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        import json
        return json.loads(resp.read().decode("utf-8", "replace"))


class HKMarketData:
    def __init__(self, quote_ttl=3.0, kline_ttl=300.0):
        self.quote_ttl, self.kline_ttl = quote_ttl, kline_ttl
        self._qcache, self._kcache = {}, {}
        self._lock = threading.Lock()

    def quotes(self, codes):
        """逐只实时报价；返回 {hk00700: quote_dict}。"""
        out, todo, now = {}, [], time.time()
        with self._lock:
            for c in codes:
                c = norm_hk(c)
                if not c:
                    continue
                hit = self._qcache.get(c)
                if hit and hit[0] > now:
                    out[c] = hit[1]
                else:
                    todo.append(c)
        for c in todo:
            try:
                d = (_http_json(
                    "https://push2.eastmoney.com/api/qt/stock/get"
                    "?ut=fa5fd1943c7b386f172d6893dbfba10b&fltt=2&invt=2"
                    "&fields=" + _FIELDS + "&secid=" + to_secid(c)) or {}).get("data") or {}
                price, prev = _num(d.get("f43")), _num(d.get("f60"))
                if price is None:
                    continue
                out[c] = {"code": c, "name": d.get("f58") or c, "price": price,
                          "pct": _num(d.get("f170")),
                          "change": round(price - prev, 3) if prev else None,
                          "open": _num(d.get("f46")), "high": _num(d.get("f44")),
                          "low": _num(d.get("f45")), "prev_close": prev,
                          "volume": _num(d.get("f47")), "amount": _num(d.get("f48")),
                          "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            except Exception:
                continue
        expire = time.time() + self.quote_ttl
        with self._lock:
            for c, q in out.items():
                self._qcache[c] = (expire, q)
        return out

    def kline(self, code, limit=120):
        """日K（腾讯源，前复权，push2his对部分客户端指纹断连故弃用东财K线）。"""
        code = norm_hk(code)
        if not code:
            return []
        now = time.time()
        with self._lock:
            hit = self._kcache.get(code)
            if hit and hit[0] > now:
                return hit[1]
        rows, prev = [], None
        try:
            url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
                   + code + ",day,,," + str(int(limit)) + ",qfq")
            with urllib.request.urlopen(urllib.request.Request(url, headers=dict(
                    UA, Referer="https://gu.qq.com/")), timeout=8) as resp:
                import json
                data = json.loads(resp.read().decode("utf-8", "replace"))
            d = (data.get("data") or {}).get(code) or {}
            for b in (d.get("qfqday") or d.get("day") or []):
                if len(b) < 6 or _num(b[1]) is None:
                    continue
                close = _num(b[2])
                pct = round((close / prev - 1) * 100, 2) if prev else None
                rows.append({"date": b[0], "open": _num(b[1]), "close": close,
                             "high": _num(b[3]), "low": _num(b[4]),
                             "volume": _num(b[5]), "amount": None, "pct": pct})
                prev = close
        except Exception:
            rows = []
        with self._lock:
            self._kcache[code] = (now + self.kline_ttl, rows)
        return rows


if __name__ == "__main__":  # 简单自测
    md = HKMarketData()
    qs = md.quotes(["hk00700", "hk09988", "00700"])
    for k, v in qs.items():
        print(k, v["name"], v["price"], "prev=", v["prev_close"], "pct=", v["pct"])
    k = md.kline("hk00700", 5)
    print("kline rows:", len(k), k[-1] if k else None)
