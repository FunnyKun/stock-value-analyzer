# 时间序列趋势分析规范（Time-Series Analysis）

> 配套 `stock-value-analyzer` v2.0 使用。本规范要求所有分析**从单时点快照升级为 5-10 年趋势**。
> 多年序列由 `scripts/value_models.py` 自动提取，写入取数 JSON 的 `time_series` 字段；
> CAGR / ROE 一致性由脚本算入 `models.growth_consistency`。
>
> **核心理念**：单看一年的 ROE/毛利率会被周期、会计调节、一次性损益骗到。
> **一家公司的质量藏在"波动模式"里，不在某一年的数字里。** 巴菲特看的是"十年如一日"的稳定高回报。

---

## 〇、为什么强制时序

| 只看单时点 | 看 5-10 年序列 |
|---|---|
| ROE 25% "真高" | 发现是从 8% 靠加杠杆冲到 25%，质量存疑 |
| 毛利率 40% "不错" | 发现连续 5 年从 55% 滑到 40%，护城河在被侵蚀 |
| 净利大增 "成长股" | 发现营收没动、靠投资收益/补贴，盈余质量差 |
| PE 5 倍 "便宜" | 发现处于周期景气顶点，归一化后 PE 其实 20 倍（陷阱） |

---

## 一、强制提取的序列（脚本 `time_series` 字段）

| 报表 | 关键科目（脚本 key） |
|---|---|
| 利润表 | `total_revenue, gross_profit, operating_income, ebit, pretax_income, tax_provision, net_income, interest_expense, sga` |
| 资产负债表 | `total_assets, total_liabilities, total_equity, current_assets, current_liabilities, cash, receivables, inventory, ppe_net, total_debt, long_term_debt, retained_earnings, working_capital, shares` |
| 现金流量表 | `operating_cashflow, capex, free_cashflow, depreciation, dividends_paid` |

- 序列顺序：**从新到旧**（index 0 = 最近一期）。`fiscal_periods` 给对应期末日期。
- yfinance 通常返回近 4 年年报；如需更长，报告可用网页/年报补充并标注信源。
- A 股摘要源（`source=akshare_abstract`）仅含营收/净利/资产/权益/经营现金流，**明细趋势需用年报补**。

---

## 二、必做的趋势判读（报告"时间序列趋势"章节）

### 2.1 ROE 一致性（脚本 `models.growth_consistency`）

| 指标 | 字段 | 判读 |
|---|---|---|
| ROE 均值 | `roe_mean` | 长期 > 15% 才算高回报 |
| ROE 最小值 | `roe_min` | **最差年份仍 > 15% 才是真·稳定优质** |
| ROE 波动 | `roe_consistency` | "高(波动小)" 优；"低(波动大)" 警惕周期/质量问题 |

> **巴菲特式标尺**：连续 7-10 年 ROE 稳定 > 15%（且非高杠杆驱动）= 强护城河的量化印证。

### 2.2 毛利率/净利率趋势

- **持续上升或高位平稳** → 定价权强/成本护城河（加分）。
- **持续下行** → 护城河侵蚀/竞争加剧（扣分，且是 M-Score 的 GMI 红旗来源）。
- 配合杜邦的 `ebit_margin` 看经营盈利趋势。

### 2.3 收入与利润 CAGR（脚本 `revenue_cagr` / `net_income_cagr`）

```
CAGR = (最新值 / 最早值)^(1/年数) - 1
```

- **净利 CAGR 长期 > 营收 CAGR** → 盈利能力提升（好）；
- **净利 CAGR << 营收 CAGR** → 增收不增利（费用失控/价格战）；
- CAGR 是**反向 DCF 隐含增速的对比基准**（见 `valuation-models.md`）。

### 2.4 FCF 趋势与含金量

- FCF 长期为正且增长 → 现金牛（好）；
- 利润增长但 FCF 长期为负 → 靠烧钱维持，结合盈余质量（现金含量）判断。

### 2.5 股本趋势（稀释 vs 回购）

- `shares` 序列持续上升 → 股权稀释（小心"增长靠增发买来"）；
- 持续下降 → 回购注销，每股价值提升（资本配置加分，见行业资本配置手册）。

---

## 三、周期股归一化盈利（关键，防顶部陷阱）

周期股（资源/化工/航运/地产/部分半导体）**不能用景气顶点的利润和低 PE 估值**。

### 3.1 归一化方法

```
正常化利润率 = 5-10 年平均(净利率 或 EBIT利润率)
正常化盈利   = 当前营收 × 正常化利润率
正常化 PE    = 当前市值 / 正常化盈利
```

### 3.2 判读

| 现象 | 含义 |
|---|---|
| 景气顶点：当前 PE 低、归一化 PE 高 | 🔴 顶部陷阱（利润不可持续，低 PE 是幻觉） |
| 景气底部：当前 PE 高/亏损、归一化 PE 低 | 🟢 可能的逆向机会（需行业未结构性衰退） |

> 与 `contrarian-checklist.md`、`valuation-traps.md` 联动：周期股的"便宜"必须用归一化盈利复核。

---

## 四、趋势对评分的加减分规则（注入五大模块）

| 趋势信号 | 影响模块 | 调整方向 |
|---|---|---|
| ROE 连续 7 年 > 15% 且波动小 | 公司-护城河/财务 | 加分（量化印证护城河） |
| 毛利率连续 3 年下行 | 行业-定价权 / 公司-护城河 | 扣分 |
| 净利 CAGR < 营收 CAGR（增收不增利） | 公司-盈利能力 | 扣分 |
| FCF 长期为负 + 利润为正 | 公司-财务健康 / 盈余质量 | 扣分 |
| 股本持续稀释 | 公司-管理层(资本配置) | 扣分 |
| 反向 DCF 隐含增速 >> 历史 CAGR | 估值 | 扣分（高估） |
| 周期股景气顶点（归一化 PE 远高于表观 PE） | 估值 | 强制按归一化重估 |

---

## 五、数据不足时的降级

- 序列 < 3 年：CAGR/一致性不可靠，报告标注"序列过短，趋势判断降级为定性"。
- A 股摘要源缺明细：毛利率/FCF/股本趋势需用年报补；未补则在报告中明示"该趋势项数据缺失"。
- 任何"数据缺失"项**不得静默跳过**，必须在报告"时间序列趋势"章节列出缺失清单。

---

## 六、脚本字段速查

```
time_series.fiscal_periods                       # 各期末日期（从新到旧）
time_series.income_statement.{...}               # 利润表多年序列
time_series.balance_sheet.{...}                  # 资产负债表多年序列
time_series.cashflow.{...}                       # 现金流量表多年序列
models.growth_consistency.{revenue_cagr, net_income_cagr, roe_mean, roe_min, roe_consistency, periods}
```

> 报告"时间序列趋势"章节的每个结论，须能回溯到 `time_series.*` 的具体序列；可在报告中贴出 5 年小表佐证。
