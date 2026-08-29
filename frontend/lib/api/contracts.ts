export type LearningGoal = {
  domain: string;
  title: string;
  goal: string;
  expectedOutput?: string | null;
  plannedMinutes?: number | null;
};

export type Evidence = {
  evidenceId: string;
  evidenceType: string;
  contentHash: string;
  textContent?: string | null;
  sourceUrl?: string | null;
  metadata: Record<string, unknown>;
};

export type Finding = {
  severity: "info" | "warning" | "error";
  message: string;
  evidenceIds: string[];
  observationRefs: string[];
};

export type ReviewResult = {
  status: string;
  score: number;
  confidence: number;
  dimensions: Record<string, number>;
  findings: Finding[];
  summary: string;
  nextStep: string;
};

export type RuntimeReviewResult = {
  sessionId: string;
  conversationMode: string;
  usedOpenHandsConversation: boolean;
  conversationId?: string | null;
  nativeEventCount?: number;
  messageEventsCount?: number;
  actionEventsCount?: number;
  observationEventsCount?: number;
  projectedEventsCount?: number;
  reviewStatus: "completed" | "awaiting_user" | "failed";
  agentQuestions?: Array<{ questionId: string; question: string }>;
  reviewResult?: ReviewResult | null;
  error?: string | null;
  status?: string;
  latestEventType?: string | null;
  eventsCount?: number;
};

export type PluginCapability = {
  pluginId: string;
  capabilityId: string;
  enabled: boolean;
  metadata: Record<string, unknown>;
};

export type ImageEvidenceCapability = {
  capabilityId: "image_evidence";
  enabled: true;
  formats: string[];
  maxCount: number;
  maxOriginalBytes: number;
  maxNormalizedBytesPerSession: number;
  explanationRequired: boolean;
};

export type SpeechTranscriptionCapabilityEnabled = {
  capabilityId: "speech_transcription";
  schemaVersion: 1;
  enabled: true;
  formats: Array<"audio/webm;codecs=opus" | "audio/wav" | "audio/mpeg">;
  maxAudioBytes: number;
  maxDurationSeconds: number;
  languageHintsAccepted: Array<"auto" | "zh" | "en">;
  languageHintEffect: "metadata_only";
};

export type SpeechTranscriptionCapabilityDisabled = {
  capabilityId: "speech_transcription";
  schemaVersion: 1;
  enabled: false;
  reasonCode:
    | "asr_not_configured"
    | "asr_configuration_invalid"
    | "asr_prerequisites_unavailable";
};

export type SpeechTranscriptionCapability =
  | SpeechTranscriptionCapabilityEnabled
  | SpeechTranscriptionCapabilityDisabled;

export type ProductCapability =
  | ImageEvidenceCapability
  | SpeechTranscriptionCapability;

export type ImageEvidenceResponse = {
  evidenceId: string;
  mediaType: string;
  normalizedBytes: number;
  replayed: boolean;
};

export type SessionView = {
  pluginCapabilities?: PluginCapability[];
  productCapabilities?: ProductCapability[];
  [key: string]: unknown;
};

export type SessionDetail = {
  sessionId: string;
  state: {
    sessionId: string;
    ownerUserId: string;
    status: string;
    goal: LearningGoal;
    evidence: Evidence[];
    answers: Record<string, string>;
    observations: unknown[];
    previousActions: unknown[];
    reviewResult: ReviewResult | null;
    adapterMode: string;
    conversationId: string;
    runtimeMode: string;
  };
  view: SessionView;
};

export type FocusProofEvent = {
  id: string;
  sessionId: string;
  type: string;
  sequence: number;
  createdAt: string;
  actor: string;
  payload: Record<string, unknown>;
};

export type ReviewProjection = {
  reviewId: string;
  sessionId: string;
  conversationId: string;
  reviewStatus: string;
  nativeEventCount: number;
  sourceOpenHandsEventId: string | null;
};

export type CreateSessionInput = {
  domain: string;
  title: string;
  goal: string;
  expectedOutput?: string | null;
  plannedMinutes?: number | null;
};

export type SubmitEvidenceRequest = {
  evidenceType: string;
  textContent?: string;
  sourceUrl?: string;
  metadata: Record<string, unknown>;
};

export type SyncResponse = {
  sessionId: string;
  evidenceId?: string;
  questionId?: string;
  syncPending: boolean;
};
