#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
value_models.py — 股票价值分析器 v2.0 可复现量化模型库

被 fetch_stock_data.py 调用，提供：
  1) 多年三大报表序列提取（yfinance / A股摘要）
  2) 八大量化模型：杜邦分解、ROIC、Piotroski F-Score、Beneish M-Score、
     Altman Z-Score、盈余质量、反向 DCF 隐含增速、历史 CAGR 与 ROE 一致性

口径详见 references/financial-quality-models.md / valuation-models.md / time-series-analysis.md

设计原则：
  - 一切计算包 try/except，缺字段返回 None / not applicable，绝不抛出中断主流程
  - 年份序列统一「从新到旧」：index 0 = 最近一期，index 1 = 上一期
  - 金融股 / 数据不足时，M-Score/Z-Score/标准 DCF 标注 applicable=False
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional


# --------------------------- 基础工具 ---------------------------
def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        if isinstance(v, str):
            v = v.replace(",", "").replace("%", "").strip()
            if v in ("", "-", "--", "None", "nan", "NaN"):
                return None
        f = float(v)
        return None if f != f else f
    except Exception:
        return None


def _safe_div(a: Any, b: Any) -> Optional[float]:
    a, b = _num(a), _num(b)
    if a is None or b is None or b == 0:
        return None
    return a / b


def _pick_row(df, names: List[str]):
    """按科目别名（先精确后模糊、不分大小写）从 yfinance 报表取一行。"""
    if df is None:
        return None
    try:
        idx_map = {str(i).strip().lower(): i for i in df.index}
        for nm in names:
            k = nm.strip().lower()
            if k in idx_map:
                return df.loc[idx_map[k]]
        for nm in names:
            k = nm.strip().lower()
            for low, orig in idx_map.items():
                if k in low:
                    return df.loc[orig]
    except Exception:
        return None
    return None


def _row_to_list(row) -> List[Optional[float]]:
    if row is None:
        return []
    try:
        return [_num(v) for v in list(row.values)]
    except Exception:
        return []


def _ts_get(ts: Dict[str, Any], statement: str, key: str, i: int = 0) -> Optional[float]:
    try:
        arr = ts.get(statement, {}).get(key, [])
        if arr and len(arr) > i:
            return arr[i]
    except Exception:
        pass
    return None


# --------------------------- 序列提取 ---------------------------
_YF_INCOME = {
    "total_revenue": ["Total Revenue", "Operating Revenue"],
    "gross_profit": ["Gross Profit"],
    "operating_income": ["Operating Income"],
    "ebit": ["EBIT", "Ebit"],
    "pretax_income": ["Pretax Income", "Pre Tax Income", "Income Before Tax"],
    "tax_provision": ["Tax Provision", "Income Tax Expense"],
    "net_income": ["Net Income Common Stockholders", "Net Income", "Net Income Continuous Operations"],
    "interest_expense": ["Interest Expense"],
    "sga": ["Selling General And Administration", "Selling General And Administrative"],
}
_YF_BALANCE = {
    "total_assets": ["Total Assets"],
    "total_liabilities": ["Total Liabilities Net Minority Interest", "Total Liabilities"],
    "total_equity": ["Stockholders Equity", "Common Stock Equity", "Total Stockholder Equity"],
    "current_assets": ["Current Assets", "Total Current Assets"],
    "current_liabilities": ["Current Liabilities", "Total Current Liabilities"],
    "cash": ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"],
    "receivables": ["Accounts Receivable", "Receivables", "Net Receivables"],
    "inventory": ["Inventory"],
    "ppe_net": ["Net PPE", "Net Property Plant And Equipment"],
    "total_debt": ["Total Debt"],
    "long_term_debt": ["Long Term Debt"],
    "current_debt": ["Current Debt", "Current Debt And Capital Lease Obligation"],
    "retained_earnings": ["Retained Earnings"],
    "working_capital": ["Working Capital"],
    "shares": ["Ordinary Shares Number", "Share Issued"],
}
_YF_CASHFLOW = {
    "operating_cashflow": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    "capex": ["Capital Expenditure"],
    "free_cashflow": ["Free Cash Flow"],
    "depreciation": ["Depreciation And Amortization", "Depreciation Amortization Depletion", "Reconciled Depreciation"],
    "dividends_paid": ["Cash Dividends Paid", "Common Stock Dividend Paid"],
}


