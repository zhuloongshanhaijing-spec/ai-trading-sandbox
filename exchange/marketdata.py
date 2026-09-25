# -*- coding: utf-8 -*-
"""东方财富行情网关：实时报价 / 日K线 / 代码搜索，带内存缓存。仅用标准库。

代码规范：内部统一用 sh600519 / sz000001 风格；兼容 600519 / 1.600519 / 600519.SH 输入。
数据源：push2.eastmoney.com（实时）、push2his.eastmoney.com（K线）、searchapi（搜索）。
"""
import json
import re
import threading
import time
import urllib.parse
import urllib.request

UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://quote.eastmoney.com/",
}
QUOTE_FIELDS = "f2,f3,f4,f5,f6,f12,f13,f14,f15,f16,f17,f18"
# f2最新价 f3涨跌幅% f4涨跌额 f5成交量(手) f6成交额 f12代码 f13市场 f14名称
# f15最高 f16最低 f17今开 f18昨收


def norm_code(raw):
    """各种输入 -> 'sh600519'/'sz000001'；无法判断返回 None。"""
    if not raw:
        return None
    s = str(raw).strip().lower().replace(" ", "").replace("_", "")
    if re.fullmatch(r"[01]\.\d{6}", s):           # 东财 secid: 1.600519
        mkt, num = s.split(".")
        s = ("sh" if mkt == "1" else "sz") + num
    elif re.fullmatch(r"\d{6}\.(sh|sz|bj)", s):   # 600519.SH
        num, mkt = s.split(".")
        s = mkt + num
    if re.fullmatch(r"(sh|sz)\d{6}", s):
        return s
    if re.fullmatch(r"\d{6}", s):
        if s[0] in "5689":
            return "sh" + s
        return "sz" + s
    return None


def to_secid(code):
    """sh600519 -> 1.600519；sz000001 -> 0.000001。"""
    mkt, num = code[:2], code[2:]
    return ("1." if mkt == "sh" else "0.") + num


def price_limit_pct(code):
    """涨跌停幅度（近似）：创业板30/科创68为20%，其余10%（北交所不入自选）。"""
    num = code[2:]
    if num.startswith("30") or num.startswith("68"):
        return 0.20
    return 0.10


