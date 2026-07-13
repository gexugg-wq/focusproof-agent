import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CreateSessionForm } from "@/features/session/CreateSessionForm";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push })
}));

describe("CreateSessionForm", () => {
  beforeEach(() => {
    push.mockClear();
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("validates required fields before creating a session", async () => {
    render(<CreateSessionForm />);
    await userEvent.click(screen.getByRole("button", { name: /start session/i }));
    expect(await screen.findByText(/enter a learning topic/i)).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("shows a safe creation failure and stays on the form", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ code: "backend_unavailable", retryable: true }), { status: 503, headers: { "content-type": "application/json" } }));
    render(<CreateSessionForm />);
    await userEvent.type(screen.getByLabelText(/learning topic/i), "TypeScript reducers");
    await userEvent.type(screen.getByLabelText(/this session goal/i), "Explain reducer state transitions clearly.");
    await userEvent.click(screen.getByRole("button", { name: /start session/i }));
    expect(await screen.findByText(/Agent Runtime current unavailable|runtime is unavailable/i)).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("creates a session, saves recent metadata, and routes to the workspace", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ sessionId: "sess_abc", status: "running" }), { status: 200 }));
    render(<CreateSessionForm />);
    await userEvent.selectOptions(screen.getByLabelText(/learning domain/i), "programming");
    await userEvent.type(screen.getByLabelText(/learning topic/i), "TypeScript reducers");
    await userEvent.type(screen.getByLabelText(/this session goal/i), "Explain reducer state transitions clearly.");
    await userEvent.type(screen.getByLabelText(/expected output/i), "Short note");
    await userEvent.clear(screen.getByLabelText(/planned minutes/i));
    await userEvent.type(screen.getByLabelText(/planned minutes/i), "30");
    await userEvent.click(screen.getByRole("button", { name: /start session/i }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/sessions/sess_abc"));
    expect(fetchMock).toHaveBeenCalledWith("/api/focusproof/sessions", expect.objectContaining({ method: "POST" }));
    expect(localStorage.getItem("focusproof.recentSession")).toContain("sess_abc");
  });
});
