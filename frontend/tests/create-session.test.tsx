import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    await userEvent.click(screen.getByRole("button", { name: /start 25 minutes/i }));
    expect(await screen.findByText(/enter a learning topic/i)).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("shows a safe creation failure and stays on the form", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ code: "backend_unavailable", retryable: true }), { status: 503, headers: { "content-type": "application/json" } }));
    render(<CreateSessionForm />);
    await userEvent.type(screen.getByLabelText(/learning topic/i), "TypeScript reducers");
    await userEvent.type(screen.getByLabelText(/this session goal/i), "Explain reducer state transitions clearly.");
    await userEvent.click(screen.getByRole("button", { name: /start 25 minutes/i }));
    expect(await screen.findByText(/Agent Runtime current unavailable|runtime is unavailable/i)).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("shows only the frozen learning inputs and maps hidden protocol defaults", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ sessionId: "sess_abc", status: "running" }), { status: 200 }));
    render(<CreateSessionForm />);
    expect(screen.queryByLabelText(/learning domain/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/expected output/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/planned minutes/i)).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/learning topic/i), "TypeScript reducers");
    await userEvent.type(screen.getByLabelText(/this session goal/i), "Explain reducer state transitions clearly.");
    await userEvent.click(screen.getByRole("button", { name: /start 25 minutes/i }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/sessions/sess_abc"));
    const request = fetchMock.mock.calls[0];
    expect(request[0]).toBe("/api/focusproof/sessions");
    expect(JSON.parse(String(request[1]?.body))).toEqual({
      domain: "general",
      title: "TypeScript reducers",
      goal: "Explain reducer state transitions clearly.",
      expectedOutput: null,
      plannedMinutes: 25
    });
    expect(localStorage.getItem("focusproof.recentSession")).toContain("sess_abc");
  });

  it("linearizes two create submissions dispatched before React rerenders", async () => {
    let finish!: (response: Response) => void;
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation(() => new Promise<Response>((resolve) => { finish = resolve; }));
    render(<CreateSessionForm />);
    await userEvent.type(screen.getByLabelText(/learning topic/i), "TypeScript reducers");
    await userEvent.type(screen.getByLabelText(/this session goal/i), "Explain reducer state transitions clearly.");
    const form = screen.getByRole("button", { name: /start 25 minutes/i }).closest("form");
    fireEvent.submit(form!);
    fireEvent.submit(form!);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    finish(new Response(JSON.stringify({ sessionId: "sess_once", status: "running" }), { status: 200 }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/sessions/sess_once"));
  });
});
