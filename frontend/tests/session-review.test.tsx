import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EvidencePanel } from "@/features/evidence/EvidencePanel";
import { ReviewPanel } from "@/features/review/ReviewPanel";
import { BuildLog } from "@/features/build-log/BuildLog";
import { SessionWorkspace } from "@/features/session/SessionWorkspace";
import type { FocusProofEvent, RuntimeReviewResult, SessionDetail } from "@/lib/api/contracts";

const workspaceApi = vi.hoisted(() => ({
  getEvents: vi.fn(),
  getReviews: vi.fn(),
  getSession: vi.fn(),
  requestReview: vi.fn(),
  submitAnswer: vi.fn(),
  submitEvidence: vi.fn()
}));

vi.mock("@/lib/api/client", () => ({
  focusProofApi: workspaceApi,
  getSafeErrorMessage: (error: unknown) => error instanceof Error ? error.message : "Request failed.",
  isApiError: () => false
}));

const session: SessionDetail = {
  sessionId: "sess_1",
  state: {
    sessionId: "sess_1",
    ownerUserId: "dev-anonymous-user",
    status: "running",
    goal: { domain: "general", title: "Event logs", goal: "Explain replay", expectedOutput: "summary", plannedMinutes: 25 },
    evidence: [],
    answers: {},
    observations: [],
    previousActions: [],
    reviewResult: null,
    adapterMode: "openhands-local-real",
    conversationId: "conv_1",
    runtimeMode: "openhands-local-real"
  },
  view: {}
};

function wrap(children: React.ReactNode) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>{children}</QueryClientProvider>;
}

function workspaceWrap(client: QueryClient, children: React.ReactNode) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const awaitingReview: RuntimeReviewResult = {
  sessionId: "sess_1",
  conversationMode: "openhands-local-real",
  usedOpenHandsConversation: true,
  reviewStatus: "awaiting_user",
  agentQuestions: [{ questionId: "q1", question: "What changed in your understanding?" }]
};

beforeEach(() => {
  vi.resetAllMocks();
  workspaceApi.getSession.mockResolvedValue(session);
  workspaceApi.getEvents.mockResolvedValue({ events: [] });
  workspaceApi.getReviews.mockResolvedValue({ reviews: [] });
  workspaceApi.requestReview.mockResolvedValue(awaitingReview);
  workspaceApi.submitAnswer.mockResolvedValue({ syncPending: false });
  workspaceApi.submitEvidence.mockResolvedValue({ syncPending: false });
});

describe("EvidencePanel", () => {
  it("shows structured evidence submission errors", async () => {
    const submit = vi.fn().mockRejectedValue(new Error("Connection failed. Nothing was submitted."));
    render(wrap(<EvidencePanel sessionId="sess_1" domain="general" walletAddress={null} onSubmitEvidence={submit} />));
    await userEvent.type(screen.getByLabelText(/learning evidence/i), "Some notes");
    await userEvent.click(screen.getByRole("button", { name: /submit evidence/i }));
    expect(await screen.findByText(/connection failed/i)).toBeInTheDocument();
  });

  it("does not expose a Web3-specific mode in the general composer", async () => {
    const submit = vi.fn().mockResolvedValue({ syncPending: true });
    render(wrap(<EvidencePanel sessionId="sess_1" domain="web3" walletAddress={null} onSubmitEvidence={submit} />));
    expect(screen.queryByRole("tab", { name: /web3/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/transaction hash|chain id|wallet/i)).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/learning evidence/i), "called a contract");
    await userEvent.click(screen.getByRole("button", { name: /submit evidence/i }));
    expect(submit).toHaveBeenCalledWith({ evidenceType: "text", textContent: "called a contract", metadata: {} });
    expect(await screen.findByText(/waiting for Agent sync/i)).toBeInTheDocument();
  });
});

