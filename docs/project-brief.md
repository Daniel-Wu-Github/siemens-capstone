# Project brief

## Problem and users

Siemens moves materials from a central warehouse to local factory floor storage areas called supermarkets. A part can be exhausted at a supermarket while production is still active; workers then wait for a reactive restock. The sponsor described this workflow and asked the team to explore a prediction model and an actionable notification or visualization. The practical goal is to give warehouse staff sufficient time to replenish a material before its absence interrupts assembly.

Primary users are warehouse or materials staff who act on replenishment needs. Floor operators benefit from fewer interruptions. Supervisors and plant management need a view of risk and performance. The sponsor and Siemens technical reviewers validate the result and any platform or data handling constraints.

## Source and confidence

| Source | What it establishes | Status |
| --- | --- | --- |
| Siemens capstone proposal | Depletion prediction, notification logic, visibility, candidate ERP/MES/WMS data, semester deliverables | Sponsor supplied starting scope |
| September 3 sponsor meeting | Supermarket workflow, a desire to target the materials that unexpectedly run out, and flexibility in model and notification design | Sponsor statements; confirm details as data is reviewed |
| Team checkpoint plan and workshop | Proposed Azure architecture, milestones, Tableau view, Docker, and hybrid process | Team proposal; not all items are approved requirements |
| Supplied workbook | Four concrete sheets of production, schedule, replenishment, and bin data | Available extract; semantics and permissions still need validation |
| Jira import CSV and weekly update | Draft sprint stories and ownership | Planning snapshot; verify current board before acting |

## Scope boundary

The first prototype should use historical and planned data to test whether it can produce a useful material risk list and early alerts. A demo can use a report or simple local view until the sponsor selects a presentation and delivery channel. Direct SCADA integration was explicitly deferred in the team checkpoint plan. Live SAP, MES, or WMS extraction; warehouse dispatch; cloud deployment; and Tableau publishing require separate access and approval.

## Important discrepancies to resolve

- A team plan describes Azure only and ISO/IEC 27001 compliance. In the September 3 meeting, the sponsor described Microsoft as familiar and said a proposed stack would go through Siemens cybersecurity classification. Record Azure as the preferred candidate, not an approved deployment mandate or a compliance claim.
- Team plans name SAP ERP and AGW MES. The meeting transcript also uses “HEW” for a shop floor tool. Confirm system names, ownership, and actual extract lineage.
- Draft documents suggest real time inventory counts, confidence intervals, and status updates. The current workbook alone may not support these claims. Validate feasibility before treating them as first release requirements.
- Project documents differ on cadence and schedule details. Use the active sponsor agreement and current Jira board for dates and ownership.
