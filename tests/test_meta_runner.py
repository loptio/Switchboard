"""Meta family through the runner (Phase 9) — refusal, suspend, persist, give-up.

Offline: scripted drafting seam + InMemorySaver + the in-memory DB fixture. These
are the guardrail tests: review is MANDATORY, approve is the ONLY path that
persists, the finalize re-check catches a collision that appeared while the run
sat suspended, and a give_up persists nothing but leaves the audit Output."""

from datetime import datetime, timezone

from langgraph.checkpoint.memory import InMemorySaver

import db
import defs_resolve
import runner
import workflows
from config import Config

T0 = datetime(2026, 6, 11, 6, 0, tzinfo=timezone.utc)


def _cfg(tmp_path) -> Config:
    return Config(feed_url="x", count=1, output_dir=tmp_path, model="m")


def _digest_clone(wf_id="meta-made"):
    wf = workflows.workflow_def_to_dict(workflows.DIGEST_DEF)
    wf["id"] = wf_id
    return wf


def _valid(wf_id="meta-made", agent_id=None):
    wf = _digest_clone(wf_id)
    agents = []
    if agent_id is not None:
        wf["nodes"][0]["agent_ref"] = agent_id
        agents.append(
            {
                "id": agent_id,
                "system_prompt": "Stern {language} summaries only.",
                "prompt_builder_ref": "digest_summary_prompt",
                "parser_ref": "parse_digest",
                "model": None,
                "params": {},
            }
        )
    return {"workflow_def": wf, "agent_defs": agents, "explanation": "ok"}


def _invalid():
    return {"workflow_def": _digest_clone("news"), "agent_defs": [], "explanation": "x"}


class _D:
    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def __call__(self, request, *, model, prior=None, errors=None, feedback=None):
        self.calls.append({"errors": errors, "feedback": feedback})
        return self.script.pop(0)


def _claimed_meta_run(request="给我一个变体", review=True):
    db.create_run(workflow="meta", trigger="manual", review=review, coding_task=request, now=T0)
    return db.claim_next_pending_run(now=T0)


# --- the mandatory-review guardrail -------------------------------------------

def test_straight_meta_run_is_refused(database, tmp_path):
    run = runner.run_once(workflow="meta", coding_task="x", config=_cfg(tmp_path), now=T0)
    assert run.status == "failed"
    assert "requires review" in run.error


def test_claimed_meta_run_without_review_is_refused(database, tmp_path):
    claimed = _claimed_meta_run(review=False)
    final = runner.execute_claimed_run(claimed, config=_cfg(tmp_path), now=T0)
    assert final.status == "failed" and "requires review" in final.error


def test_meta_run_without_request_is_refused(database, tmp_path):
    claimed = _claimed_meta_run(request="   ")
    final = runner.execute_claimed_run(
        claimed, config=_cfg(tmp_path), now=T0,
        checkpointer=InMemorySaver(), draft_fn=_D(_valid()),
    )
    assert final.status == "failed" and "no request" in final.error


# --- suspend → approve → persist ----------------------------------------------

def test_meta_suspends_with_the_proposal_payload(database, tmp_path):
    claimed = _claimed_meta_run()
    final = runner.execute_claimed_run(
        claimed, config=_cfg(tmp_path), now=T0,
        checkpointer=InMemorySaver(), draft_fn=_D(_valid()),
    )
    assert final.status == "awaiting_input"
    reviews = [o for o in db.list_outputs(claimed.id) if o.type == "review"]
    assert len(reviews) == 1
    p = reviews[0].data["proposal"]
    assert p["workflow_def"]["id"] == "meta-made"
    assert p["request"] == "给我一个变体"


