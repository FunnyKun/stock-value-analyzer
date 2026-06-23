# API 优先取数协议（API-First Data Source Protocol）

> 配套 `stock-value-analyzer` v1.6 使用。本文档定义 **yfinance + AkShare 双引擎** 数据获取机制，
> 用以替代以前完全依赖 `web_fetch` / `web_search` 抓取网页的做法。
>
> **核心改进（v1.6, 2026-05-05）**：
> - **API 数据 = S 级信源**（高于原有 A 级），因其直接来自交易所/官方数据提供方，机器可读、口径统一、不受网页改版影响
> - **网页抓取降为兜底信源**，仅在 API 取不到、或 API 与 API 之间冲突时调用
> - 引入 `scripts/fetch_stock_data.py` 一键取数脚本，让取数过程可复现、可审计

---

## 一、为什么要用 API 而不是网页抓取？

| 维度 | 网页抓取（v1.5 及以前） | API 取数（v1.6 起） |
|---|---|---|
| **数据精度** | 取决于网页排版稳定性，常出现"展示用近似值" | 直接对接交易所，毫秒/秒级精度 |
| **稳定性** | 网页改版即失效 | 库版本兼容范围内长期稳定 |
| **口径统一** | 各网站口径不一（PE-TTM vs 静态、加权 vs 摊薄） | 库内字段定义清晰、有文档 |
| **取数速度** | 单次 web_fetch 5-15 秒 | 单次 API 调用 0.5-2 秒 |
| **可审计性** | 难以复现（HTML 不留存） | 脚本可重复执行，输出 JSON 可归档 |
| **是否免费** | 免费 | yfinance / AkShare 完全免费、无需 Key |

**实战教训（海尔 06690 v1.2 → v1.3）**：网页抓取曾把 Fintel 网页的研报目标价 HK$31.30 误当作"现价"，
导致整份报告结论反转。**API 直接返回结构化字段 `regularMarketPrice`，根本不会与目标价混淆。**

---

## 二、双引擎职责分工

### 2.1 yfinance — 港股 / 美股 / 全球指数主力

**官网**：<https://github.com/ranaroussi/yfinance>
**安装**：`pip install yfinance`
**调用门槛**：零（无需注册、无需 API Key）
**调用上限**：实测无硬性限制（建议 1 秒 1 次以避免 Yahoo 反爬）

**覆盖能力**：
- ✅ **港股**：以 `0700.HK`（腾讯）、`9988.HK`（阿里巴巴）格式查询
- ✅ **美股 / ADR**：以 `AAPL`、`BABA`、`NVDA` 格式查询
- ✅ **指数**：`^HSI`（恒生）、`^GSPC`（标普 500）、`^NDX`（纳指 100）
- ❌ **A 股**：yfinance 对 A 股支持不稳定（部分代码可查，但财报数据不全），由 AkShare 接管

**核心字段**（`Ticker.info`）：
| API 字段 | 含义 | 对应 Skill 字段 |
|---|---|---|
| `regularMarketPrice` | 实时/最新收盘价 | 当前股价 |
| `marketCap` | 总市值（含非流通股） | 总市值 |
| `trailingPE` | TTM 市盈率 | PE-TTM |
| `forwardPE` | 远期 PE | 辅助估值 |
| `priceToBook` | 市净率 | PB |
| `priceToSalesTrailing12Months` | TTM 市销率 | PS |
| `dividendYield` | 股息率（小数） | 股息率 |
| `payoutRatio` | 派息率 | 派息率 |
| `returnOnEquity` | ROE | ROE |
| `profitMargins` | 净利率 | 净利率 |
| `grossMargins` | 毛利率 | 毛利率 |
| `debtToEquity` | 负债权益比 | 资产负债结构 |
| `freeCashflow` | 自由现金流（TTM） | FCF |
| `operatingCashflow` | 经营现金流（TTM） | 经营 CF |
| `fiftyTwoWeekHigh` / `Low` | 52 周高/低 | 52 周区间（**注意：不是现价**） |
| `recommendationKey` | 分析师评级 | 仅作背景，不可作"目标价" |
| `currency` | 计价币种 | **必须显式标注** |

**财务报表**（三大表）：
```python
ticker.financials       # 利润表（年度）
ticker.quarterly_financials  # 利润表（季度）
ticker.balance_sheet    # 资产负债表
ticker.cashflow         # 现金流量表
```

### 2.2 AkShare — A 股主力 + 港股补充

**官网**：<https://akshare.akfamily.xyz/>
**安装**：`pip install akshare`
**调用门槛**：零（无需注册、无需 API Key）
**调用上限**：无硬性限制；建议每接口 0.5-1 秒间隔

