"""
AI Renaissance 赛博朋克风格 HTML 报告生成器

生成深蓝黑底色、青色荧光强调、毛玻璃卡片、ECharts图表、CSS动画的科技感综合分析报告。

v3.0 修复：
- 信号指标结构化 HTML 表格渲染（不再显示 JSON）
- Agent 卡片置信度数值突出显示
- 雷达图增加总平均分 + 各维度分值标注
- 推理链 Markdown → 结构化 HTML 渲染
"""

import json
import re
from datetime import datetime
from typing import Dict, Any, List, Optional


# ═══════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════

def _render_signal_indicators(signals: list) -> str:
    """
    将 signals 列表渲染为结构化 HTML 表格。
    支持 dict 格式: {"indicator": "...", "value": "...", "signal": "..."}
    也支持纯字符串格式: "营收增长20%" (回退为 tag)
    """
    if not signals:
        return ""

    html_parts = ['<div class="indicator-table">']

    for s in signals[:6]:
        if isinstance(s, dict):
            indicator = s.get("indicator", "")
            value = s.get("value", "")
            signal_type = s.get("signal", "neutral")

            sig_map = {
                "bullish": ("看多", "var(--red)"),
                "bearish": ("看空", "var(--green)"),
                "neutral": ("中性", "var(--gray)"),
                "warning": ("预警", "var(--orange)"),
                "buy": ("买入", "var(--red)"),
                "sell": ("卖出", "var(--green)"),
            }
            sig_label, sig_color = sig_map.get(signal_type, (signal_type, "var(--gray)"))

            html_parts.append(
                f'<div class="indicator-row">'
                f'<span class="ind-name">{_esc(indicator)}</span>'
                f'<span class="ind-value">{_esc(value)}</span>'
                f'<span class="ind-signal" style="color:{sig_color}">{sig_label}</span>'
                f'</div>'
            )
        elif isinstance(s, str):
            html_parts.append(
                f'<div class="indicator-row">'
                f'<span class="ind-name" style="flex:1">{_esc(s)}</span>'
                f'</div>'
            )

    html_parts.append('</div>')
    return "".join(html_parts)


def _esc(text: str) -> str:
    """HTML 转义"""
    if not text:
        return ""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


