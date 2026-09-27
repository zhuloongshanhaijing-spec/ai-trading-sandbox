# -*- coding: utf-8 -*-
"""数据源契约与注册表：内置行情源 + 自定义适配器加载 + 换源闸门。仅标准库。

三种数据源形态（capability 声明）：
  affects_price : 你的委托是否会改变源侧价格（内置模拟源=False；交易型源=True）
  funds_model   : 'local'   = 资金由本地引擎发放（初始额可配，理论无限）
                  'platform'= 资金由源侧账户决定（只读上报）
  trading       : 是否实现交易原语（place_order/cancel_order/positions/account）

内置源（quotes/kline 与 exchange/marketdata.py 的返回结构同构）：
  cn  A股东财/新浪 | us  美股新浪 | hk  港股东财 | fut 国内期货新浪

自定义适配器：见 docs/ADAPTER-SPEC.md；由用户自行实现并负责其所接入服务的
合法性与条款遵守。本项目不附带、不指认任何第三方服务适配器。
"""
import importlib.util
import json
import os
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------- 契约 ----------------

REQUIRED_QUOTE_KEYS = {"code", "name", "price", "pct", "prev_close"}


class MarketSource:
    """数据源契约基类（适配器按 docs/ADAPTER-SPEC.md 实现同名成员即可）。"""

    id = "base"
    label = "未命名源"

    def capabilities(self):
        return {"affects_price": False, "funds_model": "local", "trading": False,
                "sessions": "未知"}

    def quotes(self, codes):
        raise NotImplementedError

    def kline(self, code, limit=120):
        raise NotImplementedError

    # ---- 以下仅 trading=True 时必须实现 ----
    def place_order(self, side, code, shares, price=None):
        raise NotImplementedError

    def cancel_order(self, oid):
        raise NotImplementedError

    def positions(self):
        raise NotImplementedError

    def account(self):
        raise NotImplementedError


class _BuiltinSource(MarketSource):
    def __init__(self, sid, label, md, sessions, kline_ok=True):
        self.id, self.label, self._md = sid, label, md
        self._sessions, self._kline_ok = sessions, kline_ok

    def capabilities(self):
        return {"affects_price": False, "funds_model": "local", "trading": False,
                "builtin": True, "sessions": self._sessions,
                "kline": self._kline_ok}

    def quotes(self, codes):
        return self._md.quotes(codes)

    def kline(self, code, limit=120):
        if not self._kline_ok:
            return []
        return self._md.kline(code, limit)


def _builtin_registry():
    from marketdata import MarketData
    from us_marketdata import USMarketData
    from hk_marketdata import HKMarketData
    from fut_marketdata import FutMarketData
    return {
        "cn": _BuiltinSource("cn", "A股（东财/新浪）", MarketData(),
                             "09:30-11:30 / 13:00-15:00"),
        "us": _BuiltinSource("us", "美股（新浪）", USMarketData(),
                             "北京时间 22:30-05:00（夏令时±1h）", kline_ok=False),
        "hk": _BuiltinSource("hk", "港股（东财）", HKMarketData(),
                             "北京时间 09:30-16:00"),
        "fut": _BuiltinSource("fut", "国内期货主连（新浪）", FutMarketData(),
                              "日盘09:00-15:00 夜盘21:00-23:00/01:00/02:30"),
    }


# ---------------- 自定义适配器 ----------------