**覆盖能力**：
- ✅ **A 股**：实时行情、历史 K 线、财务报表、股东结构、机构持仓
- ✅ **港股**：行情 + 基本面 + 财务摘要（口径与 A 股一致）
- ✅ **美股**：基础行情（不如 yfinance 全）
- ✅ **行业 / 概念板块**：申万一级二级、东财概念
- ✅ **宏观数据**：CPI、PPI、社融、利率

**常用接口**（A 股）：
| 接口 | 用途 |
|---|---|
| `ak.stock_zh_a_spot_em()` | 全 A 股实时行情快照 |
| `ak.stock_individual_info_em(symbol="000001")` | 单股基本信息（市值、PE、PB、行业） |
| `ak.stock_financial_abstract(symbol="000001")` | 财务摘要（营收、净利、ROE、资产负债等） |
| `ak.stock_zh_a_hist(symbol="000001", period="daily")` | 日线行情 |
| `ak.stock_dividend_cninfo(symbol="000001")` | 历年分红明细 |
| `ak.stock_main_business_em(symbol="000001")` | 主营业务构成 |

**常用接口**（港股）：
| 接口 | 用途 |
|---|---|
| `ak.stock_hk_spot_em()` | 全 港股实时行情 |
| `ak.stock_hk_hist(symbol="00700", period="daily")` | 港股日线 |
| `ak.stock_financial_hk_analysis_indicator_em(symbol="00700")` | 港股财务指标 |
| `ak.stock_hk_indicator_eniu(symbol="00700", indicator="PE")` | 港股 PE/PB/ROE 历史 |

---

## 三、取数优先级（v1.6 强制顺序）

> **核心原则：能用 API 绝不用网页。每往下走一级，置信度递减、报告需相应降级。**

```
Step 0.1 — API 优先
   │
   ├── 港股 / 美股 / 指数 / ADR
   │   └── yfinance 主取（S 级）
   │       └── 失败 / 关键字段缺失 → AkShare 港股接口（S 级）
   │           └── 仍失败 → 走 Step 0.2
   │
   ├── A 股
   │   └── AkShare 主取（S 级）
   │       └── 失败 / 字段缺失 → 走 Step 0.2
   │
   └── 跨市场标的（AH、ADR）
       └── A 股端用 AkShare、H 股端用 yfinance、ADR 用 yfinance
           └── 三端独立取数，禁止汇率换算

Step 0.2 — 网页抓取（兜底）
   │
   ├── 行情终端：富途 / 雪球 / 同花顺
   ├── 第三方数据库：StockAnalysis.com / Investing.com / Yahoo 网页版
   └── 交易所官方：HKEX / 上交所 / 深交所 / SEC EDGAR

Step 0.3 — 冲突仲裁
   │
   └── API 与 API 冲突 → 以交易所官方页面为准
       API 与网页冲突 → 以 API 为准（除非 API 数据明显异常，如停牌期）
```

### 3.1 何时必须降级到网页抓取？

只有以下情况才允许直接使用网页：
1. ✅ API 接口报错或返回空（如个别港股 yfinance 返回 `None`）
2. ✅ 关键字段在 API 中没有对应字段（如"管理层近期言论"、"近 30 天监管事件"）
3. ✅ 需要交叉验证 API 返回值合理性（API 返回 PE = 1000 时需用网页核实）
4. ✅ 行业数据 / 市占率 / 竞争格局 等定性信息

### 3.2 何时禁止只用网页？

凡是落入下表的字段，**必须先尝试 API**，API 取到即采用，不再走网页：

| 字段 | API 来源 | 理由 |
|---|---|---|
| 当前股价 / 收盘价 | yfinance / AkShare | API 直接对接交易所 |
| 市值 / PE / PB / PS | yfinance / AkShare | 库内字段定义清晰 |
| 股息率 / 派息率 | yfinance / AkShare | 库自动剔除特别股息口径 |
| ROE / 净利率 / 毛利率 | yfinance / AkShare | 直接读财报 |
| 三大报表（年度 / 季度） | yfinance.financials / AkShare | 结构化输出 |
| 52 周高低 | yfinance | 自动算 |
| 分红历史 | AkShare.stock_dividend_cninfo | 含派息日 |

---

## 四、信源等级映射（v1.6 升级）

原 Skill v1.5 的信源等级体系扩展为 **六级**，新增最顶层 **🟢🟢🟢🟢 Tier 0：API 数据**：