def _parse_markdown_to_html(md_text: str) -> str:
    """
    将 Markdown 推理链文本解析为结构化 HTML。

    支持:
    - ## 标题 → h3
    - **粗体** → strong
    - - 列表项 → ul > li
    - 普通段落 → p
    """
    if not md_text:
        return ""

    html_parts = []
    lines = md_text.split("\n")
    in_list = False
    in_paragraph = False

    for line in lines:
        stripped = line.strip()

        # 空行：关闭当前段落和列表
        if not stripped:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            if in_paragraph:
                html_parts.append("</p>")
                in_paragraph = False
            continue

        # ## 标题
        if stripped.startswith("## "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            if in_paragraph:
                html_parts.append("</p>")
                in_paragraph = False
            title_text = stripped[3:].strip()
            # 处理标题中的 **粗体**
            title_text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', title_text)
            html_parts.append(f'<div class="rc-section-title">{title_text}</div>')
            continue

        # # 单个 # 标题
        if stripped.startswith("# "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            if in_paragraph:
                html_parts.append("</p>")
                in_paragraph = False
            title_text = stripped[2:].strip()
            title_text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', title_text)
            html_parts.append(f'<div class="rc-main-title">{title_text}</div>')
            continue

        # ### 三级标题
        if stripped.startswith("### "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            if in_paragraph:
                html_parts.append("</p>")
                in_paragraph = False
            title_text = stripped[4:].strip()
            title_text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', title_text)
            html_parts.append(f'<div class="rc-sub-title">{title_text}</div>')
            continue

        # - 列表项 (支持嵌套: "  - xxx" 是嵌套)
        if stripped.startswith("- "):
            if in_paragraph:
                html_parts.append("</p>")
                in_paragraph = False
            if not in_list:
                html_parts.append('<ul class="rc-list">')
                in_list = True
            indent = line.startswith("  -") or line.startswith("\t-")
            item_text = stripped[2:].strip()  # 去掉 "- "
            item_text = _process_inline_formatting(item_text)
            cls = "rc-list-item nested" if indent else "rc-list-item"
            html_parts.append(f'<li class="{cls}">{item_text}</li>')
            continue

        # 普通段落文本
        text = _process_inline_formatting(stripped)
        if in_list:
            html_parts.append("</ul>")
            in_list = False
        if not in_paragraph:
            html_parts.append('<p class="rc-paragraph">')
            in_paragraph = True
        else:
            html_parts.append("<br>")
        html_parts.append(text)

    # 关闭未闭合标签
    if in_list:
        html_parts.append("</ul>")
    if in_paragraph:
        html_parts.append("</p>")

    return "".join(html_parts)


def _process_inline_formatting(text: str) -> str:
    """处理行内格式：**粗体**、`代码`"""
    # **粗体**
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong class="rc-bold">\1</strong>', text)
    # `行内代码`
    text = re.sub(r'`(.+?)`', r'<code class="rc-code">\1</code>', text)
    return text


# ═══════════════════════════════════════════════════════════
# 主生成函数
# ═══════════════════════════════════════════════════════════

def generate_html_report(
    stock_code: str,
    stock_name: str,
    price_data: Dict[str, Any],
    arbitration_result: Any,
    agent_signals: List[Dict[str, Any]],
) -> str:
    ar = arbitration_result
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 决策映射
    decision_map = {
        "buy": ("建议买入", "#ff4757"),
        "sell": ("建议卖出", "#2ed573"),
        "hold": ("建议持有", "#ffa502"),
        "wait": ("建议观望", "#a4b0be"),
    }
    direction_map = {
        "bullish": ("看多", "#ff4757"),
        "bearish": ("看空", "#2ed573"),
        "neutral": ("中性", "#a4b0be"),
    }

    dec_label, dec_color = decision_map.get(
        getattr(ar, "decision", ""), (getattr(ar, "decision", "--"), "#a4b0be")
    )
    dir_label, dir_color = direction_map.get(
        getattr(ar, "direction", ""), (getattr(ar, "direction", "--"), "#a4b0be")
    )

    price = price_data.get("price", "--")
    change_pct = price_data.get("change_pct", 0) or 0
    change_color = "#ff4757" if change_pct >= 0 else "#2ed573"
    change_sign = "+" if change_pct >= 0 else ""
    market_cap = price_data.get("market_cap", "--")
    pe = price_data.get("pe", "--")

    # 置信度圆环
    confidence = getattr(ar, "confidence", 0) or 0
    conf_pct = confidence * 100
    conf_circumference = 2 * 3.14159 * 54
    conf_offset = conf_circumference * (1 - confidence)

    # ═══════ Agent 信号卡片 ═══════
    agent_cards_html = ""
    for idx, sig in enumerate(agent_signals):
        s_dir, s_color = direction_map.get(sig.get("direction", "neutral"), ("中性", "#a4b0be"))
        s_conf = (sig.get("confidence", 0) or 0) * 100
        s_name = sig.get("name", "Unknown")
        s_reasoning = sig.get("reasoning", "")
        s_signals = sig.get("signals", [])
        s_meta = sig.get("meta", {})
        s_data_status = s_meta.get("data_status", "live")
        status_tag = '<span class="data-badge live">LIVE</span>' if s_data_status == "live" else '<span class="data-badge mock">OFFLINE</span>'

        # 风险 Agent 额外显示风险警告
        risk_warnings = s_meta.get("risk_warnings", [])
        risk_html = ""
        if risk_warnings:
            risk_items = "".join(f'<li class="risk-warn-item">{_esc(w)}</li>' for w in risk_warnings[:3])
            risk_html = f'<div class="risk-warnings"><ul>{risk_items}</ul></div>'

        # 结构化指标表格
        indicator_html = _render_signal_indicators(s_signals)

        # 推理文本：展开全部，不做截断
        reasoning_display = _esc(s_reasoning) if s_reasoning else ""

        agent_cards_html += f"""
        <div class="agent-card" style="animation-delay: {idx * 0.08}s">
            <div class="agent-card-header">
                <span class="agent-name">{_esc(s_name)}</span>
                {status_tag}
            </div>
            <div class="agent-card-meta">
                <span class="agent-direction" style="color: {s_color}">{s_dir}</span>
                <span class="agent-conf-badge" style="color: {s_color}; border-color: {s_color}40">{s_conf:.0f}%</span>
            </div>
            <div class="agent-conf-bar">
                <div class="agent-conf-fill" style="width: {s_conf}%; background: {s_color}"></div>
            </div>
            {indicator_html}
            <div class="agent-reasoning">{reasoning_display}</div>
            {risk_html}
        </div>"""

    # 信号汇总
    summary = getattr(ar, "signals_summary", {}) or {}
    total = summary.get("total", 0)
    bullish_count = summary.get("bullish", 0)
    bearish_count = summary.get("bearish", 0)
    neutral_count = summary.get("neutral", 0)

    # ═══════ 推理链：Markdown → HTML ═══════
    reasoning_chain = getattr(ar, "reasoning_chain", "") or ""
    reasoning_html = _parse_markdown_to_html(reasoning_chain)

    # 风险项
    risks = getattr(ar, "risks", []) or []
    risks_items = "".join(f'<li class="risk-item">{_esc(r)}</li>' for r in risks)

    # ═══════ 雷达图数据 ═══════
    radar_names = [s.get("name", "")[:6] for s in agent_signals]
    radar_values = [int((s.get("confidence", 0) or 0) * 100) for s in agent_signals]
    radar_avg = int(sum(radar_values) / len(radar_values)) if radar_values else 0
    radar_data = json.dumps({"names": radar_names, "values": radar_values, "avg": radar_avg})

    # 置信度维度详情列表
    dim_details_html = ""
    for i, sig in enumerate(agent_signals):
        val = (sig.get("confidence", 0) or 0) * 100
        d = direction_map.get(sig.get("direction", "neutral"), ("中性", "#a4b0be"))
        dim_details_html += (
            f'<div class="dim-item">'
            f'<span class="dim-name">{_esc(sig.get("name", "")[:6])}</span>'
            f'<div class="dim-bar-bg"><div class="dim-bar-fill" style="width:{val:.0f}%;background:{d[1]}"></div></div>'
            f'<span class="dim-val" style="color:{d[1]}">{val:.0f}</span>'
            f'</div>'
        )

    # ═══════ 组装 HTML ═══════
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Renaissance | {_esc(stock_name)} ({_esc(stock_code)}) 投资分析报告</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Inter:wght@300;400;600;700&display=swap');

:root {{
    --bg-primary: #060a13;
    --bg-secondary: #0d1321;
    --bg-card: rgba(13, 19, 33, 0.85);
    --border: rgba(0, 229, 255, 0.12);
    --border-bright: rgba(0, 229, 255, 0.3);
    --cyan: #00e5ff;
    --cyan-dim: rgba(0, 229, 255, 0.15);
    --purple: #b388ff;
    --red: #ff4757;
    --green: #2ed573;
    --orange: #ffa502;
    --gray: #a4b0be;
    --text: #e8ecf1;
    --text-dim: #636e82;
    --glow-cyan: 0 0 20px rgba(0, 229, 255, 0.3);
    --glow-red: 0 0 20px rgba(255, 71, 87, 0.3);
    --font-mono: 'JetBrains Mono', 'SF Mono', monospace;
    --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}}

* {{ margin: 0; padding: 0; box-sizing: border-box; }}

body {{
    font-family: var(--font-sans);
    background: var(--bg-primary);
    color: var(--text);
    min-height: 100vh;
    overflow-x: hidden;
}}

body::before {{
    content: '';
    position: fixed;
    inset: 0;
    background:
        linear-gradient(rgba(0, 229, 255, 0.03) 1px, transparent 1px),
        linear-gradient(90deg, rgba(0, 229, 255, 0.03) 1px, transparent 1px);
    background-size: 60px 60px;
    pointer-events: none;
    z-index: 0;
}}

body::after {{
    content: '';
    position: fixed;
    top: -50%;
    left: -50%;
    width: 200%;
    height: 200%;
    background: radial-gradient(ellipse at 30% 20%, rgba(0, 229, 255, 0.06) 0%, transparent 50%),
                radial-gradient(ellipse at 70% 80%, rgba(179, 136, 255, 0.04) 0%, transparent 50%);
    pointer-events: none;
    z-index: 0;
}}

.container {{
    max-width: 960px;
    margin: 0 auto;
    padding: 40px 24px 80px;
    position: relative;
    z-index: 1;
}}

/* ── 头部 ── */
.header {{
    text-align: center;
    margin-bottom: 48px;
    animation: fadeInDown 0.6s ease-out;
}}

.header .brand {{
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 4px;
    text-transform: uppercase;
    color: var(--cyan);
    opacity: 0.7;
    margin-bottom: 16px;
}}

.header .stock-title {{ font-size: 36px; font-weight: 700; margin-bottom: 4px; }}
.header .stock-code {{ font-family: var(--font-mono); font-size: 14px; color: var(--text-dim); margin-bottom: 20px; }}

.header .price-row {{
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 24px;
    flex-wrap: wrap;
}}

.header .price {{ font-family: var(--font-mono); font-size: 42px; font-weight: 700; letter-spacing: -1px; }}

.header .price-change {{
    font-family: var(--font-mono);
    font-size: 18px;
    padding: 4px 12px;
    border-radius: 4px;
    background: rgba(255, 255, 255, 0.05);
}}

.header .meta-tags {{
    display: flex;
    gap: 12px;
    justify-content: center;
    margin-top: 12px;
    flex-wrap: wrap;
}}

.meta-tag {{
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--text-dim);
    padding: 4px 10px;
    border: 1px solid var(--border);
    border-radius: 4px;
}}

/* ── 最终裁决卡片 ── */
.verdict-card {{
    background: var(--bg-card);
    border: 1px solid var(--border-bright);
    border-radius: 16px;
    padding: 36px;
    margin-bottom: 36px;
    backdrop-filter: blur(20px);
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    gap: 32px;
    align-items: center;
    animation: fadeInUp 0.6s ease-out 0.1s both;
    position: relative;
    overflow: hidden;
}}

.verdict-card::before {{
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, transparent, var(--cyan), transparent);
}}

.verdict-left {{ text-align: center; }}

.verdict-decision {{
    font-size: 28px;
    font-weight: 700;
    margin-bottom: 8px;
    text-shadow: 0 0 30px currentColor;
}}

.verdict-direction {{ font-size: 16px; margin-bottom: 16px; }}

.verdict-position {{
    font-family: var(--font-mono);
    font-size: 13px;
    color: var(--text-dim);
    padding: 6px 14px;
    border: 1px solid var(--border);
    border-radius: 6px;
    display: inline-block;
}}

/* ── 置信度圆环 ── */
.confidence-ring {{
    position: relative;
    width: 140px;
    height: 140px;
}}

.confidence-ring svg {{ transform: rotate(-90deg); }}

.confidence-ring .ring-bg {{
    fill: none;
    stroke: rgba(255, 255, 255, 0.06);
    stroke-width: 8;
}}

.confidence-ring .ring-fill {{
    fill: none;
    stroke: var(--cyan);
    stroke-width: 8;
    stroke-linecap: round;
    stroke-dasharray: {conf_circumference:.2f};
    stroke-dashoffset: {conf_offset:.2f};
    transition: stroke-dashoffset 1.5s ease-out;
    filter: drop-shadow(0 0 8px var(--cyan));
}}

.confidence-ring .ring-text {{
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
}}

.confidence-ring .ring-value {{
    font-family: var(--font-mono);
    font-size: 36px;
    font-weight: 700;
    color: var(--cyan);
}}

.confidence-ring .ring-label {{
    font-size: 11px;
    color: var(--text-dim);
    letter-spacing: 2px;
    text-transform: uppercase;
}}

.verdict-right {{ text-align: center; }}

.signal-stats {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
}}