def test_approve_persists_defs_and_succeeds(database, tmp_path):
    saver = InMemorySaver()
    cfg = _cfg(tmp_path)
    claimed = _claimed_meta_run()
    runner.execute_claimed_run(
        claimed, config=cfg, now=T0, checkpointer=saver,
        draft_fn=_D(_valid(agent_id="stern-summarize")),
    )
    assert db.get_run(claimed.id).status == "awaiting_input"
    # nothing persisted while suspended (approve-only persistence)
    assert db.get_workflow_def("meta-made") is None
    assert db.get_agent_def("stern-summarize") is None

    db.set_run_decision(claimed.id, {"action": "approve"})
    resumable = db.claim_next_resumable_run(now=T0)
    runner.resume_claimed_run(resumable, config=cfg, now=T0, checkpointer=saver)
    done = db.get_run(claimed.id)
    assert done.status == "success" and done.pending_decision is None

    wf_row = db.get_workflow_def("meta-made")
    assert wf_row is not None and wf_row.definition["id"] == "meta-made"
    ag_row = db.get_agent_def("stern-summarize")
    assert ag_row is not None and ag_row.definition["model"] is None
    assert "created by meta-agent" in wf_row.description
    # the persisted def resolves for execution (DB-override-else-code)
    assert defs_resolve.resolve_workflow_def("meta-made").id == "meta-made"
    # audit output + local file
    metas = [o for o in db.list_outputs(claimed.id) if o.type == "meta"]
    assert len(metas) == 1 and metas[0].data["approved"] is True
    assert (tmp_path / f"meta-{T0.date().isoformat()}.md").exists()


def test_redo_re_suspends_with_feedback(database, tmp_path):
    saver = InMemorySaver()
    cfg = _cfg(tmp_path)
    d = _D(_valid("first-take"), _valid("second-take"))
    claimed = _claimed_meta_run()
    runner.execute_claimed_run(claimed, config=cfg, now=T0, checkpointer=saver, draft_fn=d)

    db.set_run_decision(claimed.id, {"action": "redo", "feedback": "再保守一点"})
    resumable = db.claim_next_resumable_run(now=T0)
    runner.resume_claimed_run(resumable, config=cfg, now=T0, checkpointer=saver, draft_fn=d)
    again = db.get_run(claimed.id)
    assert again.status == "awaiting_input" and again.pending_decision is None
    assert d.calls[1]["feedback"] == "再保守一点"
    reviews = [o for o in db.list_outputs(claimed.id) if o.type == "review"]
    assert reviews[-1].data["proposal"]["workflow_def"]["id"] == "second-take"
    assert db.get_workflow_def("second-take") is None  # still nothing persisted


# --- give_up + finalize re-check ------------------------------------------------

def test_give_up_fails_and_persists_nothing(database, tmp_path):
    claimed = _claimed_meta_run()
    final = runner.execute_claimed_run(
        claimed, config=_cfg(tmp_path), now=T0,
        checkpointer=InMemorySaver(), draft_fn=_D(_invalid(), _invalid(), _invalid()),
    )
    assert final.status == "failed"
    assert "meta proposal rejected" in final.error
    assert db.list_workflow_defs() == []
    metas = [o for o in db.list_outputs(claimed.id) if o.type == "meta"]
    assert len(metas) == 1 and metas[0].data["approved"] is False
    assert metas[0].data["errors"]


def test_finalize_recheck_catches_a_collision_gained_while_suspended(database, tmp_path):
    saver = InMemorySaver()
    cfg = _cfg(tmp_path)
    claimed = _claimed_meta_run()
    runner.execute_claimed_run(
        claimed, config=cfg, now=T0, checkpointer=saver, draft_fn=_D(_valid("meta-made")),
    )
    # While the run sits awaiting approval, someone creates the same id via the
    # synthesizer. Approve must NOT silently shadow or double-write.
    db.create_workflow_def("meta-made", _digest_clone("meta-made"), name="human's")
    db.set_run_decision(claimed.id, {"action": "approve"})
    resumable = db.claim_next_resumable_run(now=T0)
    runner.resume_claimed_run(resumable, config=cfg, now=T0, checkpointer=saver)
    done = db.get_run(claimed.id)
    assert done.status == "failed"
    assert "failed final checks" in done.error
    # the human's row is untouched; no agent orphans
    assert db.get_workflow_def("meta-made").name == "human's"
    assert db.list_agent_defs() == []


# --- the CLI inline path ---------------------------------------------------------

def test_run_review_once_meta_and_cli_resume_approve(database, tmp_path):
    saver = InMemorySaver()
    cfg = _cfg(tmp_path)
    run, outcome = runner.run_review_once(
        workflow="meta", task="做一个变体", config=cfg, now=T0,
        checkpointer=saver, draft_fn=_D(_valid()),
    )
    assert outcome is None
    assert run.status == "awaiting_input"
    assert db.get_run(run.id).coding_task == "做一个变体"

    final, outcome = runner.resume_run(
        run.id, {"action": "approve"}, config=cfg, now=T0, checkpointer=saver
    )
    assert outcome is None
    assert final.status == "success"
    assert db.get_workflow_def("meta-made") is not None
