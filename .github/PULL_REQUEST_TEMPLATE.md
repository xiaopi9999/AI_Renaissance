## Agent 名称
（填写你的 Agent 名称）

## 负责人
@（你的 GitHub 用户名）

## 所属层级
- [ ] 感知层（Perception）
- [ ] 研究层（Research）
- [ ] 风控层（Risk）
- [ ] 认知层（Cognition）

## 做了什么
- 

## 信号示例
（粘贴一个 `analyze()` 返回的典型 Signal 对象）

## 如何测试
```python
# 测试代码
```

## Checklist
- [ ] 代码可以运行，无语法错误
- [ ] `analyze()` 返回标准 `Signal` 对象
- [ ] 如涉及真实数据源，已说明离线验证方式（例如通过 `config` 注入 fake source）
- [ ] 有异常处理（`try/except`），出错时返回 `neutral_signal`
- [ ] 已更新 `docs/AGENT_MATRIX.md`（如果是新 Agent）
- [ ] 没有提交多余文件（日志、缓存等）
