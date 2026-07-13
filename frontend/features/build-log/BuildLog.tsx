import React from "react";
import type { FocusProofEvent } from "@/lib/api/contracts";
import { sortEventsBySequence } from "@/lib/api/errors";

const labels: Record<string, string> = {
  "session.created": "Session created",
  "goal.submitted": "Goal submitted",
  "evidence.submitted": "Evidence submitted",
  "question.asked": "Question asked",
  "answer.submitted": "Answer submitted",
  "verification.requested": "Verification requested",
  "verification.completed": "Verification completed",
  "score.calculated": "Score calculated",
  "review.completed": "Review completed",
  "error.occurred": "Error occurred"
};

export function BuildLog({ events }: { events: FocusProofEvent[] }) {
  const sorted = sortEventsBySequence(events);
  return (
    <section className="panel p-4" aria-labelledby="build-log-heading">
      <h2 id="build-log-heading" className="mb-3 text-base font-semibold">Build Log</h2>
      <ol className="grid gap-2">
        {sorted.map((event) => (
          <li key={event.id} className="rounded-md border border-line p-2 text-sm">
            <div className="font-medium">{labels[event.type] ?? event.type}</div>
            <div className="text-slate-600">#{event.sequence} {event.actor}</div>
          </li>
        ))}
      </ol>
    </section>
  );
}