def extract_time_series_yf(ticker) -> Dict[str, Any]:
    ts: Dict[str, Any] = {"fiscal_periods": [], "income_statement": {},
                          "balance_sheet": {}, "cashflow": {}, "source": "yfinance"}
    try:
        fin = getattr(ticker, "financials", None)
        bs = getattr(ticker, "balance_sheet", None)
        cf = getattr(ticker, "cashflow", None)
        for df in (fin, bs, cf):
            if df is not None and hasattr(df, "columns") and len(df.columns) > 0:
                ts["fiscal_periods"] = [str(c)[:10] for c in df.columns]
                break
        for k, names in _YF_INCOME.items():
            ts["income_statement"][k] = _row_to_list(_pick_row(fin, names))
        for k, names in _YF_BALANCE.items():
            ts["balance_sheet"][k] = _row_to_list(_pick_row(bs, names))
        for k, names in _YF_CASHFLOW.items():
            ts["cashflow"][k] = _row_to_list(_pick_row(cf, names))
    except Exception as e:
        ts["_error"] = f"extract_time_series_yf 失败: {e}"
    return ts


def extract_time_series_akshare_a(df_fin) -> Dict[str, Any]:
    """A股 stock_financial_abstract 多列 → 年报序列（从新到旧）。摘要级，仅供近似。"""
    ts: Dict[str, Any] = {"fiscal_periods": [], "income_statement": {},
                          "balance_sheet": {}, "cashflow": {}, "source": "akshare_abstract"}
    try:
        cols = list(df_fin.columns)
        indicator_col = next((c for c in cols if str(c) in ("指标", "项目")), None)
        if indicator_col is None:
            indicator_col = cols[1] if len(cols) > 1 else cols[0]
        year_cols = [c for c in cols if str(c).isdigit() and len(str(c)) == 8 and str(c).endswith("1231")]
        year_cols = sorted(year_cols, key=lambda x: str(x), reverse=True)[:10]
        ts["fiscal_periods"] = [str(c) for c in year_cols]
        alias = {
            ("income_statement", "total_revenue"): ["营业总收入", "营业收入"],
            ("income_statement", "net_income"): ["归母净利润", "归属于母公司股东的净利润", "净利润"],
            ("balance_sheet", "total_assets"): ["资产总计", "总资产"],
            ("balance_sheet", "total_equity"): ["归属于母公司股东权益合计", "归属于母公司股东权益", "股东权益合计", "净资产"],
            ("cashflow", "operating_cashflow"): ["经营活动产生的现金流量净额", "经营现金流量净额"],
        }
        rowmap = {str(row[indicator_col]).strip(): row for _, row in df_fin.iterrows()}
        for (stmt, key), names in alias.items():
            chosen = None
            for nm in names:
                for ind_name, row in rowmap.items():
                    if nm == ind_name or nm in ind_name:
                        chosen = row
                        break
                if chosen is not None:
                    break
            ts[stmt][key] = [_num(chosen.get(c)) for c in year_cols] if chosen is not None else []
    except Exception as e:
        ts["_error"] = f"extract_time_series_akshare_a 失败: {e}"
    return ts


# 港股 stock_financial_hk_report_em 明细科目别名（东方财富长表 STD_ITEM_NAME 口径）
# mode: "first"=按顺序取第一个命中科目；"sum_all"=列表内所有命中科目逐年求和
_HK_INCOME = {
    "total_revenue": (["营业额", "营运收入"], "first"),
    "gross_profit": (["毛利"], "first"),
    "operating_income": (["经营溢利"], "first"),
    "pretax_income": (["除税前溢利"], "first"),
    "tax_provision": (["税项"], "first"),
    "net_income": (["股东应占溢利"], "first"),
    "interest_expense": (["融资成本"], "first"),
    "sga": (["行政开支", "销售及分销费用"], "sum_all"),
    "eps_basic": (["每股基本盈利"], "first"),
}
_HK_BALANCE = {
    "total_assets": (["总资产"], "first"),
    "total_liabilities": (["总负债"], "first"),
    "total_equity": (["股东权益"], "first"),
    "current_assets": (["流动资产合计"], "first"),
    "current_liabilities": (["流动负债合计"], "first"),
    "cash": (["现金及等价物"], "first"),
    "receivables": (["应收帐款", "应收账款"], "first"),
    "inventory": (["存货"], "first"),
    "ppe_net": (["物业厂房及设备", "固定资产"], "first"),
    "long_term_debt": (["长期贷款"], "first"),
    "current_debt": (["短期贷款"], "first"),
    "total_debt": (["长期贷款", "短期贷款"], "sum_all"),
    "retained_earnings": (["保留溢利(累计亏损)", "保留溢利"], "first"),
    "working_capital": (["净流动资产"], "first"),
}
_HK_CASHFLOW = {
    "operating_cashflow": (["经营业务现金净额"], "first"),
    "depreciation": (["加:折旧及摊销", "折旧及摊销"], "first"),
}


