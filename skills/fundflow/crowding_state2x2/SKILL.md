---
name: crowding-state2x2
description: 资金拥挤度四象限状态诊断。基于主力净流入z-score（资金流轴）和累计流入/流通市值历史分位（拥挤度轴）交叉分类，将个股归入四象限（早期趋势/晚期趋势/出货/反转）或中性区，输出方向信号与警示。当用户询问某只股票的资金拥挤状态、四象限诊断、拥挤度警示、资金流极端状态时触发。
owner_group: 专家3组（资金）
domain: fundflow
status: draft
---

# 资金拥挤度四象限状态诊断 Skill

## 1. 适用范围

所属小组：专家3组（资金）

适用任务：
- 对单只A股进行资金拥挤度四象限状态诊断，判断当前处于哪个象限
- 基于「资金流强度」和「拥挤度水平」两个维度交叉分类，给出结构化警示信号
- 为仲裁层提供资金面的方向信号（看多/看空/中性），辅助综合判断

象限定义（基于 Kyle-Obizhaeva 2018 与高盛 Tactical Flow of Funds 实践）：

| 象限 | 拥挤度 | 资金流 | 含义 | 操作建议 | direction |
|------|--------|--------|------|---------|-----------|
| State 1: EarlyTrend | 低（≤30%） | 高流入（z≥+1） | 早期趋势，资金刚涌入，拥挤尚低 | 跟随 | bullish |
| State 2: LateTrend | 高（≥70%） | 高流入（z≥+1） | 晚期趋势，资金涌入但已拥挤 | 警惕反转，减仓 | bearish |
| State 3: Distribution | 低（≤30%） | 高流出（z≤-1） | 出货阶段，资金撤离但拥挤低 | 中性等待 | neutral |
| State 4: Reversal | 高（≥70%） | 高流出（z≤-1） | 反转信号，拥挤+流出双杀 | 均值回归，逆向做多 | bullish |
| State 0: Neutral | 中间区域 | 中间区域 | 未触达极端阈值 | 无明确信号 | neutral |

边界说明：
- 本 Skill 产出的是资金面状态信号，不单独构成交易建议
- 四象限分类基于统计阈值，在趋势延续期可能持续停留在某象限，象限切换才是关键信号
- 当前仅使用 CumFlowOverFloat 单一拥挤度子指标（融资余额和龙虎榜数据暂缺），拥挤度维度覆盖不完整
- 需要至少 60 个交易日的历史数据才能计算 z-score 和历史分位，数据不足时置信度降低

## 2. 输入材料

### 必填输入

- `stock_code`：6位A股股票代码（如"600519"）
- 资金流向数据（money_flow_df）：
  - `NetAmountMain`：主力净流入（万元）
  - `NetAmountX`：超大单净流入（万元）
  - `NetAmountL`：大单净流入（万元）
  - `NetAmountM`：中单净流入（万元）
  - `NetAmountS`：小单净流入（万元）
- 日线行情数据（daily_df）：
  - `Close`：收盘价
  - `Volume`：成交量
  - `Amount`：成交额（万元）
- 股票属性数据（prop_df）：
  - `CirculatingMarketCap`：流通市值（万元）
  - `IsPaused`：是否停牌
  - `IsST`：是否ST

### 可选输入

- 融资余额数据（MarginBalance）：用于补充拥挤度子指标2，暂缺时自动降权
- 龙虎榜数据（DragonTigerFreq）：用于补充拥挤度子指标3，暂缺时自动降权
- 人工补充观点：对当前象限的定性判断

### 缺失处理

- 如果 CirculatingMarketCap 缺失：使用 ADV_20 × 240 作为流通市值代理，在 `meta.uncertainties` 说明
- 如果主力净流入数据全部缺失：输出 `direction: "neutral"`，`confidence` 不高于 0.2，`meta.needs_human_review: true`
- 如果历史数据不足 60 个交易日：仍可计算但 `confidence` 降低，在 `meta.uncertainties` 写明"历史数据不足60日，统计指标可靠性下降"
- 如果融资余额和龙虎榜数据缺失（当前常态）：拥挤度仅使用 CumFlowOverFloat 单指标，在 `meta.uncertainties` 说明"拥挤度仅基于资金流/市值比单一维度"

## 3. 分析步骤

1. **明确分析对象**：确认股票代码，加载该股票的资金流、行情和属性数据

2. **检查数据充足性**：确认至少有 60 个交易日的主力净流入数据；若不足，降低置信度并标注

