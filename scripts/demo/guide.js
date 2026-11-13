/* Testing guide page for the single-file demo (injected into the app script by scripts/build_demo_html.py). */
const GUIDE = /*__GUIDE_FACTS__*/null;
const GUIDE_SHOTS = /*__GUIDE_SHOTS__*/{};
function shot(name, alt) {
  return GUIDE_SHOTS[name] ? `<figure class="shot"><img src="${GUIDE_SHOTS[name]}" alt="${esc(alt)}"><figcaption>${esc(alt)}</figcaption></figure>` : '';
}
function guideTask(id, title, goal, steps, expect, tell, image) {
  return `<section class="panel guide-task" id="task-${id}"><h2><span class="task-n">${id}</span> ${esc(title)}</h2><div class="body">
<p class="goal"><b>Goal:</b> ${goal}</p>
<ol class="steps">${steps.map(s => `<li>${s}</li>`).join('')}</ol>
${image || ''}
<p class="see"><b>You should see:</b> ${expect}</p>
<p class="tell"><b>Tell us if:</b> ${tell}</p></div></section>`;
}
function guidePageHtml() {
  const g = GUIDE, kbd = t => `<kbd>${esc(t)}</kbd>`, b = t => `<b>${esc(t)}</b>`;
  const intro = `<section class="panel"><h2>Start here</h2><div class="body">
<p>This is a <b>practice copy</b> of the stock alert tool. It warns warehouse staff <b>before</b> a bin runs out of parts. You will click through it and tell us what is clear and what is confusing. You cannot break anything.</p>
<ul class="plain">
<li><b>The parts are invented.</b> They are called ${b('DEMO-1001')}, ${b('DEMO-1002')} and so on. No real Siemens data is in this file.</li>
<li><b>Nothing is saved or sent.</b> No email goes out. When you refresh the page, everything goes back to the start. Press ${kbd('F5')} (or ${kbd('Cmd')}+${kbd('R')} on a Mac) any time to start over.</li>
<li><b>Time needed:</b> about 30 minutes for all 8 tasks. You can do just the ones for your job.</li>
<li><b>Words you will see:</b> <b>Min</b> is the lowest amount we want in a bin. <b>Max</b> is the most it holds. <b>Available</b> is how many are in the bin now. <b>Demand per day</b> is how many are used each day. <b>Lead time</b> is how long a refill takes.</li>
</ul>
<div class="legend3">${tag('alert', 'Alert')} The bin is at or below its Min. Refill now.
${tag('predicted', 'Predicted')} It will reach its Min within about ${fmt(g.predictWindow)} hours (refill time plus a safety buffer).
${tag('watch', 'Watch')} It will reach its Min within ${fmt(g.watchHours)} hours. Keep an eye on it.
${tag('none', 'No rate')} We cannot tell how fast it is used, so we do not forecast it.</div>
<p class="note">Use the menu on the left to move between pages. Click <b>✎ Give feedback</b> at the bottom of the menu to leave suggestions or report issues. Come back to this guide any time with <b>Testing guide</b>.</p>
<p><button class="btn" type="button" data-demo-reset>Reset the demo data</button> <span class="note">Puts every change back (same as refreshing the page).</span></p>
</div></section>`;

  const t1 = guideTask(1, 'Find a part and sort the list',
    'Find one part and sort the table the way you like.',
    [`Click ${b('Stock alerts')} in the menu.`,
     `Type ${b(g.searchWord)} in the search box <span class="mk">1</span>. The list gets shorter as you type.`,
     `Clear the box. Open the filter <span class="mk">2</span> and choose ${b('Alerts')}. Only bins at or below Min stay.`,
     `Choose ${b('All positions')} again. Click the ${b('Min')} heading <span class="mk">3</span>. Click it again to flip the order. Click a third time to go back.`],
    `The table changes at once. The heading you clicked shows an arrow. Each row has a coloured word: <span class="tag alert">Alert</span> or <span class="tag ok">OK</span> <span class="mk">4</span>.`,
    `the search misses a part you know is there, the sort goes the wrong way, or a heading is hard to find.`,
    shot('stock', 'Stock alerts: 1 search, 2 filter, 3 click a heading to sort, 4 status.'));

  const t2 = guideTask(2, 'Make a bin run low',
    'Pretend a bin is almost empty and watch the alert appear.',
    [`On ${b('Stock alerts')}, search for ${b(g.okPart.material)} (${esc(g.okPart.description)}). It has ${b(g.okPart.available)} available and a Min of ${b(g.okPart.min)}.`,
     `Click in its ${b('Available')} box <span class="mk">1</span>. Type ${b(g.okPart.min)} and press ${kbd('Enter')} (or click ${b('Set')}).`,
     `Look at the Status <span class="mk">2</span> and the ${b('Last notification')} box on the right <span class="mk">3</span>.`,
     `Click ${b('Reset')} on the same row to put the number back.`],
    `The status turns to <span class="tag alert">Alert</span> because the bin is now at its Min. A message appears under ${b('Last notification')}. It reads like the email a warehouse lead would get. In this practice copy it is only shown here, not sent. After Reset the alert closes by itself.`,
    `the alert does not appear, the message is hard to understand, or you would want to see something else in it.`,
    shot('stock-edit', 'After setting the number to the Min: 1 the box, 2 the Alert, 3 the notification message, 4 the acknowledge button.'));

  const t3 = guideTask(3, 'Mark alerts as seen',
    'Tell the tool “I know about these” without changing any stock.',
    [`Set one or two more bins to 0 the same way as in task 2.`,
     `Click ${b('Acknowledge all below-minimum')} at the top right <span class="mk">4</span>. The button asks you to click again. Click it again.`,
     `Look at the row you edited. It now says <b>Acknowledged</b> under the status.`],
    `A message says how many alerts were acknowledged. The stock numbers do <b>not</b> change. <b>Predicted</b> alerts stay open, because they are only warnings.`,
    `you thought the button would also change the stock, or the name of the button is unclear.`,
    '');

  const t4 = guideTask(4, 'See what will run out soon',
    'Read the list of bins that are forecast to run out, and see how the forecast was worked out.',
    [`Click ${b('Predicted alerts')}. The numbers at the top count ${tag('predicted', 'Predicted')}, ${tag('watch', 'Watch')} and bins already below Min.`,
     `Type ${b(g.predictedPart.material)} in the search box and click its row <span class="mk">3</span>. The right side shows a chart and a box called ${b('How it was calculated')} <span class="mk">4</span>.`,
     `Read the steps from top to bottom: history per day, schedule per day, demand per day, hours to the Min.`,
     `At the bottom of that box, change ${b('Lead class')} <span class="mk">5</span> from <i>stocked</i> to <i>fab</i> and back. (Fab parts are made in the factory, so they can take longer.)`,
     `Try the rate window buttons (${b('30 d')}, ${b('45 d')}, ${b('60 d')}) at the top right to preview projections over different lookback periods. Clicking ${b('Save window and send emails')} saves the chosen window.`],
    `A short line chart goes down toward the Min. The steps show the numbers behind the answer. Row <b>${esc(g.predictedPart.material)}</b> is expected to reach its Min in about ${b(fmt(g.predictedPart.hours_to_min, 1) + ' hours')}.`,
    `a number does not make sense, a word is unclear, or the chart does not help you.`,
    shot('predicted', 'Predicted alerts: 1 counts, 2 filter, 3 pick a row, 4 how it was calculated, 5 lead class.'));

  const t5 = guideTask(5, 'Why do some parts have “No rate”?',
    'Understand which parts are not forecast, and why.',
    [`On ${b('Predicted alerts')}, open the filter <span class="mk">2</span> and choose ${b('No demand rate')}.`,
     `Read the sentence under the numbers <span class="mk">1</span>. It says how many parts have no rate and why.`,
     `Look at the small grey words under each ${tag('none', 'No rate')} tag.`],
    `${fmt(g.noRate)} parts are listed. Each one says a reason, for example “Not in BOM, no transfers”. That means we have no refill history and no production plan for the part, so we do not guess.`,
    `the reason is unclear, or you would want the tool to guess a rate for these parts.`,
    shot('norate', 'No rate: 1 the explanation, 2 the filter, 3 the reason on each row.'));

  const t6 = guideTask(6, 'Find parts with refill problems',
    'Use the flags to spot parts that are refilled too often, in small amounts, or against the plan.',
    [`Click ${b('Replenishment insights')}.`,
     `Read the box ${b('What the flags mean')} <span class="mk">2</span>. Click a flag button <span class="mk">1</span>, for example ${b('Frequent')}. The list shows only parts with that flag. Click a second flag to show parts with either one.`,
     `Try the ${b('Slow')} (1 refill / 30d) or ${b('No refill')} (0 refills / 30d) chips to locate inactive parts that may represent obsolete stock.`,
     `Change the ${b('Refills 30d')} number <span class="mk">3</span> from 8 to 5 and press ${kbd('Tab')}. The counts change.`,
     `Click a row to see a bar chart of its refills on the right. Click ${b('Export CSV')} <span class="mk">4</span> to download the list.`],
    `The number on each flag button matches the number of rows. The chart shows one bar for each refill, as a share of the bin's Max.`,
    `a flag name or its explanation is confusing, or the list is hard to read.`,
    shot('insights', 'Replenishment insights: 1 flag buttons, 2 what they mean, 3 limits you can change, 4 export.'));

  const t7 = guideTask(7, 'Try different settings',
    'See what happens to the alert counts when the forecast settings change. Nothing is saved until you press Save.',
    [`Click ${b('Settings')}.`,
     `Move the ${b('History share')} slider <span class="mk">2</span>. Higher means the forecast trusts past refills more. Lower means it trusts the production plan more.`,
     `Watch the five counts at the top <span class="mk">1</span> and the box ${b('Formulas in use')} <span class="mk">3</span>. They change as you move the slider.`,
     `Change ${b('Stocked lead time (h)')} to 40. More parts become ${tag('predicted', 'Predicted')}.`,
     `Type a name in ${b('Preset name')}, click ${b('Save as preset')}, then click ${b('Apply')} on your preset. Click ${b('Save forecast settings')} <span class="mk">4</span> to keep the numbers while you test.`],
    `The counts and the formulas update at once, with a small “+” or “−” showing the change. A table lists parts that move to another group.`,
    `the counts or formulas are hard to read, or you could not tell what a setting does.`,
    shot('settings', 'Settings: 1 live counts, 2 history share, 3 formulas, 4 save.'));

  const t8 = guideTask(8, 'Look at the data sources',
    'See where the numbers come from.',
    [`Click ${b('Data sources')}.`,
     `Read the cards: how many rows, which sheets, and the recent activity list.`,
     `<i>Optional:</i> try to import a file. This practice copy will say that importing is turned off.`],
    `A list of the data files and a message that importing is off in the practice copy. (In the real tool you can add new files here.)`,
    `you cannot tell what each source is, or something you expected is missing.`,
    '');

  const end = `<section class="panel"><h2>When you are done</h2><div class="body">
<p>Please write down your answers to these questions. Your coordinator will send you the survey link.</p>
<ol class="steps"><li>Which task was the hardest? Why?</li><li>Which word or number did you not understand?</li><li>Did the alerts show up when you expected them?</li><li>What is missing that you would need on the job?</li><li>Would you use this tool every day? What would make you trust it?</li></ol>
<p class="note">Press ${kbd('F5')} to reset everything and try again.</p></div></section>`;

  const toc = `<nav class="panel guide-toc" aria-label="Tasks"><div class="body">${[['1', 'Find a part'], ['2', 'Make a bin run low'], ['3', 'Mark alerts as seen'], ['4', 'What runs out soon'], ['5', 'No rate'], ['6', 'Refill problems'], ['7', 'Settings'], ['8', 'Data sources']].map(([n, t]) => `<a href="#guide" data-task="${n}"><span class="task-n">${n}</span> ${esc(t)}</a>`).join('')}</div></nav>`;
  return head('Testing guide', 'Plain steps for testers · about 30 minutes · practice data only') + intro + toc + t1 + t2 + t3 + t4 + t5 + t6 + t7 + t8 + end;
}
document.addEventListener('click', e => {
  if (e.target.closest('[data-demo-reset]')) { window.DEMO.reset(); invalidate(); loadDatasets().then(() => { stock = null; stockFor = null; render(); toast('Demo data reset.'); }); return; }
  const a = e.target.closest('[data-task]');
  if (a) { e.preventDefault(); const t = document.getElementById('task-' + a.dataset.task); if (t) t.scrollIntoView({behavior: 'smooth', block: 'start'}); }
});