def _hk_pivot(df, year_dates: set) -> Dict[str, Dict[str, Optional[float]]]:
    """港股长表 → {科目名: {报告日: 金额}}，仅保留 year_dates 内的年报。"""
    m: Dict[str, Dict[str, Optional[float]]] = {}
    if df is None:
        return m
    try:
        for _, row in df.iterrows():
            name = str(row.get("STD_ITEM_NAME"))
            d = str(row.get("REPORT_DATE"))[:10]
            if d in year_dates:
                m.setdefault(name, {})[d] = _num(row.get("AMOUNT"))
    except Exception:
        pass
    return m


def _hk_series(pivot_map: Dict[str, Dict[str, Optional[float]]], names: List[str],
               year_dates: List[str], mode: str = "first") -> List[Optional[float]]:
    """按别名 + 模式从 pivot_map 取一条从新到旧的年度序列。"""
    if mode == "sum_all":
        out: List[Optional[float]] = [None] * len(year_dates)
        hit = False
        for nm in names:
            row = pivot_map.get(nm)
            if row is None:
                for k in pivot_map:
                    if nm in k:
                        row = pivot_map[k]
                        break
            if row:
                hit = True
                for i, d in enumerate(year_dates):
                    v = row.get(d)
                    if v is not None:
                        out[i] = (out[i] or 0.0) + v
        return out if hit else []
    # first：精确优先，再模糊包含
    for nm in names:
        if nm in pivot_map:
            return [pivot_map[nm].get(d) for d in year_dates]
    for nm in names:
        for k in pivot_map:
            if nm in k:
                return [pivot_map[k].get(d) for d in year_dates]
    return []


def extract_time_series_akshare_hk(income_df, balance_df, cashflow_df) -> Dict[str, Any]:
    """港股 stock_financial_hk_report_em 三大报表明细 → 多年序列（从新到旧）。

    yfinance 限流时的港股兜底主力：东方财富明细覆盖 10+ 年完整三大报表，
    足以驱动全部八大量化模型（含 M-Score / Z-Score）。

    注意：明细 AMOUNT 为财报原始记账币种（如腾讯为人民币），而现价/市值多为港币，
    反向 DCF 与 Altman-Z 的市值项须由调用方做币种对齐（fx_market_to_report）。
    """
    ts: Dict[str, Any] = {"fiscal_periods": [], "income_statement": {},
                          "balance_sheet": {}, "cashflow": {}, "source": "akshare_hk_report_em"}
    try:
        def _year_dates(df) -> set:
            s = set()
            if df is None:
                return s
            try:
                for v in df["REPORT_DATE"]:
                    d = str(v)[:10]
                    if d.endswith("-12-31"):
                        s.add(d)
            except Exception:
                pass
            return s

        all_dates = _year_dates(balance_df) | _year_dates(income_df) | _year_dates(cashflow_df)
        year_dates = sorted(all_dates, reverse=True)[:10]
        ts["fiscal_periods"] = year_dates
        yd_set = set(year_dates)

        inc_m = _hk_pivot(income_df, yd_set)
        bal_m = _hk_pivot(balance_df, yd_set)
        cf_m = _hk_pivot(cashflow_df, yd_set)

        for key, (names, mode) in _HK_INCOME.items():
            ts["income_statement"][key] = _hk_series(inc_m, names, year_dates, mode)
        for key, (names, mode) in _HK_BALANCE.items():
            ts["balance_sheet"][key] = _hk_series(bal_m, names, year_dates, mode)
        for key, (names, mode) in _HK_CASHFLOW.items():
            ts["cashflow"][key] = _hk_series(cf_m, names, year_dates, mode)

        # capex：港股"购建固定资产/无形资产"为正值流出，取负以复用 fcf=cfo+capex 口径
        capex_pos = _hk_series(cf_m, ["购建固定资产", "购建无形资产及其他资产"], year_dates, "sum_all")
        ts["cashflow"]["capex"] = [(-v if v is not None else None) for v in capex_pos]
        # 股息：已付股息为正值流出，取负与 yfinance 口径一致
        div = _hk_series(cf_m, ["已付股息(融资)", "已付股息"], year_dates, "first")
        ts["cashflow"]["dividends_paid"] = [(-v if v is not None else None) for v in div]
        # free_cashflow = 经营现金净额 + capex(负)
        cfo = ts["cashflow"].get("operating_cashflow", [])
        cap = ts["cashflow"].get("capex", [])
        fcf: List[Optional[float]] = []
        for i in range(max(len(cfo), len(cap))):
            a = cfo[i] if i < len(cfo) else None
            b = cap[i] if i < len(cap) else None
            fcf.append(a + b if (a is not None and b is not None) else None)
        ts["cashflow"]["free_cashflow"] = fcf
        # 股数序列：净利润 / 每股基本盈利（用于 Piotroski no_dilution，明细无直接股数）
        ni = ts["income_statement"].get("net_income", [])
        eps = ts["income_statement"].get("eps_basic", [])
        shares: List[Optional[float]] = []
        for i in range(min(len(ni), len(eps))):
            shares.append(_safe_div(ni[i], eps[i]))
        ts["balance_sheet"]["shares"] = shares
    except Exception as e:
        ts["_error"] = f"extract_time_series_akshare_hk 失败: {e}"
    return ts


