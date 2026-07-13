"use client";

import React from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowRight, BookOpen } from "lucide-react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { focusProofApi, getSafeErrorMessage } from "@/lib/api/client";
import { saveRecentSession } from "@/lib/storage/recent-sessions";

const schema = z.object({
  domain: z.string().min(1),
  customDomain: z.string().optional(),
  title: z.string().min(2, "Enter a learning topic."),
  goal: z.string().min(8, "Describe the learning goal."),
  expectedOutput: z.string().optional(),
  plannedMinutes: z.coerce.number().int().min(1).max(480)
});

type FormValues = z.infer<typeof schema>;

const domains = [
  ["general", "General knowledge"],
  ["programming", "Programming"],
  ["math", "Math"],
  ["language", "Language"],
  ["web3", "Web3"],
  ["custom", "Custom domain"]
];

export function CreateSessionForm() {
  const router = useRouter();
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { domain: "general", title: "", goal: "", expectedOutput: "", plannedMinutes: 25 }
  });
  const selectedDomain = form.watch("domain");
  const busy = form.formState.isSubmitting;
  const [submitMessage, setSubmitMessage] = React.useState("");
  async function onSubmit(values: FormValues) {
    setSubmitMessage("");
    const domain = values.domain === "custom" ? values.customDomain?.trim() || "custom" : values.domain;
    try {
      const response = await focusProofApi.createSession({
        domain,
        title: values.title,
        goal: values.goal,
        expectedOutput: values.expectedOutput || null,
        plannedMinutes: values.plannedMinutes
      });
      saveRecentSession({ sessionId: response.sessionId, title: values.title, domain, visitedAt: new Date().toISOString() });
      router.push("/sessions/" + response.sessionId);
    } catch (error) {
      setSubmitMessage(getSafeErrorMessage(error));
    }
  }
  return (
    <form className="panel grid gap-4 p-5" onSubmit={form.handleSubmit(onSubmit)} aria-label="Create learning verification Session">
      <div className="flex items-center gap-2">
        <BookOpen size={20} aria-hidden />
        <h1 className="text-xl font-semibold">Create learning verification Session</h1>
      </div>
      <div className="field">
        <label htmlFor="domain">Learning domain</label>
        <select id="domain" className="input" {...form.register("domain")}>
          {domains.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </div>
      {selectedDomain === "custom" ? (
        <div className="field">
          <label htmlFor="customDomain">Custom domain</label>
          <input id="customDomain" className="input" {...form.register("customDomain")} />
        </div>
      ) : null}
      <div className="field">
        <label htmlFor="title">Learning topic</label>
        <input id="title" className="input" {...form.register("title")} />
        <p role="alert" className="text-sm text-red-700">{form.formState.errors.title?.message}</p>
      </div>
      <div className="field">
        <label htmlFor="goal">This session goal</label>
        <textarea id="goal" className="input min-h-24" {...form.register("goal")} />
        <p role="alert" className="text-sm text-red-700">{form.formState.errors.goal?.message}</p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="field">
          <label htmlFor="expectedOutput">Expected output</label>
          <input id="expectedOutput" className="input" {...form.register("expectedOutput")} />
        </div>
        <div className="field">
          <label htmlFor="plannedMinutes">Planned minutes</label>
          <input id="plannedMinutes" type="number" className="input" {...form.register("plannedMinutes")} />
        </div>
      </div>
      <button className="btn w-fit" disabled={busy} type="submit">
        <ArrowRight size={18} aria-hidden />
        {busy ? "Creating..." : "Start Session"}
      </button>
      <p aria-live="polite" role="status" className="text-sm text-red-700">{submitMessage}</p>
    </form>
  );
}
