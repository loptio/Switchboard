import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";
import { getRun, getRunReview, resumeRun, triggerRun } from "../api/endpoints";
import type { MetaReviewPayload } from "../api/types";

// The assistant orchestrates the Phase 9 meta workflow into a guided
// describe → review → approve/refine flow, hiding the run/worker plumbing:
//   idle      — waiting for a request
//   drafting  — a meta run is enqueued/running; the worker is drafting a proposal
//   reviewing — the run suspended at the gate; `proposal` is ready to approve/refine
//   creating  — an approve was sent; the worker is persisting the defs
//   done      — the defs were created (`created` names them)
//   error     — something failed (`error` explains; the user can start over)
export type AssistantPhase =
  | "idle"
  | "drafting"
  | "reviewing"
  | "creating"
  | "done"
  | "error";

export interface CreatedDefs {
  workflowId: string;
  agentIds: string[];
}

const POLL_MS = 1500;
// A meta draft is one real model call (~10-30s) plus worker pickup; cap the wait so
// a stuck worker surfaces as an error instead of an infinite spinner.
const MAX_POLLS = 80; // ~2 minutes

export interface UseAssistant {
  phase: AssistantPhase;
  proposal: MetaReviewPayload | null;
  created: CreatedDefs | null;
  error: string | null;
  /** The request that produced the current proposal (for display). */
  request: string;
  /** True while a poll loop is in flight (drafting or creating). */
  busy: boolean;
  submit: (request: string) => Promise<void>;
  approve: () => Promise<void>;
  refine: (feedback: string) => Promise<void>;
  reset: () => void;
}

export function useAssistant(): UseAssistant {
  const [phase, setPhase] = useState<AssistantPhase>("idle");
  const [proposal, setProposal] = useState<MetaReviewPayload | null>(null);
  const [created, setCreated] = useState<CreatedDefs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [request, setRequest] = useState("");
  const runId = useRef<string | null>(null);

  // Don't setState after unmount (a poll can resolve late).
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const fail = useCallback((message: string) => {
    if (!mounted.current) return;
    setError(message);
    setPhase("error");
  }, []);

  /** Poll a run until it leaves a transient status; returns the terminal Run or null
   *  (on error / unmount). `until` is the status we're waiting to reach. */
  const pollUntil = useCallback(
    async (id: string, until: "awaiting_input" | "success"): Promise<boolean> => {
      for (let i = 0; i < MAX_POLLS; i++) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        if (!mounted.current) return false;
        let run;
        try {
          run = await getRun(id);
        } catch (e) {
          fail(e instanceof ApiError ? e.detail : "Lost contact with the server.");
          return false;
        }
        if (run.status === until) return true;
        if (run.status === "failed") {
          fail(run.error || "The assistant run failed.");
          return false;
        }
        // pending / running / (for approve) awaiting_input→running → keep polling
      }
      fail("The assistant timed out — is the worker running?");
      return false;
    },
    [fail],
  );

  const loadProposal = useCallback(async (id: string): Promise<boolean> => {
    try {
      const review = await getRunReview(id);
      if (!mounted.current) return false;
      if (!review.proposal) {
        fail("The run suspended without a proposal.");
        return false;
      }
      setProposal(review.proposal);
      setPhase("reviewing");
      return true;
    } catch (e) {
      fail(e instanceof ApiError ? e.detail : "Failed to load the proposal.");
      return false;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fail]);

  const submit = useCallback(
    async (req: string) => {
      const text = req.trim();
      if (!text) return;
      setError(null);
      setProposal(null);
      setCreated(null);
      setRequest(text);
      setPhase("drafting");
      try {
        // Meta always takes the review gate; the request rides the per-run task pipe.
        const run = await triggerRun("meta", true, { coding_task: text });
        runId.current = run.id;
      } catch (e) {
        fail(e instanceof ApiError ? e.detail : "Failed to start the assistant.");
        return;
      }
      if (await pollUntil(runId.current, "awaiting_input")) {
        await loadProposal(runId.current);
      }
    },
    [fail, pollUntil, loadProposal],
  );

  const approve = useCallback(async () => {
    if (!runId.current) return;
    setError(null);
    setPhase("creating");
    const wf = proposal?.workflow_def as { id?: string } | null | undefined;
    const expected: CreatedDefs = {
      workflowId: (wf?.id as string) || "",
      agentIds: (proposal?.agent_defs ?? []).map((a) => (a as { id?: string }).id || ""),
    };
    try {
      await resumeRun(runId.current, "approve");
    } catch (e) {
      fail(e instanceof ApiError ? e.detail : "Failed to submit the approval.");
      return;
    }
    if (await pollUntil(runId.current, "success")) {
      if (!mounted.current) return;
      setCreated(expected);
      setPhase("done");
    }
  }, [proposal, fail, pollUntil]);

  const refine = useCallback(
    async (feedback: string) => {
      if (!runId.current) return;
      setError(null);
      setProposal(null);
      setPhase("drafting");
      try {
        await resumeRun(runId.current, "redo", feedback);
      } catch (e) {
        fail(e instanceof ApiError ? e.detail : "Failed to submit your feedback.");
        return;
      }
      if (await pollUntil(runId.current, "awaiting_input")) {
        await loadProposal(runId.current);
      }
    },
    [fail, pollUntil, loadProposal],
  );

  const reset = useCallback(() => {
    runId.current = null;
    setPhase("idle");
    setProposal(null);
    setCreated(null);
    setError(null);
    setRequest("");
  }, []);

  return {
    phase,
    proposal,
    created,
    error,
    request,
    busy: phase === "drafting" || phase === "creating",
    submit,
    approve,
    refine,
    reset,
  };
}
