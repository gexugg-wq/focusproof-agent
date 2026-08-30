# FocusProof Event and Agent Protocol

Version: v0.5
Primary implementation language: Python
Frontend API projection language: TypeScript

Accepted protocol baseline: AI4A.3.1. The protocol is frozen for AI4B.0 design;
any proof-recording event or API change requires explicit AI0 approval before
implementation.

本协议仍然是 FocusProof 的公共消息协议。v0.2 的核心变化是：运行时主实现从 TypeScript 转为 Python Agent Server，因此 Event、Action、Observation 应优先用 Python Pydantic model 实现，再按需要导出或手写 TypeScript 镜像类型给前端使用。

重要边界：

- OpenHands SDK 原生 EventLog 是运行时事件、顺序、重放与恢复的事实来源。
- 产品数据库只拥有 session/evidence/review/build-log 与只读审计投影；不得用于恢复或驱动 Agent runtime。
- Frontend 只消费 API 返回的数据，不拥有最终评分协议。
- OpenHands SDK 可以影响 Agent、Conversation 和 Tool 的实现方式，但不能改变本协议中的学习证据和评分语义。
- Web3 不属于当前通用 runtime；任何未来 optional plugin 必须显式启用并与本协议投影隔离。
- 本文件中的 TypeScript 风格接口用于描述 FocusProof 产品/API 投影，不是要求实现第二套 Agent、Conversation、EventLog、Action、Observation 或 Tool Runtime。
- 运行时存在 OpenHands SDK 公共类型或生命周期 API 时必须直接复用；产品投影通过适配器从原生事件派生，不得替代原生运行事实。
- 语音转写是统一文本 Evidence composer 的可选输入边界，不是新的 Event、Evidence 或评分协议。

## 1. 设计目标

本协议定义 FocusProof Runtime 的公共消息架构。所有实现 AI 必须遵守本文件；修改公共 Event、Action 或 Observation 前必须更新本文件。

以下是产品/API 投影流，不是第二套 runtime loop：

    OpenHands native EventLog -> FocusProof product projection -> API/frontend

## 1.1 Optional speech transcription API (AI6 V1)

The optional endpoint is:

    POST /sessions/{session_id}/transcriptions
    Headers: Authorization, Idempotency-Key: UUID
    Multipart: exactly one `file`; optional `languageHint=auto|zh|en`

The request is admitted and authenticated before multipart bytes are consumed.
The response is a live, bounded projection and has no EventLog, Evidence,
OpenHands, scoring, review, or automatic-submit side effect:

    {
      "requestId": "...",
      "transcript": "provider text, unchanged",
      "provider": "dashscope",
      "model": "qwen3-asr-flash"
    }

`transcript` is a candidate for the existing controlled text composer. The
frontend may display and let the learner edit it; only the existing Submit
Evidence action can create a `text` Evidence record. The candidate is not an
`audio` Evidence record. Raw audio and candidate text are ephemeral and must not
be written to product tables, native EventLog, OpenHands messages, scores,
reviews, logs, reports, object storage, or Git. `languageHint` is accepted
metadata in schema version 1 and has no semantic effect in the pinned adapter.

Transcription-processing outcome codes are `audio_too_large`, `audio_too_long`,
`unsupported_audio_format`, `invalid_audio`, `transcription_no_speech`,
`transcription_timeout`, `transcription_rate_limited`,
`transcription_provider_unavailable`, `transcription_ambiguous`,
`transcription_result_unavailable`, and `transcription_failed`. These are not
the entire public error-code space. Admission, route, and idempotency handling
also exposes `idempotency_conflict`, `transcription_in_progress`,
`speech_disabled`, `invalid_idempotency_key`, `speech_session_unavailable`,
`one_audio_file_required`, and `invalid_language_hint`, plus shared API codes
such as `invalid_token`, `forbidden`, `identity_unavailable`,
`database_unavailable`, and `request_too_large`. The frontend BFF may emit its
own bounded proxy failures: `forbidden_proxy_path`, `backend_unavailable`,
`upstream_response_too_large`, and `upstream_non_json`. The implementation's
typed mappings and route/admission branches are authoritative. No provider body,
URL, key, transcript, or stack trace is returned in an error.

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
      | "transaction" // historical reserved value; no default verifier
      | "contract" // historical reserved value; no default verifier
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

## 7. Historical Product Projection Interface (Superseded as Runtime EventLog)

下列接口只保留为早期产品查询投影的历史记录。当前实现不得把它命名或实现为第二套 runtime EventLog；运行时事实、顺序、重放和恢复全部由 OpenHands native EventLog 负责。

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
