"use client";

import React, { useRef } from "react";
import { CheckCircle2, HelpCircle, RotateCw } from "lucide-react";
import { useState } from "react";
import { getSafeErrorMessage } from "@/lib/api/client";
import type { RuntimeReviewResult, SessionDetail, SyncResponse } from "@/lib/api/contracts";
import { ProofRecording } from "./ProofRecording";

export function ReviewPanel({
  session,
  onRequestReview,
  onSubmitAnswer
}: {
  session: SessionDetail;
  onRequestReview: () => Promise<RuntimeReviewResult>;
  onSubmitAnswer: (input: { questionId: string; answer: string }) => Promise<Pick<SyncResponse, "syncPending">>;
}) {
  const [result, setResult] = useState<RuntimeReviewResult | null>(session.state.reviewResult ? {
    sessionId: session.sessionId,
    conversationMode: session.state.runtimeMode,
    usedOpenHandsConversation: true,
    reviewStatus: "completed",
    reviewResult: session.state.reviewResult
  } : null);
  const [busy, setBusy] = useState(false);
  const [answerBusy, setAnswerBusy] = useState<Record<string, boolean>>({});
  const answerBusyRef = useRef<Record<string, boolean>>({});
  const [message, setMessage] = useState("");
  async function reviewAgain() {
    setBusy(true);
    setMessage("");
    try {
      setResult(await onRequestReview());
    } catch (error) {
      setMessage(getSafeErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  async function submitAnswer(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const formData = new FormData(form);
    const questionId = String(formData.get("questionId") || "");
    const answer = String(formData.get("answer") || "");
    if (answerBusyRef.current[questionId]) return;
    answerBusyRef.current = { ...answerBusyRef.current, [questionId]: true };
    setAnswerBusy((current) => ({ ...current, [questionId]: true }));
    setMessage("");
    try {
      const response = await onSubmitAnswer({ questionId, answer });
      setMessage(response.syncPending ? "Answer saved, waiting for Agent sync." : "Answer submitted.");
    } catch (error) {
      setMessage(getSafeErrorMessage(error));
    } finally {
      answerBusyRef.current = { ...answerBusyRef.current, [questionId]: false };
      setAnswerBusy((current) => ({ ...current, [questionId]: false }));
    }
  }
  const review = result?.reviewResult;
  return (
    <section className="panel grid gap-4 p-4" aria-labelledby="review-heading">
      <h2 id="review-heading" className="text-base font-semibold">Agent review</h2>
      <button className="btn w-fit" disabled={busy} onClick={reviewAgain} type="button">
        <RotateCw size={16} />{result?.reviewStatus === "awaiting_user" ? "Request review again" : "End learning and verify"}
      </button>
      <p aria-live="polite" className="text-sm text-slate-700">{message}</p>
      {result?.reviewStatus === "awaiting_user" ? (
        <div className="grid gap-3">
          {(result.agentQuestions ?? []).map((question) => (
              <form key={question.questionId} onSubmit={submitAnswer} className="grid gap-2 border-t border-line pt-3">
              <input type="hidden" name="questionId" value={question.questionId} />
              <p className="flex gap-2 font-medium"><HelpCircle size={18} />{question.question}</p>
              <label className="field">
                <span>Answer for {question.questionId}</span>
                <textarea name="answer" className="input min-h-20" required />
              </label>
              <button className="btn secondary w-fit" disabled={answerBusy[question.questionId]} type="submit">{answerBusy[question.questionId] ? "Submitting answer..." : "Submit answer"}</button>
            </form>
          ))}
        </div>
      ) : null}
      {review ? (
        <div className="grid gap-4">
          <div className="flex flex-wrap items-center gap-3">
            <CheckCircle2 className="text-green-700" aria-hidden />
            <strong className="text-3xl">{review.score}</strong>
            <span>{review.status}</span>
            <span>Confidence {Math.round(review.confidence * 100)}%</span>
          </div>
          <p className="text-sm text-slate-700">FocusProof judges the credibility of this session evidence, not a judgment of the learner, learner ability, or learner value.</p>
          <dl className="grid gap-2 sm:grid-cols-2">
            {Object.entries(review.dimensions).map(([name, score]) => <div key={name} className="rounded-md border border-line p-2"><dt className="font-medium">{name}</dt><dd>{score}</dd></div>)}
          </dl>
          <ul className="grid gap-2">
            {review.findings.map((finding, index) => <li key={index} className="rounded-md border border-line p-2">{finding.message}</li>)}
          </ul>
          <p>{review.summary}</p>
          <p className="font-medium">Next step: {review.nextStep}</p>
          <ProofRecording />
        </div>
      ) : null}
    </section>
  );
}
