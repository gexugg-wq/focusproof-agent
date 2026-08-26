# AI5.6 consumedFactIds Contract A 修复报告

## Repair baseline（严格复验修复开始前）

- branch: `agent/monad-evidence-plugin`
- HEAD: `9a79998f6f62853d8dc000969ceb8a6f43040ba6`
- `git status --short` snapshot SHA256: `88fd4ec91d2decf96f65ccd961f7b957d4200a5d6737122da09b5fa39948ea34`。完整输出在本轮执行日志中；本轮不覆盖、撤销或删除其中任何既有 dirty 修改/未知文件。
- `result_extractor.py`: `dac222115ac3a927b45dbb52d08cd41ecc347f4c4cb690b9b7010f1a2197158a`
- `run_real_image_evidence_gate.py`: `977ff38d56c2608a4a7675881c83db4c666bd87bfa7953a6a56de2760113c3f6`
- `test_media_scoring.py`: `80a56259b037707571fff98af692e70309ce7f22283ea073107df9052e7a9889`
- `test_real_image_gate_contract.py`: `c5e026dcf794e191f63d5b75e06c01d941a8a415dec3661b0df76882c39c5b41`
- 本报告: `51f42aed82656ba33e63bc0d78fdb87e5a709c0b653d09f7b4713f6cecd78ad0`
- V3 fixture: `a39f41c33c24cf472f2dbc4a6a3cb8973f914d25ebb3f7e80a62fd4d9fdc4222`
- fixture hash file: `433c859b701c65bfa48ff5fadf6bb1c1f9d813986698185eadf4de51ab54d484`
- supplemental zero-set persistence test baseline, `test_conversation_lifecycle.py`: `371ca747c14d154bf507780bde8f879b69130234fb1df9b2be0fe71b68bc5b71`

Prior provenance limitation：Contract A 初始修改发生在本 repair baseline 之前，且权威树原本已 dirty，无法从当前状态逆推出其更早逐文件基线。本节只证明严格复验修复轮次的增量边界，不对更早来源作虚假声明。

## Strict review repair

本轮根据严格复验拒绝项完成以下最小修复：

- V3 不再读取 `context["lineage"]`；只有 `narrativeLineage` 可以进入 explicit/complete 输出。legacy-only 输入直接拒绝，历史 2.0 仍只能标识为 legacy/inferred。
- runner 使用统一 strict ID validator。fact、projection、source observation、score event、review、review projection ID 必须是实际 `str`、非空且无首尾空白；不再用 `str(value)` 修复非法类型。
- extractor 在单 projection 与跨 narrative 范围拒绝重复 fact ID，并全局拒绝重复 projection ID。
- `score.calculated` 始终包含 `narrativeLineage` 和 `consumedFactIds`；零 facts 明确写入 `[]`。
- writer 与 checked-in fixture 共用 `_validate_v3_summary_payload`，校验三集合、规范排序、count、事件引用和所有 ID。fixture 现包含被 visualFacts 引用的 observation event，不再是 `events=[]`。

新增独立测试：

- `test_legacy_only_lineage_cannot_be_written_as_v3_explicit`
- `test_v3_lineage_rejects_non_strict_ids`
- `test_v3_lineage_rejects_invalid_scoring_consumed_sets`
- `test_v3_summary_validator_rejects_count_mismatch`
- `test_checked_in_v3_fixture_passes_shared_validator_and_hash`
- `test_audited_narrative_lineage_rejects_cross_narrative_duplicates`
- `test_audited_narrative_lineage_rejects_non_strict_ids`
- `test_completed_review_score_is_owned_by_focusproof` 增加零集合持久化断言。

RED 证据：严格精确组首次运行出现 13 failures，分别命中 legacy fallback、零数组省略、跨 narrative 重复、空白 ID、缺少共享 validator 与无效 fixture 引用。GREEN 证据：精确 repair 组 54 passed；相关三个完整测试文件 168 passed；最终非 real_llm 相关全套 414 passed、2 skipped、1 deselected。

Repair 后 SHA256：

