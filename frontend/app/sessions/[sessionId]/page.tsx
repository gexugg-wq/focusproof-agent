import React from "react";
import { SessionWorkspace } from "@/features/session/SessionWorkspace";

export default function SessionPage({ params }: { params: { sessionId: string } }) {
  return <SessionWorkspace sessionId={params.sessionId} />;
}
