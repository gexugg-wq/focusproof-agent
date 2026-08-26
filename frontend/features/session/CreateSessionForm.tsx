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
  title: z.string().min(2, "Enter a learning topic."),
  goal: z.string().min(8, "Describe the learning goal.")
});

type FormValues = z.infer<typeof schema>;

export function CreateSessionForm() {
  const router = useRouter();
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { title: "", goal: "" }
  });
  const busy = form.formState.isSubmitting;
  const submitting = React.useRef(false);
  const [submitMessage, setSubmitMessage] = React.useState("");
  async function onSubmit(values: FormValues) {
    if (submitting.current) return;
    submitting.current = true;
    setSubmitMessage("");
    try {
      const response = await focusProofApi.createSession({
        domain: "general",
        title: values.title,
        goal: values.goal,
        expectedOutput: null,
        plannedMinutes: 25
      });
      saveRecentSession({ sessionId: response.sessionId, title: values.title, domain: "general", visitedAt: new Date().toISOString() });
      router.push("/sessions/" + response.sessionId);
    } catch (error) {
      setSubmitMessage(getSafeErrorMessage(error));
    } finally {
      submitting.current = false;
    }
  }
  return (
    <form className="panel grid gap-4 p-5" onSubmit={form.handleSubmit(onSubmit)} aria-label="Create learning verification Session">
      <div className="flex items-center gap-2">
        <BookOpen size={20} aria-hidden />
        <h1 className="text-xl font-semibold">Create learning verification Session</h1>
      </div>
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
      <button className="btn w-fit" disabled={busy} type="submit">
        <ArrowRight size={18} aria-hidden />
        {busy ? "Creating..." : "Start 25 minutes"}
      </button>
      <p aria-live="polite" role="status" className="text-sm text-red-700">{submitMessage}</p>
    </form>
  );
}