# --------------------------- 模型 ---------------------------
def calc_dupont(ts: Dict[str, Any]) -> Dict[str, Any]:
    rev = _ts_get(ts, "income_statement", "total_revenue")
    ni = _ts_get(ts, "income_statement", "net_income")
    ebit = _ts_get(ts, "income_statement", "ebit") or _ts_get(ts, "income_statement", "operating_income")
    pretax = _ts_get(ts, "income_statement", "pretax_income")
    assets = _ts_get(ts, "balance_sheet", "total_assets")
    equity = _ts_get(ts, "balance_sheet", "total_equity")
    net_margin = _safe_div(ni, rev)
    asset_turnover = _safe_div(rev, assets)
    equity_mult = _safe_div(assets, equity)
    roe_3 = (net_margin * asset_turnover * equity_mult
             if None not in (net_margin, asset_turnover, equity_mult) else None)
    tax_burden = _safe_div(ni, pretax)
    interest_burden = _safe_div(pretax, ebit)
    ebit_margin = _safe_div(ebit, rev)
    roe_5 = (tax_burden * interest_burden * ebit_margin * asset_turnover * equity_mult
             if None not in (tax_burden, interest_burden, ebit_margin, asset_turnover, equity_mult) else None)
    note = ""
    if equity_mult is not None and equity_mult > 3:
        note = "权益乘数偏高(>3)，ROE 含较多杠杆驱动成分，需结合行业判断"
    return {"components": {
        "net_margin": net_margin, "asset_turnover": asset_turnover, "equity_multiplier": equity_mult,
        "roe_3factor": roe_3, "tax_burden": tax_burden, "interest_burden": interest_burden,
        "ebit_margin": ebit_margin, "roe_5factor": roe_5}, "note": note}


def calc_roic(ts: Dict[str, Any], default_wacc: float = 0.09) -> Dict[str, Any]:
    ebit = _ts_get(ts, "income_statement", "ebit") or _ts_get(ts, "income_statement", "operating_income")
    pretax = _ts_get(ts, "income_statement", "pretax_income")
    tax = _ts_get(ts, "income_statement", "tax_provision")
    debt = _ts_get(ts, "balance_sheet", "total_debt")
    if debt is None:
        debt = (_ts_get(ts, "balance_sheet", "long_term_debt") or 0) + (_ts_get(ts, "balance_sheet", "current_debt") or 0)
    equity = _ts_get(ts, "balance_sheet", "total_equity")
    cash = _ts_get(ts, "balance_sheet", "cash") or 0
    tax_rate = _safe_div(tax, pretax)
    if tax_rate is None or tax_rate < 0:
        tax_rate = 0.25
    tax_rate = min(max(tax_rate, 0.0), 0.5)
    invested = (debt or 0) + equity - (cash or 0) if equity is not None else None
    nopat = ebit * (1 - tax_rate) if ebit is not None else None
    roic = _safe_div(nopat, invested)
    return {"roic": roic, "nopat": nopat, "invested_capital": invested, "tax_rate_used": tax_rate,
            "reference_wacc": default_wacc,
            "value_creation": (None if roic is None else roic > default_wacc),
            "note": "ROIC > WACC(参考9%) 才创造价值；WACC 实际需按个股 beta/资本结构调整"}