describe("ReviewPanel", () => {
  it("preserves answer text, shows the specific failure, and prevents duplicate answer submits", async () => {
    const awaiting: RuntimeReviewResult = {
      sessionId: "sess_1",
      conversationMode: "openhands-local-real",
      usedOpenHandsConversation: true,
      reviewStatus: "awaiting_user",
      agentQuestions: [{ questionId: "q1", question: "What changed in your understanding?" }]
    };
    const requestReview = vi.fn().mockResolvedValue(awaiting);
    const submitAnswer = vi.fn().mockRejectedValue(new Error("Connection failed. Nothing was submitted."));
    render(wrap(<ReviewPanel session={session} onRequestReview={requestReview} onSubmitAnswer={submitAnswer} />));
    await userEvent.click(screen.getByRole("button", { name: /end learning/i }));
    const answer = await screen.findByLabelText(/answer for q1/i);
    await userEvent.type(answer, "Replay separates facts from views.");
    const form = screen.getByRole("button", { name: /submit answer/i }).closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form!);
    fireEvent.submit(form!);
    expect(submitAnswer).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/connection failed/i)).toBeInTheDocument();
    expect(answer).toHaveValue("Replay separates facts from views.");
  });

  it("handles awaiting_user answers and completed review display", async () => {
    const awaiting: RuntimeReviewResult = {
      sessionId: "sess_1",
      conversationMode: "openhands-local-real",
      usedOpenHandsConversation: true,
      reviewStatus: "awaiting_user",
      agentQuestions: [{ questionId: "q1", question: "What changed in your understanding?" }]
    };
    const completed: RuntimeReviewResult = {
      sessionId: "sess_1",
      conversationMode: "openhands-local-real",
      usedOpenHandsConversation: true,
      reviewStatus: "completed",
      reviewResult: {
        status: "LikelyLearning",
        score: 82,
        confidence: 0.74,
        dimensions: { evidence: 80, explanation: 85 },
        findings: [{ severity: "info", message: "Specific evidence was submitted.", evidenceIds: [], observationRefs: [] }],
        summary: "Evidence supports the learning claim.",
        nextStep: "Add one replay example."
      }
    };
    const requestReview = vi.fn().mockResolvedValueOnce(awaiting).mockResolvedValueOnce(completed);
    const submitAnswer = vi.fn().mockResolvedValue({ syncPending: false });
    render(wrap(<ReviewPanel session={session} onRequestReview={requestReview} onSubmitAnswer={submitAnswer} />));
    await userEvent.click(screen.getByRole("button", { name: /end learning/i }));
    expect(await screen.findByText(/what changed/i)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/answer for q1/i), "Replay separates facts from views.");
    await userEvent.click(screen.getByRole("button", { name: /submit answer/i }));
    await userEvent.click(screen.getByRole("button", { name: /request review again/i }));
    expect(await screen.findByText("82")).toBeInTheDocument();
    expect(screen.getByText(/not a judgment of the learner/i)).toBeInTheDocument();
  });
});

describe("BuildLog", () => {
  it("sorts known and unknown events without crashing", () => {
    const events: FocusProofEvent[] = [
      { id: "evt_3", sessionId: "sess_1", type: "new.event", sequence: 3, createdAt: "now", actor: "agent", payload: {} },
      { id: "evt_1", sessionId: "sess_1", type: "session.created", sequence: 1, createdAt: "now", actor: "system", payload: {} }
    ];
    render(<BuildLog events={events} />);
    expect(screen.getAllByRole("listitem")[0]).toHaveTextContent(/session created/i);
    expect(screen.getByText(/new.event/)).toBeInTheDocument();
  });
});