.stat-box {{
    text-align: center;
    padding: 12px 8px;
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.03);
}}

.stat-value {{ font-family: var(--font-mono); font-size: 24px; font-weight: 700; }}
.stat-label {{ font-size: 11px; color: var(--text-dim); margin-top: 4px; }}

/* ── 区域标题 ── */
.section-title {{
    font-family: var(--font-mono);
    font-size: 12px;
    letter-spacing: 3px;
    text-transform: uppercase;
    color: var(--cyan);
    margin-bottom: 20px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
    animation: fadeInUp 0.6s ease-out 0.2s both;
}}

/* ══════════════════════════════════════
   Agent 信号面板 — 结构化输出
   ══════════════════════════════════════ */
.agents-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 16px;
    margin-bottom: 36px;
}}

.agent-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    backdrop-filter: blur(12px);
    animation: fadeInUp 0.5s ease-out both;
    transition: border-color 0.3s, box-shadow 0.3s;
}}

.agent-card:hover {{
    border-color: var(--border-bright);
    box-shadow: var(--glow-cyan);
}}

.agent-card-header {{
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
}}

.agent-name {{
    font-weight: 600;
    font-size: 14px;
    flex: 1;
}}

.agent-card-meta {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
}}

.agent-direction {{
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 700;
}}

