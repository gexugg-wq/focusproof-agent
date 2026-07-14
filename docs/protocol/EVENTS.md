# FocusProof Event and Agent Protocol

Version: v0.4
Primary implementation language: Python
Frontend mirror language: TypeScript

Accepted protocol baseline: AI4A.3.1. The protocol is frozen for AI4B.0 design;
any proof-recording event or API change requires explicit AI0 approval before
implementation.

本协议仍然是 FocusProof 的公共消息协议。v0.2 的核心变化是：运行时主实现从 TypeScript 转为 Python Agent Server，因此 Event、Action、Observation 应优先用 Python Pydantic model 实现，再按需要导出或手写 TypeScript 镜像类型给前端使用。

重要边界：

- Python Agent Server 是协议事实来源。
- Frontend 只消费 API 返回的数据，不拥有最终评分协议。
- OpenHands SDK 可以影响 Agent、Conversation 和 Tool 的实现方式，但不能改变本协议中的学习证据和评分语义。
- Web3 是领域插件，不是协议核心。
- 本文件中的 TypeScript 风格接口用于描述 FocusProof 产品/API 投影，不是要求实现第二套 Agent、Conversation、EventLog、Action、Observation 或 Tool Runtime。
- 运行时存在 OpenHands SDK 公共类型或生命周期 API 时必须直接复用；产品投影通过适配器从原生事件派生，不得替代原生运行事实。

## 1. 设计目标

本协议定义 FocusProof Runtime 的公共消息架构。所有实现 AI 必须遵守本文件；修改公共 Event、Action 或 Observation 前必须更新本文件。

核心流：

    Event -> EventLog -> View -> Agent.step() -> Action -> Tool -> Observation -> EventLog

## 2. Event 基础结构

    interface BaseEvent<TPayload = unknown> {
      id: string
      sessionId: string
      type: EventType
      sequence: number
      createdAt: string
      actor: "user" | "agent" | "tool" | "system"
      payload: TPayload
    }

约束：

- id 全局唯一。
- sequence 在同一会话内严格递增。
- Event 追加后不可静默修改。
- 派生结果必须保留来源 Event、Evidence 或 Observation ID。
- EventLog 必须支持按顺序重放。

## 3. Event 类型

    type EventType =
      | "session.created"
      | "goal.submitted"
      | "session.started"
      | "session.paused"
      | "session.ended"
      | "evidence.submitted"
      | "question.asked"
      | "answer.submitted"
      | "verification.requested"
      | "verification.completed"
      | "score.calculated"
      | "review.completed"
      | "proof.record.requested"
      | "proof.record.completed"
      | "error.occurred"

## 4. 关键事件定义

    type GoalSubmittedEvent = BaseEvent<{
      domain: string
      title: string
      goal: string
      expectedOutput?: string
      plannedMinutes?: number
    }> & { type: "goal.submitted" }

    type EvidenceSubmittedEvent = BaseEvent<{
      evidenceId: string
      evidenceType: EvidenceType
      contentHash: string
      textContent?: string
      fileUrl?: string
      sourceUrl?: string
      metadata?: Record<string, unknown>
    }> & { type: "evidence.submitted" }

    type QuestionAskedEvent = BaseEvent<{
      questionId: string
      question: string
      reason: string
      relatedEvidenceIds: string[]
    }> & { type: "question.asked" }

    type AnswerSubmittedEvent = BaseEvent<{
      questionId: string
      answer: string
    }> & { type: "answer.submitted" }

    type VerificationCompletedEvent = BaseEvent<{
      verificationId: string
      toolName: string
      status: "success" | "failed" | "inconclusive"
      facts: Record<string, unknown>
      evidenceRefs: string[]
      confidence?: number
      error?: string
    }> & { type: "verification.completed" }

## 5. Evidence 类型

    type EvidenceType =
      | "text"
      | "image"
      | "audio"
      | "video"
      | "code"
      | "url"
      | "transaction"
      | "contract"
      | "pdf"

## 6. 审查结果事件

    type ReviewStatus =
      | "VerifiedLearning"
      | "LikelyLearning"
      | "WeakEvidence"
      | "NeedsMoreVerification"
      | "InsufficientEvidence"
      | "ContradictoryEvidence"

    type ScoreCalculatedEvent = BaseEvent<{
      score: number
      confidence: number
      status: ReviewStatus
      dimensions: {
        goalClarity: number
        evidenceSpecificity: number
        goalAlignment: number
        understanding: number
        output: number
        reflection: number
      }
      findings: Finding[]
      evidenceRefs: string[]
    }> & { type: "score.calculated" }

    type ReviewCompletedEvent = BaseEvent<{
      reviewId: string
      summary: string
      nextStep: string
      scoreEventId: string
    }> & { type: "review.completed" }

