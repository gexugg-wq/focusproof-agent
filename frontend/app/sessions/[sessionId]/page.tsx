import React from "react";
import { SessionWorkspace } from "@/features/session/SessionWorkspace";

export default async function SessionPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = await params;
  return <SessionWorkspace sessionId={sessionId} />;
}