.agent-conf-badge {{
    font-family: var(--font-mono);
    font-size: 16px;
    font-weight: 700;
    padding: 2px 10px;
    border-radius: 6px;
    border: 1.5px solid;
    background: rgba(255,255,255,0.03);
}}

.data-badge {{
    font-family: var(--font-mono);
    font-size: 9px;
    letter-spacing: 1px;
    padding: 2px 6px;
    border-radius: 3px;
    font-weight: 700;
}}

.data-badge.live {{
    background: rgba(46, 213, 115, 0.15);
    color: var(--green);
    border: 1px solid rgba(46, 213, 115, 0.3);
}}

.data-badge.mock {{
    background: rgba(255, 165, 2, 0.15);
    color: var(--orange);
    border: 1px solid rgba(255, 165, 2, 0.3);
}}

.agent-conf-bar {{
    height: 4px;
    background: rgba(255, 255, 255, 0.06);
    border-radius: 2px;
    margin-bottom: 12px;
    overflow: hidden;
}}

.agent-conf-fill {{
    height: 100%;
    border-radius: 2px;
    transition: width 1s ease-out;
}}

/* ── 结构化指标表格 ── */
.indicator-table {{
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-bottom: 12px;
    padding: 10px;
    background: rgba(0, 229, 255, 0.03);
    border-radius: 8px;
    border: 1px solid rgba(0, 229, 255, 0.06);
}}

