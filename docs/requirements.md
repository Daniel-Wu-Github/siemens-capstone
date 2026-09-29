# Candidate requirements and validation

These are working hypotheses for sponsor review. They describe observable outcomes; the team has not yet agreed on numeric thresholds or production readiness.

| ID | Candidate outcome | How to verify in a prototype | Open decision |
| --- | --- | --- | --- |
| R1 | The team can reproduce an analysis dataset from approved extracts and report missing or inconsistent fields. | Schema, key, date, unit, and reconciliation checks with a data quality summary. | Authoritative sources, refresh cadence, material and order join rules. |
| R2 | A baseline estimates time until a monitored supermarket material reaches a defined minimum. | Historical replay using only information available at each prediction time. | Target definition, threshold source, observation frequency, and acceptable error. |
| R3 | A candidate model is compared fairly with the baseline. | Same time based evaluation windows and event metrics for both. | Which metric matters most to operations; minimum acceptable improvement. |
| R4 | An alert identifies material, location, risk, expected lead time, and reason. | Sponsor reviews replayed alert examples for clarity and actionability. | Required fields, warehouse lead time, suppression and repeat rules, channel. |
| R5 | A reviewer can inspect forecasts and alerts for a selected material and time period. | Demonstration with approved or synthetic data. | Report, local UI, Tableau, or another review format. |

## Evaluation questions

- What counts as a stockout: zero available stock, crossing minimum tolerance, an emergency replenishment, or a logged production interruption?
- How early must an alert arrive to let the warehouse act? What is the cost of a false alarm versus a missed event?
- Are there enough true depletion events, reliable stock snapshots, and material consumption observations to support supervised prediction?
- How do new or rarely used materials behave when there is little history?
- How should the system respond to stale, missing, duplicated, or delayed data?

## Proposed acceptance evidence

Before calling the prototype successful, show a documented data lineage, reproducible historical replay, baseline comparison, alert examples reviewed by an operations stakeholder, and limitations for materials or periods where prediction is unsupported. Record the sponsor's chosen numeric thresholds and review result here when available.

## Out of scope until separately approved

Automatic purchase orders, writes to Siemens systems, production alert dispatch, SCADA ingestion, factory wide rollout, and claims of compliance certification.
