# Operator Console presentation

The design is operator-first: the selected incident, current state, next action, assessment, and evidence carry the strongest hierarchy. Progressive disclosure keeps provenance, investigation activity, audit timeline, raw payloads, and diagnostic metadata available without competing with the operational decision.

The console uses dark surfaces, purple emphasis, readable status labels, and separate visual treatments for investigation, human governance, controlled execution, and independent verification. The queue remains the entry point. No framework, dependency, API contract, domain rule, or backend implementation was changed.

The primary operator view keeps one current state, one next action, and one dominant action. Runtime choice is a separate compact selector for the next investigation; the run action remains a single purple control, while the last executed runtime remains provenance rather than selection state. Technical history, activity, audit events, raw payloads, and deep metadata are grouped behind progressive disclosure.

## Operational records

Trust is evidence-first and governance remains visible at the point of decision. Execution and physical attempt stay distinct, verification is an independent read, and reconciliation is an exceptional read-only path for uncertain outcomes rather than a permanent workflow stage.

The overview presents Incident → Investigation → Evidence → Proposal → Human Approval → Execution → Attempt → Verification → Human Resolution. It reads the resources already held by the application, without storing a second workflow state. `Not loaded` describes an absent record in this view, not proof that a stage never occurred. Historical investigation review remains a read-only review of that run.

The existing canonical attempt GET now also supplies physical-attempt presentation for known execution outcomes. It does not invoke any capability. Known outcomes do not cause a reconciliation lookup. If the attempt cannot be loaded, its details remain explicitly unavailable rather than being reconstructed from execution state or audit events.

Reconciliation is a separate amber branch only for `outcome_unknown` or an execution whose persisted completion basis is `reconciliation`. Its controls retain the existing eligibility checks. An acknowledged normal execution never gains a reconciliation stage. Completed reconciliation does not rewrite the original attempt's uncertainty.

Proposal evidence IDs, evidence source/resource/time, human decision time, execution ID, attempt ID and invocation number, verification evidence, and human resolution records are visible at their corresponding boundaries. Payloads remain expandable. Approval, execution, verification, and resolution remain separate explicit operations.

The lifecycle stepper uses quiet indicators rather than repeated status pills. Proposal and approval remain distinct: the proposal is actionable intent, approval is the human authorization boundary, execution is the governed record, and attempt is the physical mutation record. Verification presents expected, observed, and result as independent proof; human resolution remains an explicit final decision.

## Accessibility and responsive layout

- Keyboard skip link, visible focus outlines, semantic regions and pressed queue selection.
- Text labels accompany every status color; uncertainty uses amber and failures use red.
- Single-column workspace on narrower screens, a bounded scrollable queue, wrapping actions, and responsive evidence panels.
- Loading, empty, unavailable and error presentations use the existing application state.
- System fonts, no downloaded font, no decorative telemetry, and no animation dependency.

## Validation

The frontend suite includes the existing 54 workflow tests plus three mocked UI golden journeys: INC-023 service restart, INC-024 application reset, and INC-026 account unlock. They cover evidence provenance, separate approval and execution, canonical acknowledged attempt presentation, absence of reconciliation reads/controls for known outcomes, independent verification evidence, and explicit human resolution.

The existing four backend golden-journey tests exercise the real local simulation with a fake model gateway and isolated test persistence. No live model call is required.

Automated checks: frontend tests, TypeScript, production build, backend golden-journey tests, and Git whitespace review. Browser visual acceptance at desktop/mobile sizes remains outstanding because the computer-use browser provider was unavailable in this session. Automated DOM tests do not substitute for a rendered visual review.