def calc_piotroski_f(ts: Dict[str, Any]) -> Dict[str, Any]:
    p: Dict[str, int] = {}
    ni0 = _ts_get(ts, "income_statement", "net_income", 0)
    ni1 = _ts_get(ts, "income_statement", "net_income", 1)
    a0 = _ts_get(ts, "balance_sheet", "total_assets", 0)
    a1 = _ts_get(ts, "balance_sheet", "total_assets", 1)
    cfo0 = _ts_get(ts, "cashflow", "operating_cashflow", 0)
    roa0, roa1 = _safe_div(ni0, a0), _safe_div(ni1, a1)
    p["roa_positive"] = 1 if (roa0 is not None and roa0 > 0) else 0
    p["cfo_positive"] = 1 if (cfo0 is not None and cfo0 > 0) else 0
    p["roa_rising"] = 1 if (roa0 is not None and roa1 is not None and roa0 > roa1) else 0
    p["accrual_quality"] = 1 if (cfo0 is not None and ni0 is not None and cfo0 > ni0) else 0
    ltd0, ltd1 = _ts_get(ts, "balance_sheet", "long_term_debt", 0), _ts_get(ts, "balance_sheet", "long_term_debt", 1)
    lev0, lev1 = _safe_div(ltd0, a0), _safe_div(ltd1, a1)
    p["leverage_down"] = 1 if (lev0 is not None and lev1 is not None and lev0 < lev1) else 0
    ca0, cl0 = _ts_get(ts, "balance_sheet", "current_assets", 0), _ts_get(ts, "balance_sheet", "current_liabilities", 0)
    ca1, cl1 = _ts_get(ts, "balance_sheet", "current_assets", 1), _ts_get(ts, "balance_sheet", "current_liabilities", 1)
    cr0, cr1 = _safe_div(ca0, cl0), _safe_div(ca1, cl1)
    p["current_ratio_up"] = 1 if (cr0 is not None and cr1 is not None and cr0 > cr1) else 0
    sh0, sh1 = _ts_get(ts, "balance_sheet", "shares", 0), _ts_get(ts, "balance_sheet", "shares", 1)
    p["no_dilution"] = 1 if (sh0 is not None and sh1 is not None and sh0 <= sh1 * 1.001) else 0
    gp0, rev0 = _ts_get(ts, "income_statement", "gross_profit", 0), _ts_get(ts, "income_statement", "total_revenue", 0)
    gp1, rev1 = _ts_get(ts, "income_statement", "gross_profit", 1), _ts_get(ts, "income_statement", "total_revenue", 1)
    gm0, gm1 = _safe_div(gp0, rev0), _safe_div(gp1, rev1)
    p["gross_margin_up"] = 1 if (gm0 is not None and gm1 is not None and gm0 > gm1) else 0
    at0, at1 = _safe_div(rev0, a0), _safe_div(rev1, a1)
    p["asset_turnover_up"] = 1 if (at0 is not None and at1 is not None and at0 > at1) else 0
    score = sum(p.values())
    return {"f_score": score, "max": 9, "breakdown": p,
            "rating": "强" if score >= 7 else ("中" if score >= 4 else "弱"),
            "note": "缺失数据的项默认 0 分，分数偏低可能因数据不全，请结合 breakdown 判断"}


