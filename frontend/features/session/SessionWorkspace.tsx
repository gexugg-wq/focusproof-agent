"use client";

import React from "react";
import { Activity, BookOpen } from "lucide-react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { focusProofApi, getSafeErrorMessage, isApiError } from "@/lib/api/client";
import type { SubmitEvidenceRequest } from "@/lib/api/contracts";
import { BuildLog } from "@/features/build-log/BuildLog";
import { EvidencePanel } from "@/features/evidence/EvidencePanel";
import { ReviewPanel } from "@/features/review/ReviewPanel";
import { WalletPanel } from "@/features/wallet/WalletPanel";
import { saveRecentSession } from "@/lib/storage/recent-sessions";

export function SessionWorkspace({ sessionId }: { sessionId: string }) {
  const queryClient = useQueryClient();
  const [walletAddress, setWalletAddress] = useState<string | null>(null);
  const sessionQuery = useQuery({ queryKey: ["session", sessionId], queryFn: () => focusProofApi.getSession(sessionId) });
  const eventsQuery = useQuery({ queryKey: ["events", sessionId], queryFn: () => focusProofApi.getEvents(sessionId) });
  const reviewsQuery = useQuery({ queryKey: ["reviews", sessionId], queryFn: () => focusProofApi.getReviews(sessionId) });
  const evidence = useMutation({
    mutationFn: (payload: SubmitEvidenceRequest) => focusProofApi.submitEvidence(sessionId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
      void queryClient.invalidateQueries({ queryKey: ["events", sessionId] });
      void queryClient.invalidateQueries({ queryKey: ["reviews", sessionId] });
    }
  });
  const answer = useMutation({
    mutationFn: (input: { questionId: string; answer: string }) => focusProofApi.submitAnswer(sessionId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
      void queryClient.invalidateQueries({ queryKey: ["events", sessionId] });
      void queryClient.invalidateQueries({ queryKey: ["reviews", sessionId] });
    }
  });
  const review = useMutation({
    mutationFn: () => focusProofApi.requestReview(sessionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
      void queryClient.invalidateQueries({ queryKey: ["events", sessionId] });
      void queryClient.invalidateQueries({ queryKey: ["reviews", sessionId] });
    }
  });
  if (sessionQuery.isLoading) return <main className="p-6">Loading session...</main>;
  if (sessionQuery.error || !sessionQuery.data) {
    const message = isApiError(sessionQuery.error) ? sessionQuery.error.message : "Session could not be loaded.";
    return <main className="p-6" role="alert">{message}</main>;
  }
  const session = sessionQuery.data;
  saveRecentSession({ sessionId, title: session.state.goal.title, domain: session.state.goal.domain, visitedAt: new Date().toISOString() });
  const web3Context = session.state.goal.domain.toLowerCase() === "web3";
  return (
    <main className="grid min-h-screen gap-4 p-4 lg:grid-cols-[280px_minmax(0,1fr)_340px]">
      <header className="lg:col-span-3 flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
        <strong className="text-lg">FocusProof</strong>
        <span className="inline-flex items-center gap-2 text-sm"><Activity size={16} />Session {session.state.status}</span>
      </header>
      <aside className="panel grid h-fit gap-3 p-4">
        <h1 className="flex items-center gap-2 text-lg font-semibold"><BookOpen size={18} />{session.state.goal.title}</h1>
        <p className="text-sm">{session.state.goal.goal}</p>
        <dl className="grid gap-2 text-sm">
          <div><dt className="font-medium">Domain</dt><dd>{session.state.goal.domain}</dd></div>
          <div><dt className="font-medium">Expected output</dt><dd>{session.state.goal.expectedOutput || "Not specified"}</dd></div>
          <div><dt className="font-medium">Planned minutes</dt><dd>{session.state.goal.plannedMinutes ?? "Not specified"}</dd></div>
        </dl>
        {web3Context ? <WalletPanel onWalletChange={setWalletAddress} /> : null}
      </aside>
      <div className="grid content-start gap-4">
        <EvidencePanel
          sessionId={sessionId}
          domain={session.state.goal.domain}
          walletAddress={walletAddress}
          submittedEvidence={session.state.evidence}
          onSubmitEvidence={(payload) => evidence.mutateAsync(payload)}
        />
        <ReviewPanel session={session} onRequestReview={() => review.mutateAsync()} onSubmitAnswer={(input) => answer.mutateAsync(input)} />
      </div>
      <div className="grid h-fit gap-2">
        {eventsQuery.error || reviewsQuery.error ? (
          <p className="text-sm text-red-700" role="alert">
            {getSafeErrorMessage(eventsQuery.error ?? reviewsQuery.error)}
          </p>
        ) : null}
        <BuildLog events={eventsQuery.data?.events ?? []} />
      </div>
    </main>
  );
}
