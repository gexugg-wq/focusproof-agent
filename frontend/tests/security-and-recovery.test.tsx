import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { POST } from "@/app/api/focusproof/[...path]/route";
import { BuildLog } from "@/features/build-log/BuildLog";
import { EvidencePanel } from "@/features/evidence/EvidencePanel";
import { ReviewPanel } from "@/features/review/ReviewPanel";
import { SessionWorkspace } from "@/features/session/SessionWorkspace";
import { focusProofApi } from "@/lib/api/client";
import type {
  FocusProofEvent,
  RuntimeReviewResult,
  SessionDetail
} from "@/lib/api/contracts";

const malicious = "<img src=x><script>forged()</script>";

function sessionDetail(overrides?: Partial<SessionDetail["state"]>): SessionDetail {
  return {
    sessionId: "sess_security",
    state: {
      sessionId: "sess_security",
      ownerUserId: "owner-a",
      status: "running",
      goal: {
        domain: "general",
        title: "Security boundary",
        goal: "Treat all learner content as text.",
        expectedOutput: "A safe rendering",
        plannedMinutes: 20
      },
      evidence: [],
      answers: {},
      observations: [],
      previousActions: [],
      reviewResult: null,
      adapterMode: "openhands-local-real",
      conversationId: "conv_security",
      runtimeMode: "openhands-local-real",
      ...overrides
    },
    view: {}
  };
}

function queryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false }
    }
  });
}

function wrap(children: React.ReactNode, client = queryClient()) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function expectNoExecutableMarkup(): void {
  expect(document.querySelector("script")).not.toBeInTheDocument();
  expect(document.querySelector("img[src=x]")).not.toBeInTheDocument();
  expect(document.body.innerHTML).not.toContain("onerror=");
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("untrusted content rendering", () => {
  it("renders a malicious learning goal as text", async () => {
    vi.spyOn(focusProofApi, "getSession").mockResolvedValue(
      sessionDetail({
        goal: {
          domain: "general",
          title: "Security boundary",
          goal: malicious,
          expectedOutput: "A safe rendering",
          plannedMinutes: 20
        }
      })
    );
    vi.spyOn(focusProofApi, "getEvents").mockResolvedValue({ events: [] });

    render(wrap(<SessionWorkspace sessionId="sess_security" />));

    expect(await screen.findByText(malicious)).toBeInTheDocument();
    expectNoExecutableMarkup();
  });

  it("renders malicious review findings and questions as text", async () => {
    const completed = sessionDetail({
      status: "reviewed",
      reviewResult: {
        status: "WeakEvidence",
        score: 35,
        confidence: 0.5,
        dimensions: { evidence: 35 },
        findings: [
          {
            severity: "warning",
            message: malicious,
            evidenceIds: [],
            observationRefs: []
          }
        ],
        summary: "Safe summary",
        nextStep: "Add evidence"
      }
    });
    const awaiting: RuntimeReviewResult = {
      sessionId: "sess_security",
      conversationMode: "openhands-local-real",
      usedOpenHandsConversation: true,
      reviewStatus: "awaiting_user",
      agentQuestions: [{ questionId: "q_xss", question: malicious }]
    };
    const { unmount } = render(
      wrap(
        <ReviewPanel
          session={completed}
          onRequestReview={vi.fn().mockResolvedValue(awaiting)}
          onSubmitAnswer={vi.fn()}
        />
      )
    );
    expect(screen.getByText(malicious)).toBeInTheDocument();
    expectNoExecutableMarkup();
    unmount();

    render(
      wrap(
        <ReviewPanel
          session={sessionDetail()}
          onRequestReview={vi.fn().mockResolvedValue(awaiting)}
          onSubmitAnswer={vi.fn()}
        />
      )
    );
    await userEvent.click(screen.getByRole("button", { name: /end learning/i }));
    expect(await screen.findByText(malicious)).toBeInTheDocument();
    expectNoExecutableMarkup();
  });

  it("renders malicious Build Log event labels as text", () => {
    const events: FocusProofEvent[] = [
      {
        id: "evt_xss",
        sessionId: "sess_security",
        type: malicious,
        sequence: 1,
        createdAt: "now",
        actor: "user",
        payload: {}
      }
    ];

    render(<BuildLog events={events} />);

    expect(screen.getByText(malicious)).toBeInTheDocument();
    expectNoExecutableMarkup();
  });
});

describe("BFF security boundary", () => {
  it("forwards only content-type and valid Bearer identity without leaking server errors", async () => {
    const environmentSecret = "sk-ai4b-bff-environment-secret";
    process.env.OPENAI_API_KEY = environmentSecret;
    const fetchMock = vi.fn().mockRejectedValue(
      new Error(`connect failed with ${environmentSecret}`)
    );
    vi.stubGlobal("fetch", fetchMock);
    const request = new NextRequest(
      "http://localhost/api/focusproof/sessions",
      {
        method: "POST",
        headers: {
          authorization: "Bearer browser-secret",
          cookie: "session=browser-secret",
          "x-api-key": "browser-secret",
          "x-untrusted-header": "browser-secret",
          "content-type": "application/json"
        },
        body: "{}"
      }
    );

    const response = await POST(request, {
      params: Promise.resolve({ path: ["sessions"] })
    });

    const upstreamInit = fetchMock.mock.calls[0][1] as RequestInit;
    expect([...new Headers(upstreamInit.headers).entries()]).toEqual([
      ["authorization", "Bearer browser-secret"],
      ["content-type", "application/json"]
    ]);
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain(environmentSecret);
    delete process.env.OPENAI_API_KEY;
  });
});

describe("error recovery", () => {
  it("shows a Build Log error and preserves the previous event list", async () => {
    const client = queryClient();
    const previousEvent: FocusProofEvent = {
      id: "evt_previous",
      sessionId: "sess_security",
      type: "session.created",
      sequence: 1,
      createdAt: "now",
      actor: "system",
      payload: {}
    };
    client.setQueryData(["events", "sess_security"], { events: [previousEvent] });
    vi.spyOn(focusProofApi, "getSession").mockResolvedValue(sessionDetail());
    vi.spyOn(focusProofApi, "getEvents").mockRejectedValue(
      new Error("Build Log could not be refreshed.")
    );

    render(wrap(<SessionWorkspace sessionId="sess_security" />, client));

    expect(await screen.findByText(/session created/i)).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /build log could not be refreshed/i
    );
    expect(screen.getByText(/session created/i)).toBeInTheDocument();
  });

  it("preserves Evidence text after a rejected submission", async () => {
    const submit = vi.fn().mockRejectedValue(new Error("Evidence was rejected."));
    render(
      wrap(
        <EvidencePanel
          sessionId="sess_security"
          domain="general"
          walletAddress={null}
          onSubmitEvidence={submit}
        />
      )
    );
    const input = screen.getByLabelText(/learning evidence/i);
    await userEvent.type(input, "Keep this evidence after failure.");

    await userEvent.click(screen.getByRole("button", { name: /submit evidence/i }));

    expect(await screen.findByText(/evidence was rejected/i)).toBeInTheDocument();
    expect(input).toHaveValue("Keep this evidence after failure.");
  });

  it("preserves Answer text after a rejected submission", async () => {
    const awaiting: RuntimeReviewResult = {
      sessionId: "sess_security",
      conversationMode: "openhands-local-real",
      usedOpenHandsConversation: true,
      reviewStatus: "awaiting_user",
      agentQuestions: [{ questionId: "q_recovery", question: "Explain the change." }]
    };
    const submitAnswer = vi.fn().mockRejectedValue(new Error("Answer was rejected."));
    render(
      wrap(
        <ReviewPanel
          session={sessionDetail()}
          onRequestReview={vi.fn().mockResolvedValue(awaiting)}
          onSubmitAnswer={submitAnswer}
        />
      )
    );
    await userEvent.click(screen.getByRole("button", { name: /end learning/i }));
    const input = await screen.findByLabelText(/answer for q_recovery/i);
    await userEvent.type(input, "Keep this answer after failure.");
    const form = screen.getByRole("button", { name: /submit answer/i }).closest("form");
    fireEvent.submit(form!);

    expect(await screen.findByText(/answer was rejected/i)).toBeInTheDocument();
    expect(input).toHaveValue("Keep this answer after failure.");
    await waitFor(() => expect(submitAnswer).toHaveBeenCalledTimes(1));
  });
});

