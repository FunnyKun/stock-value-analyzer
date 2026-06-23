#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fetch_stock_data.py — 股票价值分析器一键取数脚本（yfinance + AkShare 双引擎）

依赖：
    pip install yfinance akshare pandas

用法：
    python fetch_stock_data.py --symbol 0700.HK --market HK
    python fetch_stock_data.py --symbol 600519 --market A
    python fetch_stock_data.py --symbol AAPL --market US
    python fetch_stock_data.py --symbol 0700.HK --market HK --output ./account/_temp_0700.json
    # 港股币种对齐(财报人民币/市值港币)：传 HKD→RMB 汇率使反向DCF/Z-Score 正确
    python fetch_stock_data.py --symbol 0700.HK --market HK --fx-market-to-report 0.917

设计原则（详见 references/api-data-source-protocol.md）：
- 港股 / 美股 优先使用 yfinance；失败时降级到 AkShare（仅港股有完整明细兜底）
- 港股兜底：yfinance 限流时，自动用东方财富三大报表明细
  (stock_financial_hk_report_em) 补全 10+ 年序列并驱动全部八大量化模型
- A 股优先使用 AkShare
- 所有失败信息记录到 errors[] 字段，但不中断主流程
- 输出标准 JSON 供 Skill 报告引用
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# v2.0 量化模型库（与本脚本同目录）。缺失时降级为不计算，不影响基础取数。
try:
    import value_models as _vm  # type: ignore
except Exception:
    try:
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        import value_models as _vm  # type: ignore
    except Exception:
        _vm = None  # type: ignore

# Windows 控制台 GBK 兼容：把 stdout/stderr 切成 UTF-8，避免 ✅⚠️ 这种 emoji 报 UnicodeEncodeError
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _safe_get(d: Any, key: str, default: Any = None) -> Any:
    """安全取字典字段，避免 None 报错。"""
    if d is None:
        return default
    try:
        v = d.get(key, default) if isinstance(d, dict) else default
        if v is None or (isinstance(v, float) and (v != v)):  # NaN 检测
            return default
        return v
    except Exception:
        return default