3. **计算资金流强度（Y轴）**：
   - 计算 `Flow_zscore = (NetAmountMain - rolling_mean_60d) / rolling_std_60d`，按个股分组滚动计算
   - z-score ≥ +1.0 为高流入，≤ -1.0 为高流出

4. **计算拥挤度水平（X轴）**：
   - 计算 `CumFlowOverFloat = rolling_sum(NetAmountMain, 60d) / CirculatingMarketCap`
   - 计算 `CompositeCrowding_Pct = CumFlowOverFloat 的 126 日历史分位`（0~1）
   - 分位 ≥ 70% 为高拥挤，≤ 30% 为低拥挤
   - 若有融资余额/龙虎榜数据，等权合成三个子指标的历史分位

5. **四象限分类**：
   - 交叉 Flow_zscore 与 CompositeCrowding_Pct，按阈值判定象限
   - 阈值：拥挤度高=70%，拥挤度低=30%，资金流高=+1.0，资金流低=-1.0

6. **计算交互项**：`Flow_x_Crowding = Flow_zscore × CompositeCrowding_Pct`，用于仲裁层增量判断

7. **确定方向和置信度**：根据象限和极端程度给出 direction 和 confidence

8. **输出标准 JSON**

## 4. 判断规则

### 4.1 象限判定规则

| 条件 | 象限 | direction | 说明 |
|------|------|-----------|------|
| CompositeCrowding_Pct ≤ 0.30 且 Flow_zscore ≥ 1.0 | EarlyTrend | bullish | 低拥挤+高流入，趋势启动 |
| CompositeCrowding_Pct ≥ 0.70 且 Flow_zscore ≥ 1.0 | LateTrend | bearish | 高拥挤+高流入，趋势末期 |
| CompositeCrowding_Pct ≤ 0.30 且 Flow_zscore ≤ -1.0 | Distribution | neutral | 低拥挤+高流出，出货阶段 |
| CompositeCrowding_Pct ≥ 0.70 且 Flow_zscore ≤ -1.0 | Reversal | bullish | 高拥挤+高流出，反转信号 |
| 其他（拥挤度 30%-70% 或 z-score -1 到 +1） | Neutral | neutral | 未触达极端区域 |

### 4.2 置信度规则

- **0.7 - 0.9**：象限明确，两个维度均显著超阈值（如 Flow_zscore > 2.0 或 CompositeCrowding_Pct > 0.90）
- **0.5 - 0.7**：象限明确，但维度刚过阈值
- **0.3 - 0.5**：象限刚进入，或历史数据不足，或拥挤度仅基于单一子指标
- **< 0.3**：数据严重不足，使用 neutral 并标注人工复核

提升置信度的因素：
- Flow_zscore 绝对值越大（> 2.0），资金流信号越强
- CompositeCrowding_Pct 越极端（> 0.90 或 < 0.10），拥挤度信号越强
- 象限刚刚切换（前一日在不同象限或中性区），信号更值得关注
- 历史回测显示该象限方向显著（如 State 2 bearish, State 4 bullish）

降低置信度的因素：
- 拥挤度仅基于单一子指标（CumFlowOverFloat），融资余额和龙虎榜缺失
- 历史数据不足 60 个交易日
- 股票为 ST 或近期停牌复牌，数据连续性受影响
- Flow_zscore 和 CompositeCrowding_Pct 方向矛盾（一个极端、一个中性）

### 4.3 风险等级规则

- `low`：处于 Neutral 象限，或 EarlyTrend 象限且资金流入温和
- `medium`：处于 LateTrend（高拥挤+高流入，反转风险积聚），或 Distribution（资金撤离中）
- `high`：处于 Reversal（高拥挤+高流出，可能剧烈波动），或 LateTrend 且 Flow_zscore > 2.0

### 4.4 时间周期规则

- 资金流 z-score 基于 60 日滚动 → `short`（天到数周）
- 累计流入/市值比分位基于 126 日滚动 → `mid`（数周到数月）
- 综合判断取 `short`，因为象限切换信号偏短期

### 4.5 特殊规则

- **象限切换检测**：如果前一个交易日处于不同象限或中性区，本次象限为「切换信号」，在 `meta.key_findings` 中标注"象限刚从 X 切换至 Y"
- **双极端预警**：如果 Flow_zscore > 2.0 且 CompositeCrowding_Pct > 0.90，标记为「双极端」，`risk_level` 升为 high
- **停牌/ST过滤**：如果 IsPaused=1 或 IsST=1，输出 neutral 并在 `meta.uncertainties` 说明

## 5. 标准输出

### State 2: LateTrend 示例