describe("review state accessibility", () => {
  it("exposes awaiting user as an accessible review state", async () => {
    const awaiting: RuntimeReviewResult = {
      sessionId: "sess_security",
      conversationMode: "openhands-local-scripted-test",
      usedOpenHandsConversation: true,
      reviewStatus: "awaiting_user",
      agentQuestions: [
        {
          questionId: "q_state",
          question: "Explain the central idea."
        }
      ]
    };
    render(
      wrap(
        <ReviewPanel
          session={sessionDetail()}
          onRequestReview={vi.fn().mockResolvedValue(awaiting)}
          onSubmitAnswer={vi.fn()}
        />
      )
    );

    await userEvent.click(screen.getByRole("button", { name: /end learning/i }));

    expect(
      await screen.findByRole("status", { name: /review state/i })
    ).toHaveTextContent(/awaiting user/i);
  });

  it("exposes completed as an accessible review state", () => {
    render(
      wrap(
        <ReviewPanel
          session={sessionDetail({
            status: "reviewed",
            reviewResult: {
              status: "LikelyLearning",
              score: 82,
              confidence: 0.75,
              dimensions: { evidence: 80 },
              findings: [],
              summary: "Completed summary",
              nextStep: "Continue practice"
            }
          })}
          onRequestReview={vi.fn()}
          onSubmitAnswer={vi.fn()}
        />
      )
    );

    expect(
      screen.getByRole("status", { name: /review state/i })
    ).toHaveTextContent(/completed/i);
  });

  it("exposes failed as an accessible review state", async () => {
    render(
      wrap(
        <ReviewPanel
          session={sessionDetail()}
          onRequestReview={vi.fn().mockRejectedValue(new Error("Review failed safely."))}
          onSubmitAnswer={vi.fn()}
        />
      )
    );

    await userEvent.click(screen.getByRole("button", { name: /end learning/i }));

    expect(
      await screen.findByRole("status", { name: /review state/i })
    ).toHaveTextContent(/failed/i);
    expect(screen.getByText("Review failed safely.")).toBeInTheDocument();
  });
});