def calc_beneish_m(ts: Dict[str, Any], industry_type: str = "general") -> Dict[str, Any]:
    if industry_type == "financial":
        return {"applicable": False, "reason": "金融业不适用 Beneish M-Score"}
    try:
        rev0, rev1 = _ts_get(ts, "income_statement", "total_revenue", 0), _ts_get(ts, "income_statement", "total_revenue", 1)
        rec0, rec1 = _ts_get(ts, "balance_sheet", "receivables", 0), _ts_get(ts, "balance_sheet", "receivables", 1)
        gp0, gp1 = _ts_get(ts, "income_statement", "gross_profit", 0), _ts_get(ts, "income_statement", "gross_profit", 1)
        ca0, ca1 = _ts_get(ts, "balance_sheet", "current_assets", 0), _ts_get(ts, "balance_sheet", "current_assets", 1)
        ppe0, ppe1 = _ts_get(ts, "balance_sheet", "ppe_net", 0), _ts_get(ts, "balance_sheet", "ppe_net", 1)
        ta0, ta1 = _ts_get(ts, "balance_sheet", "total_assets", 0), _ts_get(ts, "balance_sheet", "total_assets", 1)
        dep0, dep1 = _ts_get(ts, "cashflow", "depreciation", 0), _ts_get(ts, "cashflow", "depreciation", 1)
        sga0, sga1 = _ts_get(ts, "income_statement", "sga", 0), _ts_get(ts, "income_statement", "sga", 1)
        ni0, cfo0 = _ts_get(ts, "income_statement", "net_income", 0), _ts_get(ts, "cashflow", "operating_cashflow", 0)
        ltd0, ltd1 = _ts_get(ts, "balance_sheet", "long_term_debt", 0) or 0, _ts_get(ts, "balance_sheet", "long_term_debt", 1) or 0
        cl0, cl1 = _ts_get(ts, "balance_sheet", "current_liabilities", 0), _ts_get(ts, "balance_sheet", "current_liabilities", 1)
        DSRI = _safe_div(_safe_div(rec0, rev0), _safe_div(rec1, rev1))
        GMI = _safe_div(_safe_div(gp1, rev1), _safe_div(gp0, rev0))
        aqi0 = 1 - ((ca0 + ppe0) / ta0) if None not in (ca0, ppe0, ta0) and ta0 else None
        aqi1 = 1 - ((ca1 + ppe1) / ta1) if None not in (ca1, ppe1, ta1) and ta1 else None
        AQI = _safe_div(aqi0, aqi1)
        SGI = _safe_div(rev0, rev1)
        dr0 = _safe_div(dep0, (dep0 + ppe0)) if None not in (dep0, ppe0) else None
        dr1 = _safe_div(dep1, (dep1 + ppe1)) if None not in (dep1, ppe1) else None
        DEPI = _safe_div(dr1, dr0)
        SGAI = _safe_div(_safe_div(sga0, rev0), _safe_div(sga1, rev1))
        TATA = _safe_div((ni0 - cfo0) if None not in (ni0, cfo0) else None, ta0)
        LVGI = _safe_div(_safe_div((ltd0 + (cl0 or 0)), ta0), _safe_div((ltd1 + (cl1 or 0)), ta1))
        defaults: List[str] = []

        def d(v, name, dv):
            if v is None:
                defaults.append(name)
                return dv
            return v
        DSRI, GMI, AQI, SGI = d(DSRI, "DSRI", 1), d(GMI, "GMI", 1), d(AQI, "AQI", 1), d(SGI, "SGI", 1)
        DEPI, SGAI, TATA, LVGI = d(DEPI, "DEPI", 1), d(SGAI, "SGAI", 1), d(TATA, "TATA", 0), d(LVGI, "LVGI", 1)
        if len(defaults) >= 5:
            return {"applicable": False, "reason": f"明细科目缺失过多({defaults})，M-Score 不可靠，已降级"}
        M = (-4.84 + 0.92 * DSRI + 0.528 * GMI + 0.404 * AQI + 0.892 * SGI
             + 0.115 * DEPI - 0.172 * SGAI + 4.679 * TATA - 0.327 * LVGI)
        return {"applicable": True, "m_score": M, "threshold": -1.78,
                "flag": "可能存在盈余操纵" if M > -1.78 else "未触发操纵信号",
                "variables": {"DSRI": DSRI, "GMI": GMI, "AQI": AQI, "SGI": SGI,
                              "DEPI": DEPI, "SGAI": SGAI, "TATA": TATA, "LVGI": LVGI},
                "defaults_used": defaults,
                "note": "M > -1.78 视为红旗；defaults_used 非空时置信度下降"}
    except Exception as e:
        return {"applicable": False, "reason": f"计算失败: {e}"}


