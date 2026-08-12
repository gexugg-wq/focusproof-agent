import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionWorkspace } from "@/features/session/SessionWorkspace";

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

function wrap(children: React.ReactNode) {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      {children}
    </QueryClientProvider>
  );
}

const baseSession = {
  sessionId: "sess_monad",
  state: {
    sessionId: "sess_monad",
    ownerUserId: "dev-anonymous-user",
    status: "running",
    goal: {
      domain: "web3",
      title: "Monad increment demo",
      goal: "Explain why increment() changes only the caller state.",
      expectedOutput: "notes",
      plannedMinutes: 25
    },
    evidence: [],
    answers: {},
    observations: [],
    previousActions: [],
    reviewResult: null,
    adapterMode: "openhands-local-scripted-test",
    conversationId: "conv_monad",
    runtimeMode: "openhands-local-scripted-test"
  },
  view: {}
};

const monadCapability = {
  pluginId: "monad",
  capabilityId: "monad_learning_transaction",
  enabled: true,
  metadata: {
    chainId: 1234,
    chainName: "Monad",
    contractAddress: "0x52908400098527886E0F7030069857D2E4169EE7",
    explorerTxBaseUrl: "https://explorer.example.test/tx/",
    operationLabel: "Call increment() on MonadLearningCounter",
    taskDescription: "Submit a wallet transaction that calls increment() on the configured teaching contract."
  }
};

beforeEach(() => {
  vi.resetAllMocks();
  workspaceApi.getEvents.mockResolvedValue({ events: [] });
  workspaceApi.getReviews.mockResolvedValue({ reviews: [] });
  workspaceApi.requestReview.mockResolvedValue({ reviewStatus: "failed" });
  workspaceApi.submitAnswer.mockResolvedValue({ syncPending: false });
  workspaceApi.submitEvidence.mockResolvedValue({ syncPending: false, evidenceId: "ev_monad" });
});

describe("Monad plugin workspace", () => {
  it("keeps the Monad panel hidden when no capability is exposed", async () => {
    workspaceApi.getSession.mockResolvedValue(baseSession);
    render(wrap(<SessionWorkspace sessionId="sess_monad" />));
    await screen.findByRole("heading", { name: /monad increment demo/i });
    expect(screen.queryByText(/MonadLearningCounter/i)).not.toBeInTheDocument();
  });

  it("renders a manual Monad evidence panel when the capability is enabled", async () => {
    workspaceApi.getSession.mockResolvedValue({
      ...baseSession,
      view: { pluginCapabilities: [monadCapability] }
    });
    render(wrap(<SessionWorkspace sessionId="sess_monad" />));
    await screen.findByText(/Submit a wallet transaction/i);
    expect(screen.getByRole("button", { name: /connect wallet/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/wallet address/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/transaction hash/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/contract address/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/operation explanation/i)).toBeInTheDocument();
  });

  it("keeps the wallet connect entry hidden when the Monad capability is not enabled", async () => {
      workspaceApi.getSession.mockResolvedValue({
      ...baseSession,
      state: {
        ...baseSession.state,
        goal: { ...baseSession.state.goal, domain: "general" }
      }
    });
    render(wrap(<SessionWorkspace sessionId="sess_monad" />));
    await screen.findByRole("heading", { name: /monad increment demo/i });
    expect(screen.queryByRole("button", { name: /connect wallet/i })).not.toBeInTheDocument();
  });

  it("submits manual Monad evidence through the existing evidence API", async () => {
    workspaceApi.getSession.mockResolvedValue({
      ...baseSession,
      view: { pluginCapabilities: [monadCapability] }
    });
    render(wrap(<SessionWorkspace sessionId="sess_monad" />));
    await screen.findByText(/Submit a wallet transaction/i);
    await userEvent.type(screen.getByLabelText(/wallet address/i), "0xde709f2102306220921060314715629080e2fb77");
    await userEvent.type(screen.getByLabelText(/transaction hash/i), "0x" + "ab".repeat(32));
    await userEvent.clear(screen.getByLabelText(/contract address/i));
    await userEvent.type(screen.getByLabelText(/contract address/i), "0x52908400098527886E0F7030069857D2E4169EE7");
    await userEvent.type(screen.getByLabelText(/operation explanation/i), "I used an existing transaction hash from the demo fixture.");
    await userEvent.click(screen.getByRole("button", { name: /submit monad evidence/i }));

    await waitFor(() => {
      expect(workspaceApi.submitEvidence).toHaveBeenCalledWith(
        "sess_monad",
        {
          evidenceType: "monad_transaction",
          metadata: {
            walletAddress: "0xde709f2102306220921060314715629080e2fb77",
            transactionHash: "0x" + "ab".repeat(32),
            contractAddress: "0x52908400098527886E0F7030069857D2E4169EE7",
            operationExplanation: "I used an existing transaction hash from the demo fixture."
          }
        }
      );
    });
  });

  it("preserves manual inputs when Monad evidence submission fails", async () => {
    workspaceApi.getSession.mockResolvedValue({
      ...baseSession,
      view: { pluginCapabilities: [monadCapability] }
    });
    workspaceApi.submitEvidence.mockRejectedValue(new Error("Backend unavailable."));
    render(wrap(<SessionWorkspace sessionId="sess_monad" />));
    await screen.findByText(/Submit a wallet transaction/i);

    const wallet = screen.getByLabelText(/wallet address/i);
    const tx = screen.getByLabelText(/transaction hash/i);
    const explanation = screen.getByLabelText(/operation explanation/i);
    await userEvent.type(wallet, "0xde709f2102306220921060314715629080e2fb77");
    await userEvent.type(tx, "0x" + "ab".repeat(32));
    await userEvent.type(explanation, "I will explain the existing demo transaction.");
    await userEvent.click(screen.getByRole("button", { name: /submit monad evidence/i }));

    expect(await screen.findByText(/backend unavailable/i)).toBeInTheDocument();
    expect(wallet).toHaveValue("0xde709f2102306220921060314715629080e2fb77");
    expect(tx).toHaveValue("0x" + "ab".repeat(32));
    expect(explanation).toHaveValue("I will explain the existing demo transaction.");
  });
});

