# 现金流量表附表间接法——JSONL计算留痕协议

## 一、协议目标

JSONL留痕用于回答四个问题：

1. 本次计算使用了什么输入和规则；
2. 每个金额片段为什么纳入、排除或形成配套抵销；
3. AI和人工分别作了什么判断；
4. 中途失败、续跑或更正后，最终结果如何形成。

JSONL是不可变证据流，不是大表计算引擎。大规模结构化计算结果仍保存为Parquet。

## 二、文件与写入

每个运行批次至少生成：

- `events.jsonl`：运行、规则、AI、金额占用、核对和输出事件；
- `issues.jsonl`：数据异常、会计异常和人工升级事项；
- `event_index.parquet`：按事件标识、金额片段、凭证和调节项目建立的查询索引；
- `decision_trace.parquet`：判断轨迹，一行表示“一个金额片段对一个候选项目的一次最终判断”。

`decision_trace.parquet`把规则理由、AI建议与正反证据、未采用方案、人工升级状态和最终金额占用合并为稳定判断轨迹，供18张表审计底稿逐行展示。稳定主键为`trace_id`，稳定关联字段为`fragment_id`、`business_event_id`、`item_no`和`source_row`。判断轨迹在金额占用完成后写入`06_decisions`目录，并复制到正式输出目录纳入输出清单哈希；工作簿生成器只读取该轨迹，不在中央金额占用层重复保存会计判断说明。

判断轨迹至少包含以下字段：`trace_id`、`fragment_id`、`business_event_id`、`source_row`、`item_no`、`candidate_status`、`final_decision`、`rule_id`、`decision_reason`、`confidence`、`recommendation`、`recommendation_reason`、`supporting_evidence`、`contrary_evidence`、`rejected_alternatives`、`human_review_required`、`claim_amount_minor`、`physical_consumed_minor`、`remaining_amount_minor`、`occupancy_type`、`paired_offset_id`、`recommended_status`、`recommended_amount_minor`、`cfo_impact_minor`、`model_id`、`prompt_template_version`、`prompt_hash`、`response_hash`。

规则：

1. 一行一个完整JSON对象；
2. UTF-8编码；
3. 只允许追加，不允许修改和删除历史行；
4. 由单一写入者分配连续序号；
5. 每写入一批记录后刷新文件并记录批次哈希；
6. 并行模块只能提交事件草稿，由中央写入者按固定顺序落盘。

## 三、通用事件外壳

当前实现每条事件包含：

| 字段 | 含义 |
|---|---|
| `sequence_no` | 运行批次内从1连续递增序号 |
| `previous_event_hash` | 上一条事件的SHA-256（首条为64个0），形成防篡改链 |
| `run_id` | 运行批次标识 |
| `stage` | 当前处理阶段 |
| `event_type` | 事件类型 |
| `payload` | 当前事件的业务内容 |
| `event_hash` | 本条事件（除自身字段外）规范JSON的SHA-256 |

## 四、事件类型

当前实现实际写入`run_started`、`normalization_completed`、`ai_requests_frozen`、`ai_decisions_validated`、`occupancy_committed`、`reconciliation_completed`、`run_completed`七类运行级事件；逐金额片段的规则、AI、证据、备选方案和占用结果统一保存在`decision_trace.parquet`，避免在事件流重复保存同一判断。下列全集为协议预留，随实现扩展逐步启用。

### 4.1 运行与检查点

- `run_started`
- `run_resumed`
- `stage_started`
- `stage_completed`
- `stage_failed`
- `run_completed`
- `run_failed`

`stage_completed`必须记录输入哈希、输出哈希、记录数、金额校验值和耗时。

### 4.2 输入与标准化

- `input_file_registered`
- `input_snapshot_created`
- `header_mapping_decided`
- `schema_validation_completed`
- `normalization_completed`
- `input_issue_detected`
- `ledger_balance_check_completed`

正常行不逐行记录标准化事件，只记录批次汇总和异常行，避免JSONL膨胀。

### 4.3 候选与业务事件

- `technical_closing_decided`
- `business_event_linked`
- `amount_fragment_created`
- `candidate_selected`
- `candidate_excluded`

每个进入项目判断的金额片段必须至少有一个候选或排除事件。

### 4.4 规则、AI和人工

- `rule_decision_made`
- `ai_decision_requested`
- `ai_decision_made`
- `human_review_escalated`
- `human_review_completed`
- `decision_corrected`

### 4.5 金额占用与计算

- `claim_proposed`
- `claim_conflict_detected`
- `paired_offset_confirmed`
- `occupancy_committed`
- `occupancy_rejected`
- `item_calculated`

### 4.6 核对与输出

- `working_capital_bridge_completed`
- `deferred_tax_bridge_completed`
- `item_reconciliation_completed`
- `final_reconciliation_completed`
- `output_file_generated`
- `output_manifest_completed`

## 五、规则判断事件

`rule_decision_made`的`payload`至少包含：

```json
{
  "fragment_id": "frag_...",
  "business_event_id": "evt_...",
  "item_no": 14,
  "rule_id": "AR-WRITEOFF-001",
  "decision": "excluded",
  "amount_minor": 1000000,
  "reason": "坏账损失已经由第2项调整，相应应收原值减少从第14项剔除",
  "supporting_evidence": [
    "本行应收账款贷方减少",
    "同一业务事件存在信用减值损失借方"
  ],
  "contrary_evidence": [],
  "confidence": 1.0
}
```