# ---------------------------------------------------------------------------
# yfinance 引擎
# ---------------------------------------------------------------------------
def fetch_via_yfinance(symbol: str) -> Dict[str, Any]:
    """通过 yfinance 取数（适用港股 / 美股 / ADR / 指数）。"""
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    info = {}
    try:
        info = ticker.info or {}
    except Exception as e:
        raise RuntimeError(f"yfinance.info 调用失败: {e}")

    # fast_info 作为 info 的补充（部分字段在 fast_info 更稳定）
    fast: Dict[str, Any] = {}
    try:
        fi = ticker.fast_info
        if fi is not None:
            # fast_info 是个特殊对象，按字段名取
            for k in ("last_price", "previous_close", "year_high", "year_low",
                      "market_cap", "currency", "shares"):
                try:
                    fast[k] = getattr(fi, k, None)
                except Exception:
                    pass
    except Exception:
        pass

    price_current = _safe_get(info, "regularMarketPrice") or fast.get("last_price")
    previous_close = _safe_get(info, "regularMarketPreviousClose") or fast.get("previous_close")

    result = {
        "price": {
            "current": price_current,
            "currency": _safe_get(info, "currency") or fast.get("currency"),
            "previous_close": previous_close,
            "fifty_two_week_high": _safe_get(info, "fiftyTwoWeekHigh") or fast.get("year_high"),
            "fifty_two_week_low": _safe_get(info, "fiftyTwoWeekLow") or fast.get("year_low"),
            "data_type": "regular_market_price",
            "source": "yfinance",
        },
        "valuation": {
            "market_cap": _safe_get(info, "marketCap") or fast.get("market_cap"),
            "trailing_pe": _safe_get(info, "trailingPE"),
            "forward_pe": _safe_get(info, "forwardPE"),
            "price_to_book": _safe_get(info, "priceToBook"),
            "price_to_sales_ttm": _safe_get(info, "priceToSalesTrailing12Months"),
            "enterprise_value": _safe_get(info, "enterpriseValue"),
            "ev_to_ebitda": _safe_get(info, "enterpriseToEbitda"),
            "source": "yfinance",
        },
        "profitability": {
            "return_on_equity": _safe_get(info, "returnOnEquity"),
            "return_on_assets": _safe_get(info, "returnOnAssets"),
            "profit_margins": _safe_get(info, "profitMargins"),
            "gross_margins": _safe_get(info, "grossMargins"),
            "operating_margins": _safe_get(info, "operatingMargins"),
            "source": "yfinance",
        },
        "financials": {
            "total_revenue_ttm": _safe_get(info, "totalRevenue"),
            "net_income_ttm": _safe_get(info, "netIncomeToCommon"),
            "free_cashflow_ttm": _safe_get(info, "freeCashflow"),
            "operating_cashflow_ttm": _safe_get(info, "operatingCashflow"),
            "total_debt": _safe_get(info, "totalDebt"),
            "total_cash": _safe_get(info, "totalCash"),
            "debt_to_equity": _safe_get(info, "debtToEquity"),
            "current_ratio": _safe_get(info, "currentRatio"),
            "quick_ratio": _safe_get(info, "quickRatio"),
            "source": "yfinance",
        },
        "growth": {
            "revenue_growth_yoy": _safe_get(info, "revenueGrowth"),
            "earnings_growth_yoy": _safe_get(info, "earningsGrowth"),
            "earnings_quarterly_growth": _safe_get(info, "earningsQuarterlyGrowth"),
            "source": "yfinance",
        },
        "dividend": {
            "dividend_yield": _safe_get(info, "dividendYield"),
            "dividend_rate": _safe_get(info, "dividendRate"),
            "payout_ratio": _safe_get(info, "payoutRatio"),
            "five_year_avg_dividend_yield": _safe_get(info, "fiveYearAvgDividendYield"),
            "source": "yfinance",
        },
        "shares": {
            "shares_outstanding": _safe_get(info, "sharesOutstanding") or fast.get("shares"),
            "float_shares": _safe_get(info, "floatShares"),
            "held_percent_insiders": _safe_get(info, "heldPercentInsiders"),
            "held_percent_institutions": _safe_get(info, "heldPercentInstitutions"),
            "source": "yfinance",
        },
        "company": {
            "long_name": _safe_get(info, "longName"),
            "short_name": _safe_get(info, "shortName"),
            "industry": _safe_get(info, "industry"),
            "sector": _safe_get(info, "sector"),
            "country": _safe_get(info, "country"),
            "website": _safe_get(info, "website"),
            "long_business_summary": _safe_get(info, "longBusinessSummary"),
            "source": "yfinance",
        },
        "analyst": {
            "recommendation_key": _safe_get(info, "recommendationKey"),
            "number_of_analyst_opinions": _safe_get(info, "numberOfAnalystOpinions"),
            "target_mean_price": _safe_get(info, "targetMeanPrice"),
            "target_median_price": _safe_get(info, "targetMedianPrice"),
            "target_high_price": _safe_get(info, "targetHighPrice"),
            "target_low_price": _safe_get(info, "targetLowPrice"),
            "_warning": "⚠️ target_*_price 是分析师目标价，不是现价！",
            "source": "yfinance",
        },
    }
    # v2.0：多年序列 + 量化模型（容错，失败不影响主数据）
    if _vm is not None:
        try:
            ts = _vm.extract_time_series_yf(ticker)
            result["time_series"] = ts
            base_fcf = _safe_get(info, "freeCashflow") or _vm._ts_get(ts, "cashflow", "free_cashflow")
            if base_fcf is None:
                cfo = _vm._ts_get(ts, "cashflow", "operating_cashflow")
                capex = _vm._ts_get(ts, "cashflow", "capex")
                if cfo is not None and capex is not None:
                    base_fcf = cfo + capex  # yfinance capex 为负值
            result["models"] = _vm.compute_models(
                ts,
                market_cap=_safe_get(info, "marketCap") or fast.get("market_cap"),
                base_fcf=base_fcf,
                sector=_safe_get(info, "sector"),
                industry=_safe_get(info, "industry"),
            )
        except Exception as e:
            result["models"] = {"_error": f"模型计算失败: {e}"}
    return result