.indicator-row {{
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 0;
}}

.ind-name {{
    font-size: 12px;
    color: var(--text-dim);
    min-width: 100px;
    flex-shrink: 0;
}}

.ind-value {{
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--text);
    font-weight: 600;
    flex: 1;
}}

.ind-signal {{
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 3px;
    background: rgba(255,255,255,0.04);
    flex-shrink: 0;
}}

.agent-reasoning {{
    font-size: 12px;
    color: var(--text-dim);
    line-height: 1.6;
    margin-top: 8px;
}}

/* ── 风险警告 (Agent 卡片内) ── */
.risk-warnings {{
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid rgba(255, 71, 87, 0.1);
}}

.risk-warnings ul {{ list-style: none; }}

.risk-warn-item {{
    padding: 3px 0;
    font-size: 11px;
    color: #ff6b7a;
}}

.risk-warn-item::before {{
    content: '\u26A0 ';
    margin-right: 4px;
}}

/* ══════════════════════════════════════
   雷达图 — 含总分和维度分值
   ══════════════════════════════════════ */
.radar-section {{
    margin-bottom: 36px;
    animation: fadeInUp 0.6s ease-out 0.4s both;
}}

.radar-container {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
    backdrop-filter: blur(12px);
}}

.radar-top {{
    display: flex;
    align-items: flex-start;
    gap: 24px;
}}

#radarChart {{
    flex: 1;
    min-width: 0;
    height: 360px;
}}

.radar-sidebar {{
    width: 200px;
    flex-shrink: 0;
}}

