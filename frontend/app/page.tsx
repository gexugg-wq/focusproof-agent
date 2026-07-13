import React from "react";
import { CreateSessionForm } from "@/features/session/CreateSessionForm";

export default function HomePage() {
  return (
    <main className="mx-auto grid min-h-screen w-full max-w-5xl content-start gap-6 px-4 py-6 md:px-8">
      <header className="flex items-center justify-between border-b border-line pb-4">
        <strong className="text-lg">FocusProof</strong>
        <span className="text-sm text-slate-600">Learning evidence workspace</span>
      </header>
      <CreateSessionForm />
    </main>
  );
}
