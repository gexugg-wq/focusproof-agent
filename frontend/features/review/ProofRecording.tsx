import React from "react";
import { LockKeyhole } from "lucide-react";

export function ProofRecording() {
  return (
    <section className="panel grid gap-2 p-4" aria-labelledby="proof-heading">
      <h3 id="proof-heading" className="font-semibold">Record learning proof</h3>
      <p className="text-sm text-slate-700">On-chain proof is not enabled yet.</p>
      <button className="btn secondary w-fit" disabled type="button"><LockKeyhole size={16} />Proof recording disabled</button>
    </section>
  );
}