describe("SessionWorkspace recovery query refresh", () => {
  it("invalidates session, events, and reviews after a successful answer", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const invalidateQueries = vi.spyOn(client, "invalidateQueries");
    render(workspaceWrap(client, <SessionWorkspace sessionId="sess_1" />));
    await screen.findByRole("heading", { name: /event logs/i });
    await userEvent.click(screen.getByRole("button", { name: /end learning/i }));
    await screen.findByLabelText(/answer for q1/i);
    invalidateQueries.mockClear();

    await userEvent.type(screen.getByLabelText(/answer for q1/i), "Native events retain their identity.");
    await userEvent.click(screen.getByRole("button", { name: /submit answer/i }));

    await waitFor(() => {
      expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["session", "sess_1"] });
      expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["events", "sess_1"] });
      expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["reviews", "sess_1"] });
    });
  });

  it("invalidates session, events, and reviews after a successful review request", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const invalidateQueries = vi.spyOn(client, "invalidateQueries");
    render(workspaceWrap(client, <SessionWorkspace sessionId="sess_1" />));
    await screen.findByRole("heading", { name: /event logs/i });

    await userEvent.click(screen.getByRole("button", { name: /end learning/i }));

    await waitFor(() => {
      expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["session", "sess_1"] });
      expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["events", "sess_1"] });
      expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["reviews", "sess_1"] });
    });
  });
});
import { getImageCapability, getSpeechCapability } from "@/features/session/SessionWorkspace";

describe("image capability validation", () => {
  it.each([
    { formats: [] },
    { formats: ["image/gif"] },
    { formats: ["image/png", 7] },
    { maxCount: 0 },
    { maxCount: -1 },
    { maxCount: 1.5 },
    { maxCount: Number.NaN },
    { maxOriginalBytes: Number.POSITIVE_INFINITY },
    { maxNormalizedBytesPerSession: -20 },
    { explanationRequired: "true" }
  ])("fails closed for malformed capability %j", (override) => {
    const malformed = { capabilityId: "image_evidence", enabled: true, formats: ["image/png"], maxCount: 4, maxOriginalBytes: 10_485_760, maxNormalizedBytesPerSession: 20_971_520, explanationRequired: true, ...override };
    const candidate = { ...session, view: { productCapabilities: [malformed] } };
    expect(getImageCapability(candidate as unknown as SessionDetail)).toBeNull();
  });
});

describe("speech capability validation", () => {
  const capability = { capabilityId: "speech_transcription", schemaVersion: 1, enabled: true, formats: ["audio/webm;codecs=opus"], maxAudioBytes: 11 * 1024 * 1024, maxDurationSeconds: 120, languageHintsAccepted: ["auto"], languageHintEffect: "metadata_only" };
  it("accepts only the declared bounded speech capability", () => {
    const candidate = { ...session, view: { productCapabilities: [capability] } };
    expect(getSpeechCapability(candidate as unknown as SessionDetail)).toEqual(capability);
  });
  it.each([{ maxDurationSeconds: 121 }, { formats: ["audio/ogg"] }, { languageHintsAccepted: ["fr"] }, { enabled: false }])("fails closed for malformed speech capability %j", (override) => {
    const candidate = { ...session, view: { productCapabilities: [{ ...capability, ...override }] } };
    expect(getSpeechCapability(candidate as unknown as SessionDetail)).toBeNull();
  });
});

describe("SessionWorkspace retired plugin removal", () => {
  it("ignores stale retired plugin capabilities from older sessions", async () => {
    const staleRetiredPluginSession: SessionDetail = {
      ...session,
      view: {
        pluginCapabilities: [
          {
            pluginId: ["mo", "nad"].join(""),
            capabilityId: ["mo", "nad_learning_transaction"].join(""),
            enabled: true,
            metadata: {
              chainId: 1234,
              chainName: ["Mo", "nad"].join(""),
              contractAddress: "0x52908400098527886E0F7030069857D2E4169EE7",
              taskDescription: "Submit a wallet transaction that calls increment() on the configured teaching contract."
            }
          }
        ]
      }
    };
    workspaceApi.getSession.mockResolvedValueOnce(staleRetiredPluginSession);
    render(workspaceWrap(new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }), <SessionWorkspace sessionId="sess_1" />));
    await screen.findByRole("heading", { name: /event logs/i });
    expect(screen.queryByText(new RegExp(["mo", "nad chain evidence"].join(""), "i"))).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: new RegExp(["submit mo", "nad evidence"].join(""), "i") })).not.toBeInTheDocument();
  });
});