- `result_extractor.py`: `bdd34101daacbb0fc4210bcf0d6947ad5d17f4e0aefe6a050c11f289ce03f944`
- `run_real_image_evidence_gate.py`: `cd60d2dba7b01195ec91d7085dbb3f88070331166afd7413cdeb8ba7e6431488`
- `test_media_scoring.py`: `bcc691dd34f13a89604ae1e492a8340de0fde843d6b12b7604fe71415872184f`
- `test_real_image_gate_contract.py`: `07415b83df3f0d0a0fc3668bba536f37c2994d38baea469035e0f1f12000b0c6`
- `test_conversation_lifecycle.py`: `66697f5ddc7684727e77647a09e5af73a871dc5b1fd292933e72f2afe26f1145`
- V3 fixture: `2cbf8488273ac58397a48778f52f2bd00b7a9793ef4f50a2303320e3c97b06f6`
- fixture hash file: `2657e476fd50ddfa9adc5ab42143c9cc694b99302aad22054af2dd8e9c3f1792`

本 repair 轮次的允许增量文件仅为上述 5 个代码/测试文件、报告、fixture 与其 hash。branch 与 HEAD 保持 baseline 值，staged 为空。

## 结论

本变更实现 Contract A：FocusProof 在 OpenHands SDK 1.31.0 的官方 EventLog 与事件 ID 之上增加产品侧显式消费审计，不修改或仿制 SDK。评分算法未改变；fact ID 仅作为审计标识，不进入学习文本。

## 实现边界

- `result_extractor.py` 从实际参与评分的 `VerifiedLearningNarrative.consumed_fact_ids` 生成 `narrativeLineage[].consumedFactIds`，并在 `score.calculated` 写入聚合 `consumedFactIds`。
- 写入前 fail-closed：非空 ID、projection 内去重、digest/ID 数量一致、projection facts 与显式集合一致。
- `run_real_image_evidence_gate.py` 要求 narrative、scoring 与 visualFacts 三个集合完全一致，输出规范排序数组，schema 为 `3.0`，并标记 `lineage.mode=explicit`。
- 保留 `projectionEventIds` 名称，未做破坏性改名。
- explanation-only 零 facts 返回空 lineage 与空聚合集合；真实图片 Gate 的至少 3 facts 规则未放宽。
- 历史 `2.0` 文件保持不变。它的集合来自 visualFacts 推断，只能视为 legacy/inferred，不能声称 explicit complete lineage。
- Monad 插件、OpenHands SDK、评分算法、生产病毒扫描均不在本修复范围。

## TDD 记录

RED：`test_pass_path_writes_requested_auditable_eventlog_summary` 明确要求 schema `3.0` 与两处 `consumedFactIds`，首次运行失败：实际 `2.0`。

GREEN：

- AI5 Gate contract：104 passed。
- 精确 domain/runtime/integration：32 passed，1 skipped。
- AI5 + domain + integration + openhands_runtime（排除 real_llm）：414 passed，2 skipped，1 deselected。
- Ruff format/check：4 个本任务文件格式稳定，check 通过。
- Mypy：2 个生产文件通过。
- `git diff --check`：通过。

覆盖包括 8-ID 稳定 fixture、三集合一致、顺序规范化、零 facts、duplicate、missing/extra/different、count/projection mismatch、以及 raw fact/OCR/PII/路径/URL/base64/data URL/object key/API key/secret 禁止项。

## 真实证据与调用次数

只读确认历史成功报告、SHA256 与 eventlog summary 存在。历史 summary 是 schema `2.0`，含 8 个 visual fact ID，但没有显式 narrative/scoring consumed 集合。仓库内未找到对应 score audit 的持久化副本，因此禁止从旧 summary 推断后冒充显式 lineage。

证据结论是 composite：

- 历史真实 Gate 证明 qwen3.7-plus 看图成功；真实 provider 调用总次数仍为 1。
- 本任务 provider 调用次数为 0；未重跑 qwen3.7-plus。
- 新增 fixture-contract V3 证明显式集合校验与确定性投影。
- 未生成、也不声称全新真实端到端 V3 产物或真实 offline reprojection。

## 非完成声明

本报告不将 AI5.7、生产病毒扫描或整体 AI5 标记为完成。