```json
{
  "direction": "bearish",
  "confidence": 0.65,
  "reasoning": "贵州茅台(600519)当前处于State2-LateTrend（晚期趋势）：拥挤度分位78%（高拥挤），主力净流入z-score=1.52（显著流入）。高拥挤+高流入组合预示趋势末期，历史回测显示该状态后续收益显著低于中性，警惕反转风险。",
  "signals": [
    "拥挤度分位78%，超过70%高阈值",
    "主力净流入z-score=1.52，超过+1.0阈值",
    "四象限状态: LateTrend（晚期趋势）",
    "高拥挤+高流入，警惕反转"
  ],
  "source": "crowding_state2x2",
  "signal_type": "fundflow",
  "stock_code": "600519",
  "weight": 1.0,
  "meta": {
    "output_version": "0.1",
    "skill_name": "crowding_state2x2",
    "owner_group": "专家3组（资金）",
    "target": "资金拥挤度四象限诊断",
    "period": "2026-05-12",
    "time_horizon": "short",
    "risk_level": "medium",
    "key_findings": [
      "四象限状态: LateTrend（高拥挤+高流入，晚期趋势）",
      "拥挤度分位78%，主力净流入z-score=1.52",
      "累计流入/流通市值=3.2%，126日分位78%",
      "拥挤度仅基于CumFlowOverFloat单指标（融资余额/龙虎榜暂缺）"
    ],
    "evidence": [
      {
        "source_type": "fund_flow",
        "source_name": "资金组 money_flow_df",
        "date": "2026-05-12",
        "metric": "主力净流入z-score",
        "value": "1.52",
        "comparison": "超过+1.0高流入阈值",
        "note": "60日滚动z-score，当日主力净流入显著高于近60日均值"
      },
      {
        "source_type": "fund_flow",
        "source_name": "资金组 money_flow_df + prop_df",
        "date": "2026-05-12",
        "metric": "累计流入/流通市值 126日分位",
        "value": "78%",
        "comparison": "超过70%高拥挤阈值",
        "note": "近60日累计主力净流入占流通市值3.2%，处于126日历史分位78%"
      },
      {
        "source_type": "market_data",
        "source_name": "资金组 daily_df",
        "date": "2026-05-12",
        "metric": "收盘价/ADV_20",
        "value": "1354.55 / 48.2亿",
        "comparison": "正常交易状态",
        "note": "非停牌非ST，数据连续性良好"
      }
    ],
    "risk_notes": [
      "LateTrend状态不意味着立即反转，趋势可能持续一段时间",
      "拥挤度仅基于资金流/市值比单一维度，缺少融资余额和龙虎榜交叉验证",
      "若有利好政策或业绩超预期，高流入可能有基本面支撑"
    ],
    "uncertainties": [
      "融资余额数据暂缺，拥挤度维度覆盖不完整",
      "龙虎榜数据暂缺，无法验证游资参与度",
      "象限分类基于固定阈值(70%/30%/±1)，不同市场阶段最优阈值可能不同"
    ],
    "needs_human_review": false,
    "state2x2_detail": {
      "flow_zscore": 1.52,
      "composite_crowding_pct": 0.78,
      "state_code": 2,
      "state_label": "LateTrend",
      "state_desc": "高拥挤+高流入：晚期趋势，警惕反转",
      "flow_x_crowding": 1.1856,
      "cum_flow_over_float": 0.032,
      "adv_20": 4820000000,
      "net_amount_main_latest": -1122886784,
      "close_price": 1354.55,
      "circulating_market_cap": 170320000000
    }
  }
}
```

### State 4: Reversal 示例