.radar-total-score {{
    text-align: center;
    padding: 16px;
    border-radius: 10px;
    background: rgba(0, 229, 255, 0.06);
    border: 1px solid rgba(0, 229, 255, 0.15);
    margin-bottom: 12px;
}}

.radar-total-label {{
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--text-dim);
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 6px;
}}

.radar-total-value {{
    font-family: var(--font-mono);
    font-size: 40px;
    font-weight: 700;
    color: var(--cyan);
    text-shadow: 0 0 20px rgba(0, 229, 255, 0.4);
}}

.radar-dim-title {{
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--text-dim);
    letter-spacing: 1px;
    margin-bottom: 8px;
    text-transform: uppercase;
}}

.dim-item {{
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 6px;
}}

.dim-name {{
    font-size: 10px;
    color: var(--text-dim);
    width: 48px;
    flex-shrink: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}}

.dim-bar-bg {{
    flex: 1;
    height: 6px;
    background: rgba(255,255,255,0.06);
    border-radius: 3px;
    overflow: hidden;
}}

.dim-bar-fill {{
    height: 100%;
    border-radius: 3px;
    transition: width 1s ease-out;
}}

.dim-val {{
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 700;
    width: 28px;
    text-align: right;
    flex-shrink: 0;
}}

/* ══════════════════════════════════════
   推理链 — 结构化渲染
   ══════════════════════════════════════ */
.reasoning-section {{
    margin-bottom: 36px;
    animation: fadeInUp 0.6s ease-out 0.5s both;
}}

.reasoning-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 28px;
    backdrop-filter: blur(12px);
}}

.rc-main-title {{
    font-size: 20px;
    font-weight: 700;
    color: var(--cyan);
    margin-bottom: 16px;
    padding-bottom: 10px;
    border-bottom: 1px solid rgba(0, 229, 255, 0.15);
}}

.rc-section-title {{
    font-size: 16px;
    font-weight: 600;
    color: var(--purple);
    margin-top: 20px;
    margin-bottom: 12px;
    padding-left: 12px;
    border-left: 3px solid var(--purple);
}}

.rc-sub-title {{
    font-size: 14px;
    font-weight: 600;
    color: var(--text);
    margin-top: 16px;
    margin-bottom: 8px;
    padding-left: 10px;
    border-left: 2px solid var(--cyan);
}}

.rc-paragraph {{
    font-size: 13px;
    color: var(--text-dim);
    line-height: 1.8;
    margin-bottom: 8px;
}}

.rc-bold {{
    color: var(--text);
    font-weight: 600;
}}

.rc-code {{
    font-family: var(--font-mono);
    font-size: 12px;
    background: rgba(0, 229, 255, 0.08);
    color: var(--cyan);
    padding: 1px 6px;
    border-radius: 3px;
}}

.rc-list {{
    list-style: none;
    margin: 8px 0 12px 0;
    padding-left: 0;
}}

.rc-list-item {{
    position: relative;
    padding: 6px 0 6px 20px;
    font-size: 13px;
    color: var(--text-dim);
    line-height: 1.7;
    border-bottom: 1px solid rgba(255, 255, 255, 0.02);
}}

.rc-list-item::before {{
    content: '';
    position: absolute;
    left: 4px;
    top: 14px;
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--cyan);
    opacity: 0.6;
}}

.rc-list-item.nested {{
    padding-left: 36px;
}}

.rc-list-item.nested::before {{
    left: 20px;
    width: 4px;
    height: 4px;
    background: var(--purple);
}}

/* ── 风险提示 ── */
.risk-section {{
    margin-bottom: 36px;
    animation: fadeInUp 0.6s ease-out 0.6s both;
}}

.risk-card {{
    background: rgba(255, 71, 87, 0.06);
    border: 1px solid rgba(255, 71, 87, 0.2);
    border-radius: 12px;
    padding: 24px;
}}

.risk-card ul {{ list-style: none; }}

.risk-item {{
    padding: 6px 0;
    font-size: 13px;
    color: #ff6b7a;
    line-height: 1.5;
}}

.risk-item::before {{
    content: '\u26A0 ';
    margin-right: 4px;
}}

