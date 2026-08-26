# FocusProof Agent Documentation

当前权威基线（2026-08-26）：AI4C 工程与 AI5 图片基础/运行验收已完成；
AI5.8 独立审计经三轮修复后由 Round 3 独立复验接受。图片与 Monad 能力均
默认关闭、可拆卸；真实视觉 Provider 默认关闭，公开生产部署、托管 OIDC、
外部长期运维/SLO 尚未授权；音频/PDF/OCR/ASR 尚未实现。AI6 multimodal
expansion requires separate AI0 approval。
当前架构与 P1 修复边界见 `architecture/ARCHITECTURE.md` 和
`research/GENERAL_CORE_P1_REPAIR_REPORT.md`；旧 fake/local runtime 报告仅作历史记录。

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
