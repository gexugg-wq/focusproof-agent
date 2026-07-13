# FocusProof Agent Documentation

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