def calc_altman_z(ts: Dict[str, Any], market_cap: Any = None, industry_type: str = "general") -> Dict[str, Any]:
    if industry_type == "financial":
        return {"applicable": False, "reason": "金融业不适用 Altman Z-Score"}
    try:
        ta = _ts_get(ts, "balance_sheet", "total_assets")
        tl = _ts_get(ts, "balance_sheet", "total_liabilities")
        wc = _ts_get(ts, "balance_sheet", "working_capital")
        if wc is None:
            ca, cl = _ts_get(ts, "balance_sheet", "current_assets"), _ts_get(ts, "balance_sheet", "current_liabilities")
            wc = ca - cl if None not in (ca, cl) else None
        re = _ts_get(ts, "balance_sheet", "retained_earnings")
        ebit = _ts_get(ts, "income_statement", "ebit") or _ts_get(ts, "income_statement", "operating_income")
        rev = _ts_get(ts, "income_statement", "total_revenue")
        X1, X2, X3 = _safe_div(wc, ta), _safe_div(re, ta), _safe_div(ebit, ta)
        X4, X5 = _safe_div(market_cap, tl), _safe_div(rev, ta)
        if None in (X1, X2, X3, X4, X5):
            return {"applicable": False, "reason": "关键科目缺失(营运资本/留存收益/EBIT/市值/总负债)，降级为定性"}
        Z = 1.2 * X1 + 1.4 * X2 + 3.3 * X3 + 0.6 * X4 + 1.0 * X5
        zone = "安全" if Z > 2.99 else ("灰色" if Z >= 1.81 else "财务困境")
        return {"applicable": True, "z_score": Z, "zone": zone,
                "variables": {"X1": X1, "X2": X2, "X3": X3, "X4": X4, "X5": X5},
                "note": "Z>2.99 安全 / 1.81-2.99 灰色 / <1.81 困境；仅适用非金融制造与商业企业"}
    except Exception as e:
        return {"applicable": False, "reason": f"计算失败: {e}"}


def calc_earnings_quality(ts: Dict[str, Any]) -> Dict[str, Any]:
    ni = _ts_get(ts, "income_statement", "net_income")
    cfo = _ts_get(ts, "cashflow", "operating_cashflow")
    ta = _ts_get(ts, "balance_sheet", "total_assets")
    cash_conv = _safe_div(cfo, ni)
    accrual = _safe_div((ni - cfo) if None not in (ni, cfo) else None, ta)
    if cash_conv is None:
        rating = "数据不足"
    elif cash_conv >= 1:
        rating = "优(现金含量>=100%)"
    elif cash_conv >= 0.8:
        rating = "良"
    elif cash_conv >= 0.5:
        rating = "一般"
    else:
        rating = "差(利润含金量低)"
    return {"cash_conversion": cash_conv, "accrual_ratio": accrual, "rating": rating,
            "note": "现金含量=经营现金流/净利润，>=1 优；应计比越高盈余质量越差"}


def calc_reverse_dcf(market_cap: Any, base_fcf: Any, r: float = 0.09,
                     g_terminal: float = 0.03, years: int = 10) -> Dict[str, Any]:
    bf, mc = _num(base_fcf), _num(market_cap)
    if bf is None or mc is None or bf <= 0 or mc <= 0:
        return {"applicable": False, "reason": "缺市值或正的基准FCF，反向DCF不适用(亏损/负FCF需改用其他模型)"}

    def pv(g: float) -> float:
        total, f = 0.0, bf
        for t in range(1, years + 1):
            f *= (1 + g)
            total += f / ((1 + r) ** t)
        total += (f * (1 + g_terminal) / (r - g_terminal)) / ((1 + r) ** years)
        return total
    lo, hi = -0.5, 1.0
    if pv(lo) > mc:
        return {"applicable": True, "implied_growth": None,
                "note": "即使 -50% 增速估值仍高于市值，市场预期极低/或深度低估"}
    if pv(hi) < mc:
        return {"applicable": True, "implied_growth": None,
                "note": "即使 +100% 增速估值仍低于市值，市场预期极高/或严重高估"}
    for _ in range(100):
        mid = (lo + hi) / 2
        if pv(mid) < mc:
            lo = mid
        else:
            hi = mid
    g = (lo + hi) / 2
    return {"applicable": True, "implied_growth": g,
            "assumptions": {"discount_rate": r, "terminal_growth": g_terminal,
                            "high_growth_years": years, "base_fcf": bf, "market_cap": mc},
            "note": f"市场价格隐含未来{years}年FCF年复合增速≈{g * 100:.1f}%；与历史CAGR/分析师预期对比判断贵贱"}