# ---------------------------------------------------------------------------
# AkShare 引擎 - A 股
# ---------------------------------------------------------------------------
def fetch_via_akshare_a(symbol: str) -> Dict[str, Any]:
    """通过 AkShare 取 A 股数据。symbol 为纯 6 位代码，如 600519/000001。"""
    import akshare as ak

    # 个股信息（市值、PE、PB、行业等）
    indiv = {}
    try:
        df_indiv = ak.stock_individual_info_em(symbol=symbol)
        if df_indiv is not None and not df_indiv.empty:
            # df 是 [item, value] 两列结构
            for _, row in df_indiv.iterrows():
                indiv[str(row["item"])] = row["value"]
    except Exception as e:
        indiv["_error"] = f"stock_individual_info_em 失败: {e}"

    # 财务摘要
    # 注意：AkShare 的 stock_financial_abstract 列结构为 [选项, 指标, 报告期1, 报告期2, ...]，
    # 列顺序通常是从最新到最早（如 "20251231","20250930",...,"19991231"），
    # 因此正确的"最新期列"是 columns[2]，而不是 columns[-1]
    fin: Dict[str, Any] = {}
    _df_fin_a = None
    try:
        df_fin = ak.stock_financial_abstract(symbol=symbol)
        _df_fin_a = df_fin
        if df_fin is not None and not df_fin.empty:
            cols = list(df_fin.columns)
            # 找到第一个看起来像 8 位日期的列作为"最新期"
            latest_col = None
            for c in cols:
                cs = str(c)
                if cs.isdigit() and len(cs) == 8:
                    latest_col = c
                    break
            if latest_col is None:
                # 兜底：用第 3 列（前两列通常是"选项""指标"）
                latest_col = cols[2] if len(cols) > 2 else cols[-1]

            # 找到"指标"列名（不同版本可能是 "指标" 或 "项目"）
            indicator_col = None
            for c in cols:
                if str(c) in ("指标", "项目"):
                    indicator_col = c
                    break
            if indicator_col is None:
                indicator_col = cols[1] if len(cols) > 1 else cols[0]

            for _, row in df_fin.iterrows():
                try:
                    indicator = str(row[indicator_col])
                    fin[indicator] = row[latest_col]
                except Exception:
                    pass
            fin["_latest_period"] = str(latest_col)
            fin["_columns"] = [str(c) for c in cols[:6]]  # 前 6 列，便于排错
    except Exception as e:
        fin["_error"] = f"stock_financial_abstract 失败: {e}"

    # 实时行情
    spot: Dict[str, Any] = {}
    try:
        df_spot = ak.stock_zh_a_spot_em()
        if df_spot is not None and not df_spot.empty:
            row = df_spot[df_spot["代码"] == symbol]
            if not row.empty:
                r = row.iloc[0].to_dict()
                spot = {
                    "current": float(r.get("最新价", 0) or 0) or None,
                    "previous_close": float(r.get("昨收", 0) or 0) or None,
                    "change_pct": float(r.get("涨跌幅", 0) or 0) or None,
                    "volume": float(r.get("成交量", 0) or 0) or None,
                    "turnover": float(r.get("成交额", 0) or 0) or None,
                    "high_52w": float(r.get("52周最高", 0) or 0) or None,
                    "low_52w": float(r.get("52周最低", 0) or 0) or None,
                    "pe_ttm": float(r.get("市盈率-动态", 0) or 0) or None,
                    "pb": float(r.get("市净率", 0) or 0) or None,
                    "market_cap_total": float(r.get("总市值", 0) or 0) or None,
                    "market_cap_float": float(r.get("流通市值", 0) or 0) or None,
                }
    except Exception as e:
        spot["_error"] = f"stock_zh_a_spot_em 失败: {e}"

    # v2.0：A股摘要级多年序列 + 量化模型（明细不足，M/Z 跳过，反向DCF 因缺 FCF 不计算）
    _ts_a: Dict[str, Any] = {}
    _models_a: Dict[str, Any] = {}
    if _vm is not None and _df_fin_a is not None:
        try:
            _ts_a = _vm.extract_time_series_akshare_a(_df_fin_a)
            _mc = spot.get("market_cap_total") or _vm._num(indiv.get("总市值"))
            _models_a = _vm.compute_models(_ts_a, market_cap=_mc, base_fcf=None,
                                           industry=indiv.get("行业"), enable_forensic=False)
        except Exception as e:
            _models_a = {"_error": f"模型计算失败: {e}"}

    return {
        "price": {
            "current": spot.get("current"),
            "currency": "CNY",
            "previous_close": spot.get("previous_close"),
            "fifty_two_week_high": spot.get("high_52w"),
            "fifty_two_week_low": spot.get("low_52w"),
            "change_pct": spot.get("change_pct"),
            "data_type": "regular_market_price",
            "source": "akshare",
        },
        "valuation": {
            "market_cap": spot.get("market_cap_total") or indiv.get("总市值"),
            "market_cap_float": spot.get("market_cap_float") or indiv.get("流通市值"),
            "trailing_pe": spot.get("pe_ttm"),
            "price_to_book": spot.get("pb"),
            "source": "akshare",
        },
        "company": {
            "long_name": indiv.get("股票简称"),
            "industry": indiv.get("行业"),
            "listing_date": str(indiv.get("上市时间", "")),
            "total_shares": indiv.get("总股本"),
            "float_shares": indiv.get("流通股"),
            "source": "akshare",
        },
        "financials_abstract": fin,
        "raw_individual_info": indiv,
        "time_series": _ts_a,
        "models": _models_a,
    }


