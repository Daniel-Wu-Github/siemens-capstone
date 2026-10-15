# Open sponsor questions

Questions the team needs Siemens to answer. When one is answered, record the decision in `docs/decisions.md` (date, decision maker, evidence) and remove it here. This file must stay free of Siemens rows, part numbers, and internal URLs.

Last updated: 2026-10-09.

## Alert rule and tolerances

1. **At or below, or strictly below?** The prototype alerts when `available <= min` (configurable with `ALERT_RULE=below`). Which one do operations want?
2. **Conflicting tolerances.** 11 Min/Max rows have min > max: 10 have max 0 ("max not set") and 1 has both values set. What are the correct values, and who maintains the Min/Max file?
3. **Several storage units in one bin.** A bin can appear on several snapshot rows (storage units or special stock). The prototype sums them into one position, so each bin raises at most one alert. Is the combined bin stock the right quantity to compare with the minimum, or should each storage unit be judged on its own?

## Notifications and workflow

4. **Teams channel(s).** Which Teams channel or channels should receive alerts, and who owns the Power Automate workflow that posts them?
5. **Digest frequency.** Should a digest of all open alerts go out on a schedule (for example once per shift), on demand only, or both?
6. **Who acknowledges?** Which role acknowledges an alert, and should acknowledgement be done in Teams, the dashboard, or both?
7. **Flapping bins.** When stock hovers around the minimum, each new breach sends a new alert. Should there be a cooldown (for example no repeat alert for the same bin within N hours)?
8. **Failed deliveries.** If email or Teams delivery fails, should the system retry automatically (and how often), or is a visible failure plus a manual resend enough?

## Data feeds

9. **Daily snapshot.** In what format and from what start date can a dated daily bin snapshot be supplied? Is it exported from SAP, and at what time of day?
10. **AMR fabrication-direct replenishment.** Which status code (or other field) identifies replenishments that go straight from fabrication to the line by AMR?
11. **BOM per sales order.** Is the BOM the same for every sales order that has the same line item, or can it vary by order configuration?

## Course logistics

12. **UAT tester count.** Can the minimum UAT tester count be waived or met with Siemens staff plus team members?
