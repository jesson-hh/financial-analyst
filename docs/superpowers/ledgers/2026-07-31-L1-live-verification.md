# L1 · Task 6 — 真机验证账(2026-07-31)

> 计划:`docs/superpowers/plans/2026-07-31-orchestration-L1-subject-projection.md` · 执行:控制器本人,进程内生产装配(战役验证过的方法;9999 未动——「一列车」裁决:9999 只在 L2-b 落地后部署)。

## 这次真跑证明了什么

**出口判据逐字达成。** 新身份 601318(临时策略 opt-in,有界租约 `max_admissions=1`),run `deep-f52ddb827d3c72e6`,4 次 deepseek 结算:

- sentiment / bull-r1 / research-mgr **completed**(真 LLM 产物);
- **`dec.pm` 在桥执行层失败**,耐久 `NodeRun` 载荷里的拒因**原文**:

  > `worker 'dec.pm': 1 reviewed data prefetch row(s) (methods: 'verified_snapshot') have params resolved from the run subject projection (code '601318', as_of '2026-07-30T16:00:00+00:00'), but no production DataRuntimeWorld is bound (the chartered L2-b gap) -- refusing rather than faking a data read`

  冻结标记 ✓、**本 run 自己的标的与会话时点** ✓、L2-b 点名 ✓ —— **物化时从已提交 RunSubject 盖章的投影,可证明地把 THE subject 送进了数据桥**(D-0 方案 (i) 在生产上成立);
- pm **零 LLM、零能力计费**(拒绝先于模型调用);trader 诚实 blocked;快线判断照常返回(观望/75)。

**同身份重跑:0 次 LLM 结算**,3.4 秒诚实失败重放——无双花(拒因为已挂账的上游工件信封持久化缺口,非新缺陷)。

## 观察到的与预测不同处(按计划纪律:观察为准,差异是发现不是尴尬)

1. **bear-r1 本次 incomplete**(`BearCase` 解码为 None——模型侧偶发;前两次战役真跑均通过;诚实 incomplete 是正确行为)。已记档,不在本任务修。
2. 计划 3.1 预测四席全 completed;实测 3 completed + bear incomplete,结算数仍为 4(bear 的那次花在无效答案上)。
3. **失败臂不落台账行是设计使然**:失败的深跑把 `deep_outcome` 随快结果返回、由 watcher tick 持久化;只有 **completed** 深跑自写编排行(000858 先例)。

## 没有证明什么

- **没有任何数据被读到**——L2-b(生产数据世界)仍是缺口,pm 的拒绝正确地指向它;
- **十节点完整 preset 仍被支持检查拒绝**——L3(补授权+重冻)未动。

## 出口闸清点

真密封行经投影解析+校验(测试与真机双证)✓ · `spec.py`/校验块零改动 ✓ · 无新 `source_kind`、`SubjectParams` 未注册 ✓ · 九个密封摘要逐位与 §5 真值相等 ✓ · 旧提供者全仓删净、三形状全大声、m1 变异证明在案 ✓ · `data_runtime_provider_factory` 零生产调用者(L2-b 翻)✓ · 翻面清单齐全含双向证据 ✓ · 未绑时工厂对象恒等、`GUANLAN_SEATS_DEEP` 未设 watcher 逐位不变 ✓ · 全套件 5715+1x 对账吻合 ✓ · 真机新拒绝形状带本 run 自己的 subject 值 ✓
