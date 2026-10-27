// Real caption helper and source binding; no browser/network or money operations.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const source = fs.readFileSync(path.join(root, 'src/goal_analysis/web/app.js'), 'utf8').replace(/\r\n/g, '\n');
const start = source.indexOf('  function recordedReturnPresentation(');
const end = source.indexOf('  function performanceViewLabels(', start);
assert.ok(start >= 0 && end > start);
const context = {arr: value => Array.isArray(value) ? value : [],
  isNumber: value => typeof value === 'number' && Number.isFinite(value)};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);
const cases = [];
function check(name, fn) { fn(); cases.push(name); }
check('legacy_reported_amount_has_no_gross_cash_or_system_profit_claim', () => {
  const value = context.recordedReturnPresentation({payout_eur: 30, monetary_projection: {records: [
    {status: 'KNOWN', payout_basis: 'LEGACY_REPORTED_RETURN_BASIS_UNSPECIFIED', gross_sports_return_eur: null}]}}, false);
  assert.equal(value.title, 'Rögzített elszámolási összeg');
  assert.ok(value.detail.includes('bruttó sportjogcím részben vagy teljesen ismeretlen'));
  assert.ok(value.detail.includes('Banki jóváírás és költségek utáni rendszereredmény: ismeretlen'));
});
check('typed_gross_has_proven_sports_role_but_cash_and_cost_remain_unknown', () => {
  const value = context.recordedReturnPresentation({monetary_projection: {records: [
    {status: 'KNOWN', gross_sports_return_eur: 30}]}}, false);
  assert.ok(value.detail.includes('bruttó sportjogcíme igazolt'));
  assert.ok(value.detail.includes('rendszereredmény: ismeretlen'));
});
check('mixed_unknown_cohort_is_not_relabelled_gross', () => {
  const value = context.recordedReturnPresentation({monetary_projection: {records: [
    {status: 'KNOWN', gross_sports_return_eur: 0}, {status: 'UNKNOWN', gross_sports_return_eur: null}]}}, false);
  assert.ok(value.detail.includes('részben vagy teljesen ismeretlen'));
});
check('research_is_simulation_not_real_cash', () => {
  const value = context.recordedReturnPresentation({payout_eur: 100}, true);
  assert.equal(value.title, 'Számított visszatérítés');
  assert.ok(value.detail.includes('nem valódi pénzmozgás'));
});
check('missing_sources_do_not_become_zero_or_known_gross', () => {
  const value = context.recordedReturnPresentation({}, false);
  assert.ok(value.detail.includes('ismeretlen'));
  assert.ok(!value.detail.includes('0 €'));
});
check('normal_renderer_consumes_helper_and_preserves_mode_identity', () => {
  assert.ok(source.includes('const returnRole = recordedReturnPresentation(summary, isResearch)'));
  assert.ok(source.includes('metric(returnRole.title, money(summary.payout_eur), returnRole.detail)'));
  assert.ok(!source.includes('A lezárt szelvények bruttó visszafizetése'));
  assert.ok(source.includes('önmagában nem banki jóváírás vagy költségek utáni rendszerprofit'));
});
console.log(JSON.stringify({status:'PUBLIC_MONEY_CAPTION_6_PASS_NOT_BROWSER_OR_APP_ACCEPTANCE', cases:cases.length, tests:cases, network:0}));