def calc_cagr_consistency(ts: Dict[str, Any]) -> Dict[str, Any]:
    def cagr(arr: List[Optional[float]]) -> Optional[float]:
        vals = [v for v in arr if v is not None]
        if len(vals) < 2:
            return None
        first, last, n = vals[0], vals[-1], len(vals) - 1
        if first is None or last is None or last <= 0 or first <= 0:
            return None
        try:
            return (first / last) ** (1 / n) - 1
        except Exception:
            return None
    rev = ts.get("income_statement", {}).get("total_revenue", [])
    ni = ts.get("income_statement", {}).get("net_income", [])
    eq = ts.get("balance_sheet", {}).get("total_equity", [])
    roe_series = [_safe_div(ni[i], eq[i]) for i in range(min(len(ni), len(eq)))]
    roe_vals = [x for x in roe_series if x is not None]
    roe_mean = sum(roe_vals) / len(roe_vals) if roe_vals else None
    roe_min = min(roe_vals) if roe_vals else None
    roe_std = None
    consistency = None
    if len(roe_vals) >= 2 and roe_mean:
        roe_std = (sum((x - roe_mean) ** 2 for x in roe_vals) / len(roe_vals)) ** 0.5
        cv = roe_std / abs(roe_mean) if roe_mean != 0 else None
        if cv is not None:
            consistency = "高(波动小)" if cv < 0.2 else ("中" if cv < 0.5 else "低(波动大)")
    return {"revenue_cagr": cagr(rev), "net_income_cagr": cagr(ni),
            "roe_mean": roe_mean, "roe_min": roe_min, "roe_std": roe_std,
            "roe_consistency": consistency, "periods": len([v for v in rev if v is not None]),
            "note": "周期股需用归一化盈利重算；ROE 一致性高 + 最低值仍>15% 才是真·高质量"}


def _guess_industry_type(sector: Any = None, industry: Any = None) -> str:
    text = " ".join([str(sector or ""), str(industry or "")]).lower()
    fin_kw = ["bank", "insurance", "financial", "capital markets", "asset management",
              "金融", "银行", "保险", "证券"]
    return "financial" if any(k in text for k in fin_kw) else "general"


def compute_models(ts: Dict[str, Any], *, market_cap: Any = None, base_fcf: Any = None,
                   sector: Any = None, industry: Any = None,
                   enable_forensic: bool = True) -> Dict[str, Any]:
    ind = _guess_industry_type(sector, industry)
    models: Dict[str, Any] = {
        "industry_type_guess": ind,
        "_disclaimer": "模型为分析辅助锚，最终结论须结合定性判断；金融/数据不足标的部分模型不适用",
    }

    def run(name, fn):
        try:
            models[name] = fn()
        except Exception as e:
            models[name] = {"error": str(e)}
    run("dupont", lambda: calc_dupont(ts))
    run("roic", lambda: calc_roic(ts))
    run("piotroski_f", lambda: calc_piotroski_f(ts))
    run("earnings_quality", lambda: calc_earnings_quality(ts))
    run("reverse_dcf", lambda: calc_reverse_dcf(market_cap, base_fcf))
    run("growth_consistency", lambda: calc_cagr_consistency(ts))
    if enable_forensic:
        run("beneish_m", lambda: calc_beneish_m(ts, ind))
        run("altman_z", lambda: calc_altman_z(ts, market_cap, ind))
    else:
        models["beneish_m"] = {"applicable": False,
                               "reason": "当前数据源不含完整三大报表明细(如A股摘要)，已跳过造假模型，建议用年报明细补算"}
        models["altman_z"] = {"applicable": False,
                              "reason": "当前数据源不含完整三大报表明细，已跳过破产风险模型，建议用年报明细补算"}
    return models
