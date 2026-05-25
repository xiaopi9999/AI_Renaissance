---
name: llm-arbitration-policy
description: 基于专家 Signal、编排 trace 与外部 MCP 工具完成 Orchestrator LLM 仲裁，输出兼容 ArbitrationResult 的最终交易决策 JSON。
owner_group: 开发2组（Orchestrator）
domain: orchestrator
status: draft
---

# LLM 仲裁策略 Skill

## 1. 适用范围

所属小组：开发2组（Orchestrator）

适用任务：
- 汇总多个专家 Agent 产出的 `SignalBundle`
- 结合编排 trace 检查专家执行失败、超时、无效信号等上下文
- 在必要时调用 MCP 工具补充市场、风险或外部上下文
- 输出兼容 `agents.orchestrator.arbitration.ArbitrationResult` 的最终仲裁结果

边界说明：
- 本 Skill 只定义 LLM 仲裁方法和输出约束，不直接获取原始行情、财报、新闻或股吧数据
- 不替代专家 Skill 的专业判断；专家 Signal 是主要输入，外部 MCP 信息只作为补充证据
- 当专家信号数量不足、方向严重冲突或 MCP 工具不可用时，应降低 `confidence`，必要时输出 `wait` 或 `hold`
- 不得输出超出 schema 的交易指令，例如具体买入价格、止损价或卖出批次

## 2. 输入材料

### 必填输入

- `stock_code`：股票代码
- `signal_bundle`：专家 Agent 产出的信号集合，包含方向、置信度、权重、推理和来源
- `execution_trace`：Orchestrator 编排 trace，包含专家执行状态、失败数、超时数、无效数等
- `available_skills`：当前框架加载到的 Skill 列表
- `required_output_schema`：必须遵守的最终输出结构

### 可选输入

- `mcp_tools`：可用 MCP 工具列表及注册信息
- MCP 工具返回的市场上下文、风险事件、数据质量说明或其他外部证据

### 缺失处理

- 如果 `signal_bundle.signals` 为空，输出 `decision: "wait"`、`direction: "neutral"`，`confidence` 不高于 0.3，`position_ratio` 为 0
- 如果多数专家执行失败或超时，在 `risks` 中说明数据覆盖不足，并降低 `confidence`
- 如果 MCP 工具不可用，不应阻塞仲裁，但必须在 `risks` 或 `reasoning_chain` 中说明外部验证不足

## 3. 仲裁流程

按下面步骤执行：

1. 读取 `signal_bundle`，按方向统计 `bullish`、`bearish`、`neutral` 信号数量。
2. 结合每个信号的 `confidence` 与 `weight`，评估看多、看空和中性证据强度。
3. 检查 `execution_trace`，识别专家失败、超时、无效输出和数据覆盖缺口。
4. 如存在可用 MCP 工具，优先补充与当前标的相关的市场上下文和风险事件。
5. 判断主方向：证据明显偏多时为 `bullish`，明显偏空时为 `bearish`，证据冲突或不足时为 `neutral`。
6. 计算 `confidence`：方向一致性越高、专家覆盖越完整、证据质量越高，置信度越高；反之降低。
7. 计算 `position_ratio`：只在 `direction` 为 `bullish` 或 `bearish` 且 `confidence` 足够时给出非零仓位；中性或等待时为 0。
8. 输出最终 JSON，不添加 Markdown 解释或额外文本。

## 4. 判断规则

### 方向判断

- 多数高置信度加权信号为 `bullish`，且无重大风险抵消时，输出 `direction: "bullish"`
- 多数高置信度加权信号为 `bearish`，或风险类信号强烈负面时，输出 `direction: "bearish"`
- 多空信号接近、核心专家缺失、证据质量不足或外部上下文不明确时，输出 `direction: "neutral"`

### 决策映射

- `direction: "bullish"` 且 `confidence >= 0.6` 时，通常输出 `decision: "buy"`
- `direction: "bearish"` 且 `confidence >= 0.6` 时，通常输出 `decision: "sell"`
- `direction: "neutral"` 或多空证据冲突时，通常输出 `decision: "hold"`
- 输入不足、执行失败较多、外部验证缺失或风险不可判断时，输出 `decision: "wait"`

### 仓位约束

- `position_ratio` 必须在 0 到 1 之间
- `decision` 为 `hold` 或 `wait` 时，`position_ratio` 应为 0
- 非零仓位应随 `confidence`、信号一致性和风险水平调整
- 当风险提示包含重大不确定性、数据缺失或专家执行异常时，应主动下调仓位

### 风险处理

需要重点识别并写入 `risks`：
- 专家信号数量不足
- 关键专家失败、超时或返回无效结果
- 多空信号冲突
- 单一来源信号占比过高
- MCP 外部验证失败或缺失
- 市场上下文存在重大不确定性

## 5. 标准输出

必须只返回一个 JSON 对象，字段如下：

```json
{
  "decision": "buy | hold | sell | wait",
  "direction": "bullish | bearish | neutral",
  "confidence": 0.0,
  "position_ratio": 0.0,
  "reasoning": "",
  "signals_summary": {
    "total": 0,
    "bullish": 0,
    "bearish": 0,
    "neutral": 0,
    "by_type": {}
  },
  "risks": [],
  "reasoning_chain": []
}
```

字段约束：
- `decision` 只能是 `buy`、`hold`、`sell`、`wait`
- `direction` 只能是 `bullish`、`bearish`、`neutral`
- `confidence` 必须是 0 到 1 之间的数字
- `position_ratio` 必须是 0 到 1 之间的数字
- `signals_summary` 必须是对象
- `risks` 必须是字符串数组
- `reasoning_chain` 必须是字符串数组
- `reasoning` 必须是字符串，简明说明最终结论

## 6. 输出示例

```json
{
  "decision": "buy",
  "direction": "bullish",
  "confidence": 0.72,
  "position_ratio": 0.25,
  "reasoning": "多数高置信度专家信号偏多，且未发现足以抵消的重大风险；考虑到部分外部验证不足，仓位保持克制。",
  "signals_summary": {
    "total": 5,
    "bullish": 3,
    "bearish": 1,
    "neutral": 1,
    "by_type": {
      "financial": {"bullish": 1, "bearish": 0, "neutral": 0},
      "news": {"bullish": 1, "bearish": 0, "neutral": 0},
      "risk": {"bullish": 0, "bearish": 1, "neutral": 0}
    }
  },
  "risks": [
    "存在一条风险类负面信号",
    "外部 MCP 市场上下文覆盖不足"
  ],
  "reasoning_chain": [
    "统计专家信号：看多 3 条，看空 1 条，中性 1 条。",
    "看多信号在数量和加权置信度上占优。",
    "风险信号提示需控制仓位。",
    "最终输出 buy，仓位比例 0.25。"
  ]
}
```

## 7. 关联模块

- `agents/orchestrator/arbitration_strategy.py`：加载本 Skill，并将其作为 LLM 仲裁框架的外部裁决方法来源
- `agents/orchestrator/arbitration.py`：定义 `ArbitrationResult` 结构，约束最终输出字段
- `agents/orchestrator/agent.py`：收集专家信号后调用仲裁策略