/* ── 底部 ── */
.footer {{
    text-align: center;
    padding: 40px 0 0;
    border-top: 1px solid var(--border);
    animation: fadeInUp 0.6s ease-out 0.7s both;
}}

.footer .disclaimer {{
    font-size: 11px;
    color: var(--text-dim);
    line-height: 1.8;
    max-width: 600px;
    margin: 0 auto;
}}

.footer .timestamp {{
    font-family: var(--font-mono);
    font-size: 10px;
    color: rgba(255, 255, 255, 0.15);
    margin-top: 16px;
    letter-spacing: 1px;
}}

/* ── 动画 ── */
@keyframes fadeInUp {{
    from {{ opacity: 0; transform: translateY(20px); }}
    to {{ opacity: 1; transform: translateY(0); }}
}}

@keyframes fadeInDown {{
    from {{ opacity: 0; transform: translateY(-20px); }}
    to {{ opacity: 1; transform: translateY(0); }}
}}

/* ── 响应式 ── */
@media (max-width: 768px) {{
    .verdict-card {{
        grid-template-columns: 1fr;
        text-align: center;
    }}
    .confidence-ring {{ margin: 0 auto; }}
    .header .stock-title {{ font-size: 28px; }}
    .header .price {{ font-size: 32px; }}
    .agents-grid {{ grid-template-columns: 1fr; }}
    .radar-top {{ flex-direction: column; }}
    .radar-sidebar {{ width: 100%; }}
    #radarChart {{ height: 280px; }}
}}

@media (max-width: 480px) {{
    .ind-name {{ min-width: 70px; font-size: 11px; }}
    .ind-value {{ font-size: 11px; }}
}}
</style>
</head>
<body>

<div class="container">

    <!-- 头部 -->
    <div class="header">
        <div class="brand">AI Renaissance &middot; Multi-Agent Intelligence</div>
        <h1 class="stock-title">{_esc(stock_name)}</h1>
        <div class="stock-code">{_esc(stock_code)}</div>
        <div class="price-row">
            <span class="price" style="color: {change_color}">{price}</span>
            <span class="price-change" style="color: {change_color}; border-color: {change_color}33">{change_sign}{change_pct:.2f}%</span>
        </div>
        <div class="meta-tags">
            <span class="meta-tag">\u5E02\u503C {market_cap}</span>
            <span class="meta-tag">PE {pe}</span>
            <span class="meta-tag">{now}</span>
        </div>
    </div>

    <!-- 最终裁决 -->
    <div class="verdict-card">
        <div class="verdict-left">
            <div class="verdict-decision" style="color: {dec_color}">{dec_label}</div>
            <div class="verdict-direction" style="color: {dir_color}">{dir_label}</div>
            <span class="verdict-position">\u5EFA\u8BAE\u4ED3\u4F4D {getattr(ar, 'position_ratio', 0) or 0:.0%}</span>
        </div>
        <div class="confidence-ring">
            <svg width="140" height="140" viewBox="0 0 140 140">
                <circle class="ring-bg" cx="70" cy="70" r="54"/>
                <circle class="ring-fill" cx="70" cy="70" r="54"/>
            </svg>
            <div class="ring-text">
                <span class="ring-value">{conf_pct:.0f}%</span>
                <span class="ring-label">\u7F6E\u4FE1\u5EA6</span>
            </div>
        </div>
        <div class="verdict-right">
            <div class="signal-stats">
                <div class="stat-box">
                    <div class="stat-value" style="color: var(--cyan)">{total}</div>
                    <div class="stat-label">\u603B\u8BA1</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value" style="color: var(--red)">{bullish_count}</div>
                    <div class="stat-label">\u770B\u591A</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value" style="color: var(--green)">{bearish_count}</div>
                    <div class="stat-label">\u770B\u7A7A</div>
                </div>
            </div>
        </div>
    </div>

    <!-- 七专家信号 -->
    <div class="section-title">// 07 Expert Agents Analysis</div>
    <div class="agents-grid">
        {agent_cards_html}
    </div>

    <!-- 雷达图 + 总分 + 维度分值 -->
    <div class="radar-section">
        <div class="section-title">// Confidence Radar</div>
        <div class="radar-container">
            <div class="radar-top">
                <div id="radarChart"></div>
                <div class="radar-sidebar">
                    <div class="radar-total-score">
                        <div class="radar-total-label">\u7EFC\u5408\u5747\u5206</div>
                        <div class="radar-total-value">{radar_avg}</div>
                    </div>
                    <div class="radar-dim-title">\u5404\u7EF4\u5EA6\u5206\u503C</div>
                    {dim_details_html}
                </div>
            </div>
        </div>
    </div>

    <!-- 推理链 -->
    <div class="reasoning-section">
        <div class="section-title">// Reasoning Chain</div>
        <div class="reasoning-card">
            {reasoning_html}
        </div>
    </div>

    <!-- 风险提示 -->
    {f'''<div class="risk-section">
        <div class="section-title" style="color: var(--red)">// Risk Warnings</div>
        <div class="risk-card">
            <ul>{risks_items}</ul>
        </div>
    </div>''' if risks else ''}

    <!-- 底部 -->
    <div class="footer">
        <p class="disclaimer">
            \u672C\u62A5\u544A\u7531 AI Renaissance \u591A\u667A\u80FD\u4F53\u6295\u8D44\u51B3\u7B56\u5F15\u64CE\u81EA\u52A8\u751F\u6210\uFF0C\u4EC5\u4F9B\u53C2\u8003\uFF0C\u4E0D\u6784\u6210\u4EFB\u4F55\u6295\u8D44\u5EFA\u8BAE\u3002<br>
            \u6295\u8D44\u6709\u98CE\u9669\uFF0C\u5165\u5E02\u9700\u8C28\u614E\u3002\u6570\u636E\u6765\u6E90\u4E8E\u516C\u5F00\u5E02\u573A\u4FE1\u606F\uFF0C\u53EF\u80FD\u5B58\u5728\u5EF6\u8FDF\u6216\u504F\u5DEE\u3002
        </p>
        <div class="timestamp">AI RENAISSANCE ENGINE v3.0 &middot; {now}</div>
    </div>