def load_custom_adapter(path):
    """从 .py 文件加载用户适配器并做契约校验；返回 (source, err)。"""
    path = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.isfile(path):
        return None, "适配器文件不存在: %s" % path
    try:
        spec = importlib.util.spec_from_file_location(
            "paperlab_adapter_%d" % int(time.time()), path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        return None, "适配器加载失败: %r" % e
    src = None
    for name in ("SOURCE", "source", "MarketSourceImpl", "Adapter"):
        cand = getattr(mod, name, None)
        if cand is not None:
            src = cand() if isinstance(cand, type) else cand
            break
    if src is None or not isinstance(src, MarketSource) and not hasattr(src, "quotes"):
        return None, "适配器未暴露 SOURCE/source 实例（须实现 MarketSource 契约）"
    caps = {}
    try:
        caps = src.capabilities() or {}
    except Exception:
        pass
    if not getattr(src, "id", None):
        src.id = "custom"
    if not getattr(src, "label", None):
        src.label = "自定义源(%s)" % os.path.basename(path)
    caps.setdefault("affects_price", False)
    caps.setdefault("funds_model", "local")
    caps.setdefault("trading", False)
    src.capabilities = lambda c=caps: c
    return src, None


# ---------------- 换源闸门 + 断代(era) ----------------

class SourceManager:
    """当前源状态机：切换前强制清仓（闸门），切换即开启新净值断代(era)。"""

    def __init__(self, state_path=None, adapter_path_env="PAPERLAB_ADAPTER"):
        if state_path is None:
            state_path = os.path.join(_HERE, os.pardir, "data", "source_state.json")
        self.state_path = os.path.abspath(state_path)
        self.era_path = os.path.join(os.path.dirname(self.state_path), "source_eras.jsonl")
        self._lock = threading.Lock()
        self._custom = None
        self._adapter_err = None
        self._builtin = None
        self.current = self._load_state().get("current", "cn")
        ap = os.environ.get(adapter_path_env, "")
        if ap:
            self._custom, self._adapter_err = load_custom_adapter(ap)

    # ---- 内置注册表懒加载 ----
    @property
    def registry(self):
        if self._builtin is None:
            try:
                self._builtin = _builtin_registry()
            except Exception as e:
                self._builtin = {}
                self._adapter_err = "内置源初始化失败: %r" % e
        return self._builtin

    def get(self, sid=None):
        sid = sid or self.current
        if sid == "custom":
            return self._custom
        return self.registry.get(sid)

    def status(self):
        avail = {}
        for sid, s in self.registry.items():
            avail[sid] = {"label": s.label, "caps": s.capabilities()}
        if self._custom is not None:
            avail["custom"] = {"label": self._custom.label, "caps": self._custom.capabilities()}
        cur = self.get()
        return {"current": self.current,
                "current_label": cur.label if cur else None,
                "current_caps": cur.capabilities() if cur else None,
                "available": avail,
                "adapter_error": self._adapter_err}

    # ---- 闸门切换 ----
    def switch(self, target, account_view):
        """account_view() -> {'positions': int, 'open_orders': int}。
        持仓/挂单非空一律拒绝（先清仓再换源）；成功后写断代记录。"""
        with self._lock:
            src = self.get(target)
            if src is None:
                return {"ok": False, "reason": "unknown_source",
                        "detail": "可用源: %s" % ",".join(sorted(self.status()["available"]))}
            try:
                acct = account_view() or {}
            except Exception as e:
                return {"ok": False, "reason": "account_view_error", "detail": repr(e)}
            if acct.get("positions", 0) or acct.get("open_orders", 0):
                return {"ok": False, "reason": "positions_open",
                        "detail": "换源前必须全部平仓并撤单（当前持仓%d笔/挂单%d笔）。"
                                  "这是硬闸门：不同数据源的资金与价格语义不可混算。"
                                  % (acct.get("positions", 0), acct.get("open_orders", 0))}
            old = self.current
            self.current = target
            self._save_state({"current": target})
            era = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "from": old, "to": target,
                   "label": src.label,
                   "caps": src.capabilities()}
            try:
                with open(self.era_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(era, ensure_ascii=False) + "\n")
            except Exception:
                pass
            return {"ok": True, "era": era,
                    "note": "净值曲线自此刻起按新断代分段，勿跨源混算。"}

    def eras(self):
        try:
            with open(self.era_path, encoding="utf-8") as f:
                return [json.loads(L) for L in f if L.strip()]
        except Exception:
            return []

    def _load_state(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_state(self, st):
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
        except Exception:
            pass


if __name__ == "__main__":  # 自测：注册表 + 闸门
    sm = SourceManager(state_path="/tmp/paperlab_src_state.json")
    for sid, info in sm.status()["available"].items():
        print(sid, info["label"], info["caps"].get("affects_price"),
              info["caps"].get("funds_model"))
    r = sm.switch("hk", lambda: {"positions": 2, "open_orders": 0})
    print("带仓切换→", r["ok"], r["reason"])
    r = sm.switch("hk", lambda: {"positions": 0, "open_orders": 0})
    print("清仓切换→", r["ok"], r["era"])
    print("断代记录:", len(sm.eras()))