def _http_json(url, timeout=6):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _num(v):
    """东方财富 fltt=2 时通常已是数字；停牌等为 '-'。"""
    if v in ("-", "", None):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class MarketData:
    def __init__(self, quote_ttl=3.0, kline_ttl=300.0):
        self.quote_ttl = quote_ttl
        self.kline_ttl = kline_ttl
        self._qcache = {}  # code -> (expire_ts, dict)
        self._kcache = {}  # code -> (expire_ts, list)
        self._lock = threading.Lock()

    # ---------- 实时报价 ----------
    def quotes(self, codes):
        """批量实时报价（新浪为主，东财逐只后备）；返回 {code: quote_dict}。"""
        out = {}
        todo = []
        now = time.time()
        with self._lock:
            for c in codes:
                c = norm_code(c)
                if not c:
                    continue
                hit = self._qcache.get(c)
                if hit and hit[0] > now:
                    out[c] = hit[1]
                else:
                    todo.append(c)
        if not todo:
            return out
        rest = self._quotes_sina(todo)
        out.update(rest)
        rest = [c for c in todo if c not in rest]
        if rest:
            out.update(self._quotes_east(rest))
        expire = time.time() + self.quote_ttl
        with self._lock:
            for c, q in out.items():
                self._qcache[c] = (expire, q)
        return out

    def _quotes_sina(self, codes):
        """新浪批量：hq.sinajs.cn，需 Referer，GBK。"""
        out = {}
        try:
            url = "https://hq.sinajs.cn/list=" + ",".join(codes)
            req = urllib.request.Request(url, headers=dict(UA, Referer="https://finance.sina.com.cn"))
            with urllib.request.urlopen(req, timeout=6) as resp:
                text = resp.read().decode("gbk", "replace")
            for line in text.splitlines():
                m = re.match(r'var hq_str_(\w+)="(.*)";?\s*$', line.strip())
                if not m:
                    continue
                code, payload = m.group(1), m.group(2)
                p = payload.split(",")
                if len(p) < 32 or not p[0] or _num(p[3]) is None:
                    continue  # 停牌/无效
                q = {
                    "code": code,
                    "name": p[0],
                    "price": _num(p[3]),
                    "pct": round((_num(p[3]) / _num(p[2]) - 1) * 100, 2) if _num(p[2]) else None,
                    "change": round(_num(p[3]) - _num(p[2]), 3) if _num(p[2]) else None,
                    "open": _num(p[1]),
                    "high": _num(p[4]),
                    "low": _num(p[5]),
                    "prev_close": _num(p[2]),
                    "volume": round(_num(p[8]) / 100, 0) if _num(p[8]) else 0,  # 股->手
                    "amount": _num(p[9]),
                    "ts": p[30] + " " + p[31],
                }
                out[code] = q
        except Exception:
            pass
        return out

    def _quotes_east(self, codes):
        """东财逐只后备：qt/stock/get。"""
        fields = "f43,f44,f45,f46,f47,f48,f57,f58,f60,f170"
        out = {}
        for c in codes:
            try:
                url = ("https://push2.eastmoney.com/api/qt/stock/get"
                       "?ut=fa5fd1943c7b386f172d6893dbfba10b&fltt=2&invt=2"
                       "&fields=" + fields + "&secid=" + to_secid(c))
                d = (_http_json(url) or {}).get("data") or {}
                price = _num(d.get("f43"))
                prev = _num(d.get("f60"))
                if price is None:
                    continue
                out[c] = {
                    "code": c,
                    "name": d.get("f58") or c,
                    "price": price,
                    "pct": _num(d.get("f170")),
                    "change": round(price - prev, 3) if prev else None,
                    "open": _num(d.get("f46")),
                    "high": _num(d.get("f44")),
                    "low": _num(d.get("f45")),
                    "prev_close": prev,
                    "volume": _num(d.get("f47")),   # 手
                    "amount": _num(d.get("f48")),
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            except Exception:
                continue
        return out

    # ---------- 日K线 ----------
    def kline(self, code, limit=120):
        """日K（腾讯前复权为主，东财后备）；返回升序列表。"""
        code = norm_code(code)
        if not code:
            return []
        now = time.time()
        with self._lock:
            hit = self._kcache.get(code)
            if hit and hit[0] > now:
                return hit[1]
        rows = self._kline_tencent(code, limit)
        if not rows:
            rows = self._kline_east(code, limit)
        with self._lock:
            self._kcache[code] = (now + self.kline_ttl, rows)
        return rows

    def _kline_tencent(self, code, limit):
        """腾讯日K（前复权）。数组字段: 日期,开,收,高,低,量(手)。"""
        try:
            url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
                   + code + ",day,,," + str(int(limit)) + ",qfq")
            data = _http_json(url)
            d = (data.get("data") or {}).get(code) or {}
            bars = d.get("qfqday") or d.get("day") or []
            rows, prev = [], None
            for b in bars:
                if len(b) < 6 or _num(b[1]) is None:
                    continue
                close = _num(b[2])
                pct = round((close / prev - 1) * 100, 2) if prev else None
                rows.append({
                    "date": b[0], "open": _num(b[1]), "close": close,
                    "high": _num(b[3]), "low": _num(b[4]), "volume": _num(b[5]),
                    "amount": None, "pct": pct,
                })
                prev = close
            return rows
        except Exception:
            return []

    def _kline_east(self, code, limit):
        """东财日K（前复权），两次重试。字段: 日期,开,收,高,低,量(手),额,涨跌幅。"""
        url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid="
               + to_secid(code)
               + "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f59"
               + "&klt=101&fqt=1&end=20500101&lmt=" + str(int(limit)))
        for attempt in range(2):
            try:
                data = _http_json(url)
                klines = (data.get("data") or {}).get("klines") or []
                rows = []
                for line in klines:
                    p = line.split(",")
                    if len(p) < 8:
                        continue
                    rows.append({
                        "date": p[0], "open": _num(p[1]), "close": _num(p[2]),
                        "high": _num(p[3]), "low": _num(p[4]), "volume": _num(p[5]),
                        "amount": _num(p[6]), "pct": _num(p[7]),
                    })
                if rows:
                    return rows
            except Exception:
                if attempt == 0:
                    time.sleep(0.4)
        return []

    def _kline_sina(self, code, limit):
        """新浪日K（不复权，仅作最后备胎）。"""
        try:
            url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                   "CN_MarketDataService.getKLineData?symbol=" + code
                   + "&scale=240&ma=no&datalen=" + str(int(limit)))
            req = urllib.request.Request(url, headers=dict(UA, Referer="https://finance.sina.com.cn"))
            with urllib.request.urlopen(req, timeout=8) as resp:
                text = resp.read().decode("utf-8", "replace")
            items = json.loads(text) if text.strip() else []
            rows = []
            for it in items:
                rows.append({
                    "date": str(it.get("day", ""))[:10], "open": _num(it.get("open")),
                    "close": _num(it.get("close")), "high": _num(it.get("high")),
                    "low": _num(it.get("low")), "volume": _num(it.get("volume")),
                    "amount": None, "pct": None,
                })
            return rows
        except Exception:
            return []

    # ---------- 搜索 ----------
    def search(self, q, limit=10):
        """按代码/名称/拼音搜索；东财优先，腾讯后备。"""
        out = self._search_east(q, limit)
        if out:
            return out
        return self._search_tencent(q, limit)

    def _search_east(self, q, limit):
        try:
            url = ("https://searchapi.eastmoney.com/api/suggest/get?input="
                   + urllib.parse.quote(str(q))
                   + "&type=14&token=D43BF722C8E33BDC906FB84D85E326E8&count=" + str(int(limit)))
            data = _http_json(url)
            items = ((data.get("QuotationCodeTable") or {}).get("Data")) or []
            out = []
            for it in items:
                code = norm_code(str(it.get("MarketNumber", "")) + "." + str(it.get("Code", "")))
                if not code:
                    continue
                out.append({"code": code, "name": it.get("Name") or "", "code_raw": it.get("Code", "")})
            return out
        except Exception:
            return []

    def _search_tencent(self, q, limit):
        try:
            url = ("https://smartbox.gtimg.cn/s3/?v=2&q="
                   + urllib.parse.quote(str(q)) + "&t=all")
            req = urllib.request.Request(url, headers=dict(UA, Referer="https://gu.qq.com/"))
            with urllib.request.urlopen(req, timeout=6) as resp:
                text = resp.read().decode("gbk", "replace")
            m = re.search(r'v_hint="(.*)"', text.strip())
            if not m or not m.group(1).strip():
                return []
            out = []
            for item in m.group(1).split("^"):
                p = item.split("~")
                if len(p) < 4:
                    continue
                mkt, num, name = p[0], p[1], p[2]
                if not mkt or not num or not num.isdigit() or len(num) != 6:
                    continue
                if len(p) >= 5 and p[4] and not p[4].startswith("GP"):  # 只要A股
                    continue
                if "\\u" in name:  # 形如 \u8d35\u5dde 的转义
                    try:
                        name = name.encode("latin-1", "ignore").decode("unicode_escape")
                    except Exception:
                        pass
                code = norm_code(mkt + num)
                if code:
                    out.append({"code": code, "name": name, "code_raw": num})
                if len(out) >= limit:
                    break
            return out
        except Exception:
            return []


if __name__ == "__main__":  # 简单自测
    md = MarketData()
    qs = md.quotes(["sh600519", "sz000001", "300750", "1.601127"])
    for k, v in qs.items():
        print(k, v["name"], v["price"], "prev_close=", v["prev_close"])
    k = md.kline("sh600519", 5)
    print("kline rows:", len(k), k[-1] if k else None)
    print("search:", md.search("茅台", 3))