</div>

<script>
// 雷达图 — 含各维度分值标注
(function() {{
    var data = {radar_data};
    var chart = echarts.init(document.getElementById('radarChart'), 'dark');

    // 将分值标在各维度名称旁
    var indicators = data.names.map(function(n, i) {{
        return {{ name: n + ' ' + data.values[i], max: 100 }};
    }});

    chart.setOption({{
        backgroundColor: 'transparent',
        radar: {{
            indicator: indicators,
            center: ['50%', '55%'],
            radius: '65%',
            shape: 'polygon',
            splitNumber: 4,
            axisName: {{
                color: '#8892a4',
                fontSize: 11,
                fontFamily: 'JetBrains Mono, monospace',
                formatter: function(params) {{
                    return params;
                }}
            }},
            splitLine: {{ lineStyle: {{ color: 'rgba(0,229,255,0.08)' }} }},
            splitArea: {{ show: false }},
            axisLine: {{ lineStyle: {{ color: 'rgba(0,229,255,0.15)' }} }}
        }},
        series: [{{
            type: 'radar',
            data: [{{
                value: data.values,
                name: '\u7F6E\u4FE1\u5EA6',
                areaStyle: {{
                    color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                        {{ offset: 0, color: 'rgba(0,229,255,0.3)' }},
                        {{ offset: 1, color: 'rgba(0,229,255,0.02)' }}
                    ])
                }},
                lineStyle: {{ color: '#00e5ff', width: 2 }},
                itemStyle: {{ color: '#00e5ff' }},
                symbol: 'circle',
                symbolSize: 8,
                label: {{
                    show: true,
                    formatter: function(params) {{ return params.value; }},
                    color: '#00e5ff',
                    fontSize: 11,
                    fontFamily: 'JetBrains Mono, monospace',
                    fontWeight: 'bold',
                    distance: 5
                }}
            }}]
        }}]
    }});
    window.addEventListener('resize', function() {{ chart.resize(); }});
}})();
</script>

</body>
</html>"""
    return html