```json
{
  "direction": "bullish",
  "confidence": 0.55,
  "reasoning": "XXX(000001)当前处于State4-Reversal（反转信号）：拥挤度分位82%（高拥挤），主力净流入z-score=-1.83（显著流出）。高拥挤+高流出组合为均值回归信号，历史回测该状态后续收益显著高于中性，但需警惕趋势延续风险。",
  "signals": [
    "拥挤度分位82%，超过70%高阈值",
    "主力净流入z-score=-1.83，低于-1.0阈值",
    "四象限状态: Reversal（反转信号）",
    "高拥挤+高流出，均值回归信号"
  ],
  "source": "crowding_state2x2",
  "signal_type": "fundflow",
  "stock_code": "000001",
  "weight": 1.0,
  "meta": {
    "output_version": "0.1",
    "skill_name": "crowding_state2x2",
    "owner_group": "专家3组（资金）",
    "target": "资金拥挤度四象限诊断",
    "period": "2026-05-12",
    "time_horizon": "short",
    "risk_level": "high",
    "key_findings": [
      "四象限状态: Reversal（高拥挤+高流出，反转信号）",
      "拥挤度分位82%，主力净流入z-score=-1.83",
      "均值回归逻辑：资金流出+拥挤出清后可能反弹"
    ],
    "evidence": [
      {
        "source_type": "fund_flow",
        "source_name": "资金组 money_flow_df",
        "date": "2026-05-12",
        "metric": "主力净流入z-score",
        "value": "-1.83",
        "comparison": "低于-1.0高流出阈值",
        "note": "60日滚动z-score，当日主力净流出显著"
      },
      {
        "source_type": "fund_flow",
        "source_name": "资金组 money_flow_df + prop_df",
        "date": "2026-05-12",
        "metric": "累计流入/流通市值 126日分位",
        "value": "82%",
        "comparison": "超过70%高拥挤阈值",
        "note": "历史累计流入仍处高位，但正在快速流出"
      }
    ],
    "risk_notes": [
      "Reversal信号不意味着立即反弹，资金流出可能持续",
      "高拥挤出清过程可能伴随剧烈波动",
      "若基本面恶化，资金流出可能是理性定价而非过度反应"
    ],
    "uncertainties": [
      "融资余额数据暂缺，拥挤度维度覆盖不完整",
      "反转时点无法精确判断"
    ],
    "needs_human_review": true,
    "state2x2_detail": {
      "flow_zscore": -1.83,
      "composite_crowding_pct": 0.82,
      "state_code": 4,
      "state_label": "Reversal",
      "state_desc": "高拥挤+高流出：反转信号，均值回归",
      "flow_x_crowding": -1.5006
    }
  }
}
```

### State 0: Neutral 示例

```json
{
  "direction": "neutral",
  "confidence": 0.3,
  "reasoning": "贵州茅台(600519)当前处于State0-Neutral（中性区）：拥挤度分位52%（中间区域），主力净流入z-score=0.35（中间区域），均未触达极端阈值，无明确方向信号。",
  "signals": [
    "拥挤度分位52%，处于30%-70%中间区域",
    "主力净流入z-score=0.35，处于-1到+1中间区域",
    "四象限状态: Neutral（中性区）"
  ],
  "source": "crowding_state2x2",
  "signal_type": "fundflow",
  "stock_code": "600519",
  "weight": 1.0,
  "meta": {
    "output_version": "0.1",
    "skill_name": "crowding_state2x2",
    "owner_group": "专家3组（资金）",
    "target": "资金拥挤度四象限诊断",
    "period": "2026-05-12",
    "time_horizon": "short",
    "risk_level": "low",
    "key_findings": [
      "四象限状态: Neutral（中性区），资金面无极端信号",
      "拥挤度分位52%，主力净流入z-score=0.35"
    ],
    "evidence": [
      {
        "source_type": "fund_flow",
        "source_name": "资金组 money_flow_df",
        "date": "2026-05-12",
        "metric": "主力净流入z-score",
        "value": "0.35",
        "comparison": "处于-1到+1中间区域",
        "note": "资金流入不显著"
      },
      {
        "source_type": "fund_flow",
        "source_name": "资金组 money_flow_df + prop_df",
        "date": "2026-05-12",
        "metric": "累计流入/流通市值 126日分位",
        "value": "52%",
        "comparison": "处于30%-70%中间区域",
        "note": "拥挤度处于正常区间"
      }
    ],
    "risk_notes": [],
    "uncertainties": [
      "融资余额数据暂缺，拥挤度维度覆盖不完整"
    ],
    "needs_human_review": false,
    "state2x2_detail": {
      "flow_zscore": 0.35,
      "composite_crowding_pct": 0.52,
      "state_code": 0,
      "state_label": null,
      "state_desc": null,
      "flow_x_crowding": 0.182
    }
  }
}
```

## 6. 质量检查

输出前检查：

- 是否明确标注四象限状态（EarlyTrend/LateTrend/Distribution/Reversal/Neutral）
- `direction` 是否符合象限逻辑（EarlyTrend→bullish, LateTrend→bearish, Distribution→neutral, Reversal→bullish, Neutral→neutral）
- `confidence` 是否根据极端程度和数据充足性合理设定
- `signal_type` 是否为 `fundflow`
- 证据是否写明 Flow_zscore 和 CompositeCrowding_Pct 的数值和阈值比较
- 是否在 `meta.uncertainties` 中说明拥挤度维度覆盖不完整的问题
- 是否在 `meta.state2x2_detail` 中提供完整计算细节供下游追溯
- 是否标注了 `meta.needs_human_review`（Reversal 和数据不足时为 true）
- 停牌/ST股票是否被过滤并输出 neutral