## 7. EventLog 接口

    interface EventLog {
      append(event: Event): Promise<void>
      appendMany(events: Event[]): Promise<void>
      list(sessionId: string): Promise<Event[]>
      getByType(sessionId: string, type: EventType): Promise<Event[]>
      latest(sessionId: string): Promise<Event | undefined>
      count(sessionId: string): Promise<number>
    }

实现要求：

- 并发追加必须保证 sequence 不重复。
- 查询默认按 sequence 升序。
- EventLog 不能把 View 当作事实保存。
- 数据库错误必须向上返回，不得伪造成功事件。

## 8. AgentView

    interface AgentView {
      session: {
        id: string
        status: string
        startedAt?: string
        endedAt?: string
        elapsedSeconds?: number
      }
      goal: {
        domain: string
        title: string
        goal: string
        expectedOutput?: string
        plannedMinutes?: number
      }
      evidence: EvidenceSummary[]
      verificationResults: VerificationSummary[]
      findings: Finding[]
      unansweredQuestions: Question[]
      availableTools: ToolDescription[]
      previousActions: ActionSummary[]
    }

ViewBuilder 必须能说明摘要来自哪些 Event。

## 9. Action

    type Action =
      | {
          type: "ask_question"
          question: string
          reason: string
          relatedEvidenceIds: string[]
        }
      | {
          type: "request_evidence"
          evidenceType: EvidenceType
          reason: string
        }
      | {
          type: "verify_evidence"
          toolName: string
          input: Record<string, unknown>
          evidenceIds: string[]
        }
      | { type: "calculate_score" }
      | { type: "generate_summary" }
      | { type: "finish_review" }

Agent 只能返回 Action，不能执行 Action。

## 10. Observation 和 Tool

以下结构描述 FocusProof 对外投影。Python 运行时必须使用 OpenHands SDK 原生 Action、Observation、`ToolDefinition` 与 `ToolExecutor`；不得按本示例另建可执行工具协议。

    interface Observation {
      toolName: string
      status: "success" | "failed" | "inconclusive"
      facts: Record<string, unknown>
      sourceRefs: string[]
      error?: string
    }

    interface ToolDefinition {
      name: string
      description: string
      inputSchema: Record<string, unknown>
    }

    interface ToolExecutor {
      execute(action: Extract<Action, { type: "verify_evidence" }>): Promise<Observation>
    }

Observation 的 facts 是工具观察到的事实，不代表用户一定理解了这些事实。Observation 必须被 Runtime 写入 EventLog。

## 11. Agent 和 Conversation

以下接口只说明 FocusProof API 需要表达的状态，不是本地 Runtime 实现规范。实际运行必须由 OpenHands SDK `Agent`、`LocalConversation`、`ConversationState`、原生 EventLog/View 与公开生命周期方法承担。FocusProof 数据库只保存产品事实和幂等审计投影。

    interface Agent {
      step(view: AgentView): Promise<Action>
    }

    interface ConversationState {
      conversationId: string
      sessionId: string
      status: "idle" | "running" | "waiting" | "completed" | "failed"
      stepCount: number
      eventLog: EventLog
      currentView?: AgentView
      lastError?: string
    }

    interface Conversation {
      id: string
      start(): Promise<void>
      run(): Promise<void>
      stop(): Promise<void>
      appendEvent(event: Event): Promise<void>
      getState(): ConversationState
    }

## 12. 标准运行流程

    GoalSubmittedEvent
      -> SessionStartedEvent
      -> EvidenceSubmittedEvent
      -> ViewBuilder
      -> Agent.step()
      -> VerifyEvidenceAction
      -> VerificationRequestedEvent
      -> ToolExecutor
      -> VerificationCompletedEvent
      -> ViewBuilder
      -> Agent.step()
      -> AskQuestionAction
      -> QuestionAskedEvent
      -> AnswerSubmittedEvent
      -> Agent.step()
      -> CalculateScoreAction
      -> ScoreCalculatedEvent
      -> ReviewCompletedEvent

## 13. 运行限制和错误恢复

默认限制：

- 单次 OpenHands Conversation run 最大 Agent iteration：6。
- 最大追问次数：3。
- 最大工具调用次数：8。
- 单次工具超时：15 秒。
- 单次 Review 超时：120 秒。

工具失败时：ErrorEvent -> failed Observation -> 重试、换工具或请求补充证据。

LLM 输出无法解析时：ErrorEvent -> request_evidence 或 ask_question。解析失败时禁止直接高分或上链。

会话中断时，从最后一个 Event 重建 ConversationState 和 AgentView。

## 14. 默认评分维度

- 目标明确性：15。
- 证据具体性：20。
- 目标匹配度：20。
- 理解验证：25。
- 学习产出：10。
- 反思与下一步：10。

时间只能作为辅助信号，不得单独产生有效学习结论。