| 视觉等级 | 英文 | 含义 | 典型来源 |
|:---:|---|---|---|
| 🟢🟢🟢🟢 | **Tier 0（v1.6 新增）** | **S 级 / API 直取** | yfinance / AkShare / 富途 OpenD / 交易所官方 API |
| 🟢🟢🟢 | **Tier 1** | **一级官方源** | 公司年报/公告、HKEX/SEC 披露页、政府监管部门 |
| 🟢🟢 | **Tier 2** | **二级权威源** | Bloomberg/Reuters/FT/WSJ/财新、专业数据库网页 |
| 🟢 | **Tier 3** | **三级参考源** | 36 氪 / 搜狐 / 东财 / 车家号 |
| 🟡 | **Tier 4** | **单源/弱信源** | 仅 1 源、二级互抄无原始源 |
| 🔴 | **Tier 5** | **传闻/小道** | 股吧、知乎匿名、供应链传闻 |

**采信规则更新**：
- **价格类数据**：1 个 🟢🟢🟢🟢（API）即可视为已满足"3 源"基本要求，但**仍建议补 1-2 个网页源做交叉**
- **财务指标**：1 个 🟢🟢🟢🟢（API）+ 1 个 🟢🟢 / 🟢🟢🟢 网页源即可
- **若仅有 API 单源**：必须在标注栏显式写 `(yfinance 单源 / 暂无网页交叉)`，不视为弱信源（API 自身就是高置信度），但读者可知风险

---

## 五、调用脚本规范

### 5.1 标准调用入口

所有报告启动时，**第一动作**应该是执行：

```bash
python .codebuddy/skills/stock-value-analyzer/scripts/fetch_stock_data.py \
  --symbol 0700.HK \
  --market HK \
  --output ./account/_temp_value_analysis_0700.json
```

参数说明：
- `--symbol`：股票代码（港股 `XXXX.HK`、A 股纯数字 `000001` / `600519`、美股 `AAPL`）
- `--market`：`HK` / `A` / `US`（决定调用 yfinance 还是 AkShare）
- `--output`：输出 JSON 路径（默认 `./account/_temp_value_analysis_<symbol>.json`）

### 5.2 输出 JSON 标准格式

```json
{
  "meta": {
    "symbol": "0700.HK",
    "market": "HK",
    "fetch_time_utc": "2026-05-05T04:12:33Z",
    "engines_used": ["yfinance"],
    "engines_failed": []
  },
  "price": {
    "current": 380.20,
    "currency": "HKD",
    "previous_close": 378.40,
    "fifty_two_week_high": 423.60,
    "fifty_two_week_low": 285.20,
    "data_type": "regular_market_price",
    "source": "yfinance"
  },
  "valuation": {
    "market_cap": 3578000000000,
    "trailing_pe": 18.62,
    "forward_pe": 16.10,
    "price_to_book": 4.20,
    "price_to_sales_ttm": 5.85,
    "source": "yfinance"
  },
  "profitability": {
    "return_on_equity": 0.2150,
    "profit_margins": 0.2820,
    "gross_margins": 0.5210,
    "operating_margins": 0.3110,
    "source": "yfinance"
  },
  "financials": {
    "total_revenue_ttm": 660000000000,
    "net_income_ttm": 186000000000,
    "free_cashflow_ttm": 178000000000,
    "operating_cashflow_ttm": 220000000000,
    "total_debt": 350000000000,
    "debt_to_equity": 35.10,
    "source": "yfinance"
  },
  "dividend": {
    "dividend_yield": 0.0085,
    "payout_ratio": 0.20,
    "source": "yfinance"
  },
  "errors": []
}
```

### 5.3 报告中的引用方式

报告 Step 0 的校验记录表中，对每个由 API 取得的字段，**信源列直接填写 `yfinance@2026-05-05` 或 `akshare@2026-05-05`**，并在等级列标注 🟢🟢🟢🟢。

```markdown
| 字段 | 数据类型 | 采用值 | 信源 1（API） | 信源 2 | 信源 3 | 信源等级 | 取数时间 |
|---|---|---|---|---|---|:---:|---|
| 港股 0700 现价 | 实时收盘价 | HK$380.20 | yfinance@2026-05-05 | 富途 380.20 | 雪球 380.20 | 🟢🟢🟢🟢 + 🟢🟢🟢 + 🟢🟢 | 2026-05-05 16:08 HKT |
| PE-TTM | TTM-GAAP | 18.62x | yfinance@2026-05-05 | StockAnalysis 18.55 | — | 🟢🟢🟢🟢 + 🟢🟢 | 2026-05-05 |
```

---

## 六、Step 8 复验与 API 协同

Step 8.1 关键数据复验时，**复验信源不得与 Step 0 完全重叠**：

- 若 Step 0 用了 yfinance → Step 8 应换 AkShare 港股接口 + 1 个网页源
- 若 Step 0 用了 AkShare → Step 8 应换网页源（雪球 / 东财） + （可选）富途 OpenD
- **API → API 复验** 与 **API → 网页 复验** 都允许，但至少要换一个引擎

---

## 七、常见 API 陷阱（v1.6 起替换原 Red Flags）

