import { useState } from "react";
import { Link } from "react-router-dom";

import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { ErrorBanner } from "../components/ErrorBanner";
import { Spinner } from "../components/Spinner";
import { MetaProposal } from "../runs/ReviewPanel";
import { useAssistant } from "./useAssistant";
import styles from "./AssistantPage.module.css";

const EXAMPLES = [
  "做一个只看科技与产业视角的简报变体，命名为 tech-brief",
  "给我一个更严格的新闻摘要工作流，summarizer 绝不推断、只贴原文",
  "做一个只保留 3 条最高价值新闻的简报",
];

/** The meta-agent, framed as an assistant: describe the workflow you want, it drafts
 *  a proposal (new workflow + agents, built only from registered components), you
 *  approve it or refine it with feedback. Approving creates the definitions; they
 *  then appear under Workflows / Agents and are runnable. Orchestrates the Phase 9
 *  meta run + human-review gate behind a guided UI (useAssistant). */
export function AssistantPage() {
  const a = useAssistant();
  const [draft, setDraft] = useState("");
  const [feedback, setFeedback] = useState("");

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    void a.submit(draft);
  }

  return (
    <div className={styles.page}>
      <div className={styles.hero}>
        <h1 className={styles.title}>Workflow assistant</h1>
        <p className={styles.subtitle}>
          Describe the workflow you want in plain language — the assistant drafts it
          (a new workflow plus any agents it needs) for you to review and approve.
        </p>
      </div>

      {a.error && <ErrorBanner message={a.error} />}

      {/* IDLE: the prompt box */}
      {(a.phase === "idle" || a.phase === "error") && (
        <Card>
          <form className={styles.promptForm} onSubmit={onSubmit}>
            <textarea
              aria-label="Describe the workflow you want"
              className={styles.prompt}
              placeholder="e.g. 做一个只看科技视角的简报变体，命名为 tech-brief"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
            <div className={styles.actions}>
              <Button type="submit" disabled={!draft.trim()}>
                Draft it
              </Button>
            </div>
          </form>
          <div className={styles.examples}>
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                type="button"
                className={styles.example}
                onClick={() => setDraft(ex)}
              >
                {ex}
              </button>
            ))}
          </div>
        </Card>
      )}

      {/* DRAFTING: the worker is calling the model */}
      {a.phase === "drafting" && (
        <Card>
          <p className={styles.requestEcho}>“{a.request}”</p>
          <div className={styles.status}>
            <Spinner label="Drafting a workflow proposal… this takes a few seconds." />
          </div>
        </Card>
      )}

      {/* REVIEWING: the proposal + approve / refine */}
      {a.phase === "reviewing" && a.proposal && (
        <Card>
          <MetaProposal proposal={a.proposal} />
          <div className={styles.refineBox}>
            <textarea
              aria-label="Refine with feedback"
              className={styles.refineInput}
              placeholder="Not quite right? Tell the assistant what to change, then Refine."
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
            />
            <div className={styles.actions}>
              <Button onClick={() => void a.approve()}>Approve &amp; create</Button>
              <Button
                variant="secondary"
                disabled={!feedback.trim()}
                onClick={() => {
                  void a.refine(feedback);
                  setFeedback("");
                }}
              >
                Refine with feedback
              </Button>
              <Button variant="secondary" onClick={a.reset}>
                Start over
              </Button>
            </div>
          </div>
        </Card>
      )}

      {/* CREATING: persisting the approved defs */}
      {a.phase === "creating" && (
        <Card>
          <div className={styles.status}>
            <Spinner label="Creating the definitions…" />
          </div>
        </Card>
      )}

      {/* DONE: success + links to the new defs */}
      {a.phase === "done" && a.created && (
        <Card className={styles.doneCard}>
          <h2>✅ Created</h2>
          <p>Your new definitions are live and runnable:</p>
          <ul className={styles.createdList}>
            <li>
              Workflow <code>{a.created.workflowId}</code> —{" "}
              <Link to="/workflows">view in Workflows</Link>
            </li>
            {a.created.agentIds.length > 0 && (
              <li>
                Agents:{" "}
                {a.created.agentIds.map((id, i) => (
                  <span key={id}>
                    {i > 0 && ", "}
                    <code>{id}</code>
                  </span>
                ))}{" "}
                — <Link to="/agents">view in Agents</Link>
              </li>
            )}
          </ul>
          <div className={styles.actions} style={{ justifyContent: "center" }}>
            <Link to="/">
              <Button>Go to Runs to try it</Button>
            </Link>
            <Button variant="secondary" onClick={a.reset}>
              Build another
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}
