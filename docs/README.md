# FocusProof Agent Documentation

当前基线：AI4B 已在提交 `bf5c9a8` 通过 AI0 验收，文本/URL 通用学习验证 MVP、OpenHands 原生运行时、持久化、前端、质量与安全基线均已完成。下一阶段是 AI4C.0 Production Readiness 设计门禁，先定义正式身份、真实 LLM 运行和可复现部署边界；多模态输入属于后续 AI5，不在 AI4C 范围内。

本目录按职责分类管理：

- `architecture/`：系统架构和模块边界。
- `protocol/`：EventLog、View、Action、Observation 和 Agent Runtime 协议。
- `project-management/`：AI 分工、权限、依赖和验收标准。

维护规则：

1. 架构变化先更新 `architecture/ARCHITECTURE.md`。
2. 公共事件或接口变化先更新 `protocol/EVENTS.md`。
3. AI 任务状态更新 `project-management/TASK_BOARD.md`。
4. 实现代码不放入 `docs/`，代码按 runtime、domain、tools、database、frontend、contracts 分类。
5. 文档统一使用 UTF-8 编码。
6. 所有运行时设计先执行 `architecture/OPENHANDS_REUSE_STRATEGY.md` 的 OpenHands 直接复用门禁：SDK 已有公共能力时直接使用，禁止建立同义仿制层；只有正式记录 SDK gap 后才允许最小化补充。
