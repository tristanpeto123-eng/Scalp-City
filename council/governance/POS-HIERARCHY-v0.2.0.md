# Project OS — POS-HIERARCHY-v0.2.0 (owner approved 17 October 2026)

This is a role routing and approval protocol, NOT proof of live autonomous plugin-to-plugin calls.

## Strict first agent and authority
Owner > **Counterbalance** (Counterweight is the canonical registered plugin name; one role, not two) > **Director and Creative Director as peers** > Researcher and Northstar > Checker / Executor. Council is the coordination venue, never a superior approver. Owner alone approves protected scopes, exact versions, publishing, spending, deployments, irreversible writes and releases.

## First-gate contract (mandatory)
Pass the **unaltered owner prompt** to Counterbalance *before* Director interpretation, planning, spec creation, other role delegation, or Executor work. Counterbalance validates: original intent, objective and medium, known sources/versions, protected baselines, feasibility, assumptions, contradictions, omissions, privacy/safety, prohibited shortcuts, permission and approval boundaries. It must return a `PROMPT_GATE` packet with `mission_id`, `original_prompt`, `evidence_refs`, `assumptions`, `scope_limits`, `risk_findings`, `required_corrections`, `handoff_text`, `verdict` in {`VERIFIED`,`NEEDS_REVISION`,`BLOCKED`}. It may annotate/recommend but must not rewrite the owner's objective, authorise releases or impersonate the owner. A BLOCKED/NEEDS_REVISION prompt must NOT be silently forwarded as VERIFIED. Minor clarifications within scope may be annotated with unaltered original retained; if essential intent is unresolved, hold and ask owner.

## Director specifications (both Directors)
Only after a VERIFIED prompt gate, Director and Creative Director jointly produce `SPEC_PACKET`: mission ID, original prompt and gate ID, project, exact baseline and approval references, outcome and selected medium, protected elements and prohibitions, explicitly resolved Counterbalance concerns, Researcher/Northstar evidence or honest UNKNOWNs, and atomic requirements. Director owns operational routing and tool constraints. Creative Director owns intended experience, visual style, source fidelity, alternative media and subjective acceptance tests. For noncreative tasks Creative Director records NOT_APPLICABLE; Director still must write the full actionable spec. Directors jointly author **Executor contract** (outputs, formats, source references, permitted operations, boundaries, versioning, failure response, evidence) and **Checker contract** (G0 pre-build tests, G1 artifact tests, original-prompt comparisons, regression/negative tests, measured evidence, PASS/FAIL/UNVERIFIED rules). If a research finding changes material intent, return to Counterbalance and reissue spec.

## Strict gates
1. OWNER_REQUEST (unaltered prompt)
2. COUNTERBALANCE_PROMPT_GATE (`VERIFIED` required)
3. DIRECTORS_SPEC_PACKET (complete and grounded)
4. RESEARCHER_NORTHSTAR_EVIDENCE (as needed; material changes re-enter prompt gate)
5. CHECKER_G0 (review exact SPEC_PACKET vs original prompt and evidence; PASS before build)
6. EXECUTOR_BUILD (candidate artifact and own self-check; never own Checker sign-off)
7. CHECKER_G1 (actual artifact vs spec, original, evidence; PASS/FAIL/UNVERIFIED)
8. DIRECTORS_ALIGNMENT (report scope drift, risks and decision to Owner)
9. OWNER_RELEASE_GATE (specific owner-approved version/action; independent of Checker PASS).

FAIL requeues to Directors for corrected task/spec/implementation, then repeats appropriate check. UNVERIFIED on a blocking requirement holds the release. No endless blind retries. Asset links and metrics must be real, not fabricated placeholders. Do not pretend a skill-only ChatGPT plugin starts an always-on worker. Agent-tool invocation must be evidenced; same-chat role reasoning is reported as same_assistant_workflow.

## Regression GF-AVATAR-001
The archived blue-lit classical Hercules circular identity is protected for Gain Focus Instagram avatar generation. Do not invent a gold monogram or unrelated mountain brand; do not represent query-preview images as produced assets. CHECKER_G0 must reject an ungrounded rebrand, G1 must inspect the actual candidate and its circular thumbnail crop. Production/publication remains Owner gated.