# ---------------------------------------------------------------------------
# AkShare 引擎 - 港股
# ---------------------------------------------------------------------------
def fetch_via_akshare_hk(symbol_5digit: str, market_cap_hint: Any = None,
                         fx_market_to_report: float = 1.0) -> Dict[str, Any]:
    """通过 AkShare 取港股数据。symbol_5digit 为 5 位代码，如 00700。

    v2.0 兜底升级：yfinance 限流时，用东方财富港股三大报表明细
    (stock_financial_hk_report_em) 补全多年序列并驱动全部八大量化模型。

    参数：
      market_cap_hint    上游(如 yfinance)已拿到的市值，作为反向DCF/Z-Score 输入优先项
      fx_market_to_report  市值币种→财报记账币种的换算系数（默认 1.0=同币种）。
                           港股常见陷阱：财报以人民币计、市值以港币计，需传 HKD→RMB≈0.917。
    """
    import akshare as ak

    spot: Dict[str, Any] = {}
    try:
        df_spot = ak.stock_hk_spot_em()
        if df_spot is not None and not df_spot.empty:
            row = df_spot[df_spot["代码"] == symbol_5digit]
            if not row.empty:
                r = row.iloc[0].to_dict()
                spot = {
                    "current": float(r.get("最新价", 0) or 0) or None,
                    "previous_close": float(r.get("昨收", 0) or 0) or None,
                    "change_pct": float(r.get("涨跌幅", 0) or 0) or None,
                    "volume": float(r.get("成交量", 0) or 0) or None,
                    "turnover": float(r.get("成交额", 0) or 0) or None,
                    "market_cap_total": float(r.get("总市值", 0) or 0) or None,
                    "name": r.get("名称"),
                }
    except Exception as e:
        spot["_error"] = f"stock_hk_spot_em 失败: {e}"

    # 财务分析指标（最新一期摘要 + 币种提示）
    fin: Dict[str, Any] = {}
    report_currency = None
    is_cny_code = None
    try:
        df_fin = ak.stock_financial_hk_analysis_indicator_em(symbol=symbol_5digit, indicator="年度")
        if df_fin is not None and not df_fin.empty:
            latest = df_fin.iloc[0].to_dict()
            fin = {str(k): (str(v) if hasattr(v, "isoformat") else v) for k, v in latest.items()}
            report_currency = fin.get("CURRENCY")
            is_cny_code = fin.get("IS_CNY_CODE")
    except Exception as e:
        fin["_error"] = f"stock_financial_hk_analysis_indicator_em 失败: {e}"

    # 三大报表明细（yfinance 限流时的量化模型主力数据源）
    detail_errors: Dict[str, str] = {}
    balance_df = income_df = cashflow_df = None
    _stmt_map = {"资产负债表": "balance", "利润表": "income", "现金流量表": "cashflow"}
    for sym_cn, tag in _stmt_map.items():
        try:
            df = ak.stock_financial_hk_report_em(stock=symbol_5digit, symbol=sym_cn, indicator="年度")
            if tag == "balance":
                balance_df = df
            elif tag == "income":
                income_df = df
            else:
                cashflow_df = df
        except Exception as e:
            detail_errors[tag] = str(e)
        time.sleep(0.3)  # 轻量限速，避免东财风控

    time_series: Dict[str, Any] = {}
    models: Dict[str, Any] = {}
    if _vm is not None and (income_df is not None or balance_df is not None):
        try:
            time_series = _vm.extract_time_series_akshare_hk(income_df, balance_df, cashflow_df)
            base_fcf = _vm._ts_get(time_series, "cashflow", "free_cashflow")
            if base_fcf is None:
                cfo = _vm._ts_get(time_series, "cashflow", "operating_cashflow")
                capex = _vm._ts_get(time_series, "cashflow", "capex")
                if cfo is not None and capex is not None:
                    base_fcf = cfo + capex

            # 市值：上游提示 > 港股实时总市值 > 现价 × 反推股数
            mc_raw = _vm._num(market_cap_hint) or spot.get("market_cap_total")
            if mc_raw is None:
                px = spot.get("current")
                sh = _vm._ts_get(time_series, "balance_sheet", "shares")
                if px is not None and sh:
                    mc_raw = px * sh
            # 币种对齐：市值(港币) → 财报记账币种
            mc_aligned = mc_raw * fx_market_to_report if mc_raw is not None else None

            models = _vm.compute_models(
                time_series,
                market_cap=mc_aligned,
                base_fcf=base_fcf,
                industry=spot.get("name"),
                enable_forensic=True,
            )
            models["_currency_alignment"] = {
                "report_currency_hint": report_currency,
                "is_cny_code_hint": is_cny_code,
                "market_cap_currency": "HKD",
                "fx_market_to_report_used": fx_market_to_report,
                "market_cap_raw": mc_raw,
                "market_cap_aligned": mc_aligned,
                "warning": (
                    "⚠️ 港股币种陷阱：明细报表 AMOUNT 为财报记账币种，市值为港币。"
                    "CURRENCY 字段可能不可靠(部分内地企业实际以人民币计却被标 HKD)。"
                    "若财报币种≠港币，请用 --fx-market-to-report 传入 HKD→财报币种汇率(如人民币≈0.917)后重跑，"
                    "否则仅反向DCF隐含增速与 Altman-Z 的 X4 受影响，其余比率类模型不受币种影响。"
                ),
            }
        except Exception as e:
            models = {"_error": f"港股明细模型计算失败: {e}"}

    return {
        "price": {
            "current": spot.get("current"),
            "currency": "HKD",
            "previous_close": spot.get("previous_close"),
            "change_pct": spot.get("change_pct"),
            "data_type": "regular_market_price",
            "source": "akshare",
        },
        "valuation": {
            "market_cap": spot.get("market_cap_total") or market_cap_hint,
            "source": "akshare",
        },
        "company": {
            "long_name": spot.get("name"),
            "source": "akshare",
        },
        "financials_hk_indicator": fin,
        "time_series": time_series,
        "models": models,
        "_detail_errors": detail_errors,
    }


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------
def normalize_symbol(symbol: str, market: str) -> Dict[str, str]:
    """根据 market 标准化 symbol，返回 {yfinance, akshare} 两种格式。"""
    s = symbol.strip().upper()
    market = market.upper()

    if market == "HK":
        # yfinance 用 0700.HK；AkShare 用 00700（5 位）
        if s.endswith(".HK"):
            num = s.replace(".HK", "").lstrip("0")
            yf_sym = f"{int(num):04d}.HK"  # 至少 4 位
            ak_sym = f"{int(num):05d}"
        else:
            num = s.lstrip("0") or "0"
            yf_sym = f"{int(num):04d}.HK"
            ak_sym = f"{int(num):05d}"
        return {"yfinance": yf_sym, "akshare": ak_sym}

    if market == "A":
        # AkShare 用 6 位纯数字；yfinance 用 .SS / .SZ 但不稳定
        num = s.zfill(6)
        if num.startswith(("60", "68", "9")):
            yf_sym = f"{num}.SS"
        else:
            yf_sym = f"{num}.SZ"
        return {"yfinance": yf_sym, "akshare": num}

    # US
    return {"yfinance": s, "akshare": s}


