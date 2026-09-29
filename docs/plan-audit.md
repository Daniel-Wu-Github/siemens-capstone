# Implementation audit — September 29, 2026

The handoff meets the sponsor's prototype goals: reproducible intake (R1), an interpretable depletion baseline (R2), fair evaluation (R3), actionable alerts (R4), and visible review (R5). It correctly identifies missing material consumption, threshold, and historical stock semantics. It is suitable for an offline prototype with the following changes authorized by the user's implementation request.

1. Deliver one HTML frontend containing all CSS and JavaScript, served by a local Python backend with SQLite persistence. This supersedes the handoff's suggestion to defer the interface.
2. Include a deterministic synthetic demonstration so all forecasting, alert, and evaluation behavior can be reviewed immediately. Every screen and exported result must identify synthetic data. Do not report synthetic evaluation as Siemens accuracy.
3. Support local workbook intake and persist the rows, source checksum, quality findings, and join coverage. Uploaded workbooks remain a separate dataset. Missing consumption, snapshot time, and threshold definitions block production forecasting for that dataset.
4. Compare a trailing consumption baseline with a schedule adjusted consumption estimator. These are transparent fitted estimators; do not describe the candidate as a validated industrial AI model.
5. Evaluate both estimators on identical synthetic held-out scenarios using only observations before each decision. Include event precision, recall, actionable recall, timing error, sample counts, and explicit evaluation limitations.
6. Persist policy edits, scenario load, forecast runs, evaluation results, and alert lifecycle. Recording a simulated delivery adds a stock receipt; marking a dispatch alone must not increase inventory.
7. Prove behavior with failing tests before implementation and browser checks after integration. Live system integration, deployment, and sponsor UAT remain external milestones.

User authorization covers implementation and local processing in this session. It does not imply permission to publish Siemens data or deploy into factory systems. No new approval is needed to build and test the local prototype.