规则编号必须稳定。仅修改文字说明不能改变规则编号；业务含义改变时必须升级规则版本。

## 六、AI判断事件

`ai_decision_made`必须包含：

```json
{
  "fragment_id": "frag_...",
  "business_event_id": "evt_...",
  "candidate_items": [6, 10, 16],
  "recommended_item": 6,
  "recommended_status": "included",
  "recommended_amount_minor": -2500000,
  "cfo_impact_minor": -2500000,
  "recommendation": "作为非金融长期资产处置收益计入第6项",
  "reason": "处置标的是固定资产，损益进入资产处置损益，不属于金融资产或股权投资",
  "supporting_evidence": [
    "固定资产原值和累计折旧同时转销",
    "对方科目为资产处置损益"
  ],
  "contrary_evidence": [
    "摘要包含投资字样，但没有金融资产科目"
  ],
  "rejected_alternatives": [
    {
      "item_no": 10,
      "reason": "没有长期股权投资或金融资产"
    }
  ],
  "confidence": 0.91,
  "performance_materiality_minor": 5000000,
  "strong_conflict": false,
  "human_review_required": false,
  "model_id": "实际调用模型标识",
  "prompt_template_version": "cashflow-ai-v1",
  "prompt_hash": "SHA-256",
  "response_hash": "SHA-256"
}
```

要求：

1. 必须有唯一首选项目和金额；
2. 必须写建议理由、支持证据和相反证据；
3. 必须说明为什么不采用主要备选分类；
4. 必须记录重要性水平和强冲突判断；
5. 不保存API密钥或其他秘密；
6. 原始提示词可以另存安全文件，JSONL只保存模板版本和哈希；
7. AI失败时重试并留痕，仍失败则使用确定性兜底，不留下未分类金额。
8. `cfo_impact_minor`必须使用最小货币单位整数；布尔值不属于合法金额。

## 七、人工升级事件

只有同时满足以下条件才生成`human_review_escalated`：

1. 金额达到实际执行重要性水平；
2. 存在能够改变项目归属或金额的强冲突证据。

事件必须包含：

- AI首选处理；
- AI建议理由；
- 备选处理；
- 两种处理对16项和经营活动现金流量的金额影响；
- 具体冲突证据；
- 建议人工只回答的明确问题。

人工完成后生成`human_review_completed`，不得改写原AI事件。

## 八、金额占用事件

`occupancy_committed`至少包含：

```json
{
  "fragment_id": "frag_...",
  "original_amount_minor": 1000000,
  "claimed_amount_minor": 1000000,
  "remaining_amount_minor": 0,
  "item_no": 2,
  "occupancy_type": "exclusive",
  "decision_event_id": "event_...",
  "paired_offset_id": null
}
```

对于允许的配套抵销，必须生成共同的`paired_offset_id`，并分别引用两项金额占用，不能用“重复占用”代替配套抵销。

中央占用层完成后必须记录：

- 原始金额总计；
- 已占用金额总计；
- 剩余金额总计；
- 互斥冲突数量；
- 配套抵销数量。

## 九、更正和版本

1. 历史事件不修改；
2. 新判断通过`supersedes_event_id`引用原事件；
3. 更正原因必须区分输入更正、规则变化、AI判断变化和人工复核；
4. 更正导致金额变化时，重新生成受影响阶段的Parquet快照；
5. `event_index.parquet`为当前运行级事件建立查询索引；存在更正事件时，旧事件仍保留并按更正关系标记有效状态；
6. 输入文件或规则发生实质变化时，原则上新建运行批次，不在原批次中掩盖变化。

## 十、隐私和最小记录

1. 留痕仅保存完成判断所需的信息；
2. 不保存账号密码、API密钥和访问令牌；
3. 对大段合同或摘要只保存必要片段和原始位置；
4. 原始Excel保存在本地运行目录，不上传到无关服务；
5. 输出给用户的Excel可以展示摘要，但机器留痕必须保留稳定的来源引用。

## 十一、判断轨迹完整性检查

`validate_decision_trace`必须通过后才能进入输出阶段，覆盖以下错误：

1. 引用了未知金额片段；
2. 同一轨迹主键（`trace_id`）重复；
3. 已纳入金额无法关联最终占用；
4. 候选项目缺少最终纳入或改归说明；
5. AI证据编号无法解析。

发现以上任一错误时，输出阶段必须停止并返回具体中文错误，不得静默丢证据。

## 十二、留痕完整性检查

运行完成前必须验证：

1. JSONL每行都是合法JSON；
2. `sequence_no`从1连续递增（由存储层在追加时强制校验）；
3. `event_hash`与`previous_event_hash`逐条链式衔接；
4. 每个金额片段都有最终处理事件；
5. 每个占用都能通过`decision_trace.parquet`追溯到规则、AI或人工判断；
6. 每个项目汇总都能追溯到金额占用明细；
7. 每个更正事件引用的原事件存在；
8. 所有阶段检查点的文件哈希与实际文件一致；
9. JSONL文件最终SHA-256已经写入输出清单；
10. 不存在没有AI建议的人工升级事项。
