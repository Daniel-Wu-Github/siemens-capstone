# Open sponsor questions

Questions the team needs Siemens to answer. When one is answered, record the decision in `docs/decisions.md` (date, decision maker, evidence) and remove it here. This file must stay free of Siemens rows, part numbers, and internal URLs.

Last updated: 2026-10-29.

## Alert rule and tolerances

1. **At or below, or strictly below?** The prototype alerts when `available <= min` (configurable with `ALERT_RULE=below`). Which rule does plant operations require?
2. **Alerts for Min = 0 bins.** When a bin has `Min = 0`, an empty bin currently triggers an alert (and email notification if configured). Should empty slots with minimum zero generate active alerts, or should zero-minimum bins be suppressed from alerting?
3. **Conflicting tolerances.** 11 Min/Max rows have `min > max`: 10 have `max = 0` ("max not set") and 1 has both set. What are the correct tolerances, and who maintains the authoritative Min/Max reference?
4. **Several storage units in one bin.** A bin can appear across several snapshot rows (storage units or special stock segments). The prototype sums them into a single position so each bin raises at most one alert. Is combined bin stock the right quantity to compare with minimum, or should individual storage units be monitored independently?

## Data feeds and physical inventory

5. **Live Value interpretation.** In the daily bin-status extract (`Bin_Status_YYYY-MM-DD.xlsx`), is the `Live Value` column a real physical inventory count (e.g., from barcode scans or cycle counts) or a calculated value derived by SAP ERP?
6. **Historical daily bin snapshots (Jan–May 2026).** Are daily bin snapshots available for January through May 2026? Having continuous daily snapshots will enable measuring real days under minimum and validating stockout events.
7. **AMR fabrication-direct replenishment.** Which status code (or other field) in the data identifies replenishments that move directly from fabrication to the line via AMR?
8. **BOM per sales order.** Is the BOM identical for every sales order sharing the same line item, or does it vary by custom order configuration?

## Lead times and replenishment dynamics

9. **Fabrication lead time confirmation.** The system currently defaults both stocked items and fab-produced items to a 3-hour lead time (`lead_hours_fab = 3.0`). What is the actual operational lead time for items built in fabrication?
10. **Refill-to-Max vs. standard pack sizes.** Analysis of transfer records shows that the median transfer target is approximately 35% of the bin's configured Max. Does replenishment intend to top bins off to Max, or are transfers dictated by fixed standard carton/pack quantities?
11. **Precedence between schedule and transfer history.** When production schedule and historical transfer cadence diverge significantly, the prototype currently defaults to dropping history if divergence exceeds 50%. Under what operational conditions should the schedule supersede history, or should both be weighted equally?
12. **Review thresholds for View 2 problem parts.** Replenishment Insights flags parts using initial thresholds:
    - Median refill size < 50% of Max, counted only when a part has at least 3 refills in the window
    - More than 8 refills in 30 days
    - Transfer vs. schedule rate divergence > 50%
    Do these thresholds align with plant operations' definition of replenishment inefficiency?

## Notifications and workflow

13. **Teams channel(s).** Which Teams channel should receive reactive and predicted alerts, and who manages the incoming webhook or Power Automate workflow? (Setup guide available in `docs/teams-setup.md`).
14. **Digest frequency.** Should a digest of all open alerts go out on a schedule (e.g. once per shift), on demand only, or both?
15. **Who acknowledges?** Which role acknowledges an alert, and should acknowledgement be done in Teams, the dashboard, or both?
16. **Flapping bins.** When stock hovers around the minimum, each new breach sends a new alert. Should there be a cooldown (for example no repeat alert for the same bin within N hours)?
17. **Failed deliveries.** If email or Teams delivery fails, should the system retry automatically (and how often), or is a visible failure plus a manual resend enough?

## Course logistics

18. **UAT tester count.** Can the minimum UAT tester count requirement be waived or met by combining Siemens staff with project team members?