def fetch_all(symbol: str, market: str, fx_market_to_report: float = 1.0) -> Dict[str, Any]:
    norm = normalize_symbol(symbol, market)
    engines_used: List[str] = []
    engines_failed: List[Dict[str, str]] = []
    errors: List[str] = []

    merged: Dict[str, Any] = {}

    if market.upper() in ("HK", "US"):
        # yfinance 主取
        try:
            data = fetch_via_yfinance(norm["yfinance"])
            merged.update(data)
            engines_used.append("yfinance")
        except Exception as e:
            engines_failed.append({"engine": "yfinance", "error": str(e)})
            errors.append(f"yfinance: {e}")

        # 港股：yfinance 关键字段或量化模型缺失时，AkShare 明细兜底（全自动跑通核心）
        if market.upper() == "HK":
            price_ok = merged.get("price", {}).get("current") is not None
            _m = merged.get("models")
            models_ok = (
                isinstance(_m, dict)
                and not _m.get("_error")
                and isinstance(_m.get("reverse_dcf"), dict)
                and isinstance(_m.get("dupont"), dict)
            )
            if not price_ok or not models_ok:
                try:
                    mc_hint = merged.get("valuation", {}).get("market_cap")
                    data_hk = fetch_via_akshare_hk(
                        norm["akshare"],
                        market_cap_hint=mc_hint,
                        fx_market_to_report=fx_market_to_report,
                    )
                    # 价格缺失时用 akshare 填充
                    if not price_ok and data_hk.get("price", {}).get("current"):
                        merged["price"] = data_hk["price"]
                    # 量化模型缺失时用 akshare 明细补全 models + time_series
                    if not models_ok and isinstance(data_hk.get("models"), dict) and not data_hk["models"].get("_error"):
                        merged["models"] = data_hk["models"]
                        merged["time_series"] = data_hk.get("time_series")
                        merged.setdefault("valuation", {})
                        if not merged["valuation"].get("market_cap"):
                            merged["valuation"]["market_cap"] = data_hk.get("valuation", {}).get("market_cap")
                    merged["_akshare_hk_supplement"] = data_hk
                    engines_used.append("akshare")
                except Exception as e:
                    engines_failed.append({"engine": "akshare(HK)", "error": str(e)})
                    errors.append(f"akshare HK: {e}")

    elif market.upper() == "A":
        try:
            data = fetch_via_akshare_a(norm["akshare"])
            merged.update(data)
            engines_used.append("akshare")
        except Exception as e:
            engines_failed.append({"engine": "akshare(A)", "error": str(e)})
            errors.append(f"akshare A: {e}")

    else:
        errors.append(f"未知市场类型: {market}（仅支持 HK/A/US）")

    return {
        "meta": {
            "symbol_input": symbol,
            "market": market.upper(),
            "symbol_normalized": norm,
            "fx_market_to_report": fx_market_to_report,
            "fetch_time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fetch_time_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "engines_used": engines_used,
            "engines_failed": engines_failed,
        },
        **merged,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="股票价值分析器一键取数脚本（yfinance + AkShare 双引擎）"
    )
    parser.add_argument("--symbol", required=True, help="股票代码，如 0700.HK / 600519 / AAPL")
    parser.add_argument(
        "--market",
        required=True,
        choices=["HK", "A", "US", "hk", "a", "us"],
        help="市场类型：HK（港股）/ A（A股）/ US（美股）",
    )
    parser.add_argument("--output", default=None, help="输出 JSON 文件路径")
    parser.add_argument(
        "--fx-market-to-report",
        type=float,
        default=1.0,
        help="港股专用：市值币种→财报记账币种的换算系数（默认 1.0=同币种）。"
             "腾讯等内地企业财报以人民币计、市值以港币计，传 0.917(HKD→RMB) 对齐反向DCF/Z-Score。",
    )
    parser.add_argument("--print", action="store_true", help="同时打印到 stdout")

    args = parser.parse_args()

    try:
        data = fetch_all(args.symbol, args.market, fx_market_to_report=args.fx_market_to_report)
    except Exception as e:
        print(f"❌ 取数过程发生未捕获异常: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1

    # 决定输出路径
    if args.output:
        output_path = args.output
    else:
        # 默认输出到 ./account/_temp_value_analysis_<symbol>.json
        safe_sym = args.symbol.replace(".", "_").replace("/", "_")
        output_dir = "./account"
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"_temp_value_analysis_{safe_sym}.json")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    # 用 ASCII 替代 emoji，避免在 Windows GBK 控制台下崩溃
    print(f"[OK] 取数完成 -> {output_path}")
    print(f"     使用引擎: {data['meta']['engines_used']}")
    if data["meta"]["engines_failed"]:
        print(f"     [WARN] 失败引擎: {data['meta']['engines_failed']}")
    if data.get("price", {}).get("current"):
        print(f"     当前价格: {data['price']['current']} {data['price'].get('currency', '')}")
    if data.get("valuation", {}).get("trailing_pe"):
        try:
            print(f"     PE-TTM: {float(data['valuation']['trailing_pe']):.2f}")
        except Exception:
            print(f"     PE-TTM: {data['valuation']['trailing_pe']}")

    if args.print:
        print("\n--- JSON 内容 ---")
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))

    return 0


if __name__ == "__main__":
    sys.exit(main())
