"""A股三表回归检查：合成数据，不访问网络、不依赖本地财务文件。"""
import sys
sys.dont_write_bytecode = True

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pandas as pd
import fetch_stock_data as fetch
import value_models as vm

ROOT = Path(__file__).resolve().parents[1]
APIS = ("stock_profit_sheet_by_report_em", "stock_balance_sheet_by_report_em",
        "stock_cash_flow_sheet_by_report_em")


def check():
    temp = ROOT / f".check-a-{uuid4().hex}"
    temp.mkdir()
    try:
        for code in ("600519", "000651", "601398"):
            # 金额为合成值；金融业由三表ORG_TYPE识别，不依赖基本信息接口。
            row = {"REPORT_DATE": "2025-12-31", "UPDATE_DATE": "2026-04-01",
                   "ORG_TYPE": "银行" if code == "601398" else "通用",
                   "OPERATE_INCOME": 1000, "OPERATE_COST": 400, "OPERATE_PROFIT": 300,
                   "TOTAL_PROFIT": 300, "INCOME_TAX": 75, "NETPROFIT": 225,
                   "FE_INTEREST_EXPENSE": 10, "SALE_EXPENSE": 50, "MANAGE_EXPENSE": 30,
                   "TOTAL_ASSETS": 2000, "TOTAL_LIABILITIES": 600, "TOTAL_EQUITY": 1400,
                   "TOTAL_CURRENT_ASSETS": 800, "TOTAL_CURRENT_LIAB": 300,
                   "MONETARYFUNDS": 200, "ACCOUNTS_RECE": 100, "FIXED_ASSET": 600,
                   "LONG_LOAN": 200, "SHORT_LOAN": 50, "SHARE_CAPITAL": 100,
                   "SURPLUS_RESERVE": 100, "UNASSIGN_RPOFIT": 500,
                   "NETCASH_OPERATE": 350, "FA_IR_DEPR": 50, "CONSTRUCT_LONG_ASSET": 80}
            old = {**row, "REPORT_DATE": "2024-12-31", "NETCASH_OPERATE": 300}
            stale = {**row, "UPDATE_DATE": "2026-03-01", "NETCASH_OPERATE": 1}
            # 季报、重复旧记录及仅单表拥有的年报不得影响共同年报序列。
            tables = {name: pd.DataFrame([row, old, stale, {**row, "REPORT_DATE": "2026-06-30"}])
                      for name in APIS}
            tables[APIS[0]] = pd.concat([tables[APIS[0]], pd.DataFrame([{**row, "REPORT_DATE": "2023-12-31"}])])
            ts = vm.extract_time_series_akshare_a_reports(*(tables[name] for name in APIS))
            assert ts["fiscal_periods"] == ["2025-12-31", "2024-12-31"]
            assert ts["cashflow"]["free_cashflow"] == [270, 220]
            missing = tables[APIS[2]].drop(columns=["CONSTRUCT_LONG_ASSET"])
            assert vm.extract_time_series_akshare_a_reports(tables[APIS[0]], tables[APIS[1]], missing)["cashflow"]["free_cashflow"][0] is None

            fake = SimpleNamespace(__version__="mock",
                stock_individual_info_em=lambda **kw: pd.DataFrame({"item": ["总市值"], "value": [5000]}),
                stock_financial_abstract=lambda **kw: pd.DataFrame({"选项": ["常用"], "指标": ["营业收入"], "20251231": [1000]}),
                stock_zh_a_spot_em=lambda: pd.DataFrame([{"代码": code, "最新价": 10, "总市值": 5000}]))
            if code != "000651":
                fake.interface_info = lambda name: {"name": name, "source": "mock"}
            for name in APIS:
                setattr(fake, name, lambda name=name, **kw: tables[name].sample(frac=1, random_state=1))
            with patch.dict(sys.modules, {"akshare": fake}), patch.object(fetch.Path, "cwd", return_value=temp):
                result = fetch.fetch_all(code, "A")
                assert result["time_series"]["source"] == "akshare_a_report_em", result["errors"]
                assert not result["errors"]
                for model in ("beneish_m", "altman_z", "reverse_dcf"):
                    assert result["models"][model]["applicable"] == (code != "601398")
                files = result["financials_detail"]["files"]
                assert len(files) == 3
                original = Path(files[0]["file"]).read_bytes()
                assert json.loads(Path(files[0]["file"]).with_suffix(".json").read_text(encoding="utf-8"))["parameters"]["symbol"] == ("SH" if code.startswith("6") else "SZ") + code
                fake.stock_cash_flow_sheet_by_report_em = lambda **kw: (_ for _ in ()).throw(RuntimeError("模拟接口失败"))
                fallback = fetch.fetch_all(code, "A")
                assert fallback["time_series"]["source"] == "akshare_abstract" and fallback["errors"]
                assert not fallback["models"]["reverse_dcf"]["applicable"]
                assert Path(files[0]["file"]).read_bytes() == original
    finally:
        assert temp.resolve().is_relative_to(ROOT.resolve())
        shutil.rmtree(temp)
    print("PASS: 三表对齐/去重、FCF/缺失、沪深参数、M/Z/DCF、金融业限制、接口兼容、失败回退、原始数据留存")


if __name__ == "__main__":
    check()