| 🚩 陷阱 | 说明 | 应对 |
|---|---|---|
| yfinance 对部分港股返回空 `info` | Yahoo 数据库偶尔丢失少数港股 | AkShare 港股接口兜底 |
| `dividendYield` 是小数（0.0085）不是百分比 | 易被当 0.85% 误读为 0.0085% | 报告中显式 ×100 标注 |
| `trailingPE` 在亏损公司返回 `None` | yfinance 不会返回负 PE | 改用 PS / PB / EV/EBITDA |
| AkShare A 股代码不带 sh/sz 前缀 | 如 600519、000001 | 注意接口签名差异 |
| AkShare 港股代码 5 位带前导零 | 腾讯是 `00700` 不是 `0700` | 与 yfinance 的 `0700.HK` 区分 |
| 财报数据有 1-2 季度滞后 | API 财报口径以最新已披露季报为准 | 报告中标注"截至 YYYY-Qn 财报" |
| 货币单位混淆 | 港股财报有时以人民币计、有时港元计 | 必读 `currency` 字段 |
| TTM 与最新年度不同 | yfinance.financials 给年度，info 给 TTM | 报告中明确口径 |

---

## 八、降级与异常处理

```python
# 伪代码 - 取数优先级降级逻辑
def fetch_price(symbol, market):
    try:
        if market in ("HK", "US"):
            data = yfinance_fetch(symbol)
            if data["price"]: return data, "yfinance"
        elif market == "A":
            data = akshare_fetch_a(symbol)
            if data["price"]: return data, "akshare"

        # 一级降级：换引擎
        if market == "HK":
            data = akshare_fetch_hk(symbol)
            if data["price"]: return data, "akshare"

        # 二级降级：网页（让上层 web_fetch 处理）
        return None, "fallback_to_web"
    except Exception as e:
        log(f"API 异常: {e}")
        return None, "fallback_to_web"
```

报告中必须如实标注最终采用的引擎，禁止"伪装成 API 取数实际用了网页"。

---

## 八·补、v2.0 新增字段组：`time_series` 与 `models`

> v2.0 起，`fetch_stock_data.py` 在原九大字段组之外，调用 `scripts/value_models.py` 自动计算并输出两组新字段。原九组契约不变，向后兼容。

### `time_series`（时间序列，index 0 = 最近期）

| 子字段 | 含义 |
|---|---|
| `revenue` / `net_income` | 营收 / 归母净利 5-10 年序列 |
| `gross_margin` / `roe` | 毛利率 / ROE 序列 |
| `fcf` / `operating_cf` | 自由现金流 / 经营现金流序列 |
| `shares` | 股本序列（用于股权稀释检查） |
| `years` | 对应年份标签 |

### `models`（量化模型输出）

| 子字段 | 含义 | applicable 规则 |
|---|---|---|
| `dupont` | 杜邦三/五因子分解 | 数据足即可 |
| `roic` | ROIC vs WACC | 数据足即可 |
| `piotroski_f` | F-Score（0-9） | 数据足即可 |
| `beneish_m` | M-Score（盈余操纵） | **金融股 / 数据不足 → applicable=False**；缺失变量用中性值代入并记 `defaults_used`，缺≥5 个 → False |
| `altman_z` | Z-Score（破产风险） | **金融股 → applicable=False** |
| `earnings_quality` | 盈余质量（现金含量/应计比） | 数据足即可 |
| `reverse_dcf` | 反向 DCF 隐含增速 | **缺 market_cap / base_fcf（如 A 股）→ applicable=False** |
| `growth_consistency` | CAGR + ROE 一致性 | 数据足即可 |

### 引用铁律

1. 报告引用任何量化指标，**必须先检查该模型 `applicable` 字段**；为 `False` 时不得下量化结论，改用定性判断并标注 "NA（数据不足/不适用）"。
2. **A 股限制**：`stock_financial_abstract` 为摘要级，缺三大报表明细，故 A 股调用时 `enable_forensic=False`、`base_fcf=None`，M-Score/Z-Score/反向 DCF 多为 NA。**禁止用经营现金流冒充 FCF。**
3. 序列方向统一为"从新到旧"（index 0 最近期），跨模型一致。
4. 报告附录须附 `models`/`time_series` 原始输出快照，保证可审计、可复现。

---

## 九、版本记录

- **v2.0（2026-06-22）** — 新增 `time_series` 与 `models` 两大字段组，由 `scripts/value_models.py` 自动计算（杜邦/ROIC/F/M/Z-Score/盈余质量/反向 DCF/CAGR 一致性）；明确 applicable 护栏与 A 股数据限制；保持原九组向后兼容。
- **v1.6（2026-05-05）** — 初版。建立 yfinance + AkShare 双引擎机制，新增 Tier 0 信源等级，新增 `scripts/fetch_stock_data.py` 一键脚本。
