import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { EvidencePanel } from "@/features/evidence/EvidencePanel";
import { ReviewPanel } from "@/features/review/ReviewPanel";
import { BuildLog } from "@/features/build-log/BuildLog";
import type { FocusProofEvent, RuntimeReviewResult, SessionDetail } from "@/lib/api/contracts";

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

describe("EvidencePanel", () => {
  it("shows structured evidence submission errors", async () => {
    const submit = vi.fn().mockRejectedValue(new Error("Connection failed. Nothing was submitted."));
    render(wrap(<EvidencePanel sessionId="sess_1" domain="general" walletAddress={null} onSubmitEvidence={submit} />));
    await userEvent.type(screen.getByLabelText(/learning notes/i), "Some notes");
    await userEvent.click(screen.getByRole("button", { name: /submit evidence/i }));
    expect(await screen.findByText(/connection failed/i)).toBeInTheDocument();
  });

  it("submits Web3 evidence without requiring a wallet", async () => {
    const submit = vi.fn().mockResolvedValue({ syncPending: true });
    render(wrap(<EvidencePanel sessionId="sess_1" domain="web3" walletAddress={null} onSubmitEvidence={submit} />));
    await userEvent.click(screen.getByRole("tab", { name: /web3/i }));
    await userEvent.type(screen.getByLabelText(/transaction hash/i), "0xabc");
    await userEvent.type(screen.getByLabelText(/chain id/i), "10143");
    await userEvent.type(screen.getByLabelText(/what did this operation complete/i), "called a contract");
    await userEvent.click(screen.getByRole("button", { name: /submit evidence/i }));
    expect(submit).toHaveBeenCalledWith({ evidenceType: "web3", textContent: "called a contract", metadata: { txHash: "0xabc", chainId: "10143" } });
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
