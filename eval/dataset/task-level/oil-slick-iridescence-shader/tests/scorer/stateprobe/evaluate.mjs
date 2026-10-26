import { actionDuration, evaluateScript, executeAction } from './actions.mjs';
import { StateProbeProtocol } from './probe.mjs';
import { runL0Checks } from './checkers.mjs';
import { assertionCoverage, assertionsOfTransition, zeroAssertionCoverage } from '../evaluator/metrics.mjs';

const CHECK_TIERS = new Set(['L0', 'L1', 'L2']);

export function expressionOf(check) {
  if (typeof check === 'string') return check;
  return check?.expr ?? check?.expression ?? check?.check ?? check?.condition ?? 'false';
}

function verifierDelay(verifier, phase) {
  if (!verifier || typeof verifier !== 'object') return 0;
  const values = phase === 'before'
    ? [verifier.wait_before_ms, verifier.before_wait_ms]
    : [verifier.pre_wait_ms, verifier.wait_ms, verifier.delay_ms, verifier.post_wait_ms,
      verifier.wait_after_ms, verifier.timeout_ms];
  const value = values.find(item => item !== undefined && Number.isFinite(Number(item)));
  return value === undefined ? 0 : actionDuration({ duration_ms: Number(value) }, 0);
}

function verifierPreChecks(verifier) {
  if (!verifier || typeof verifier !== 'object') return [];
  const values = verifier.pre_checks ?? verifier.preChecks ?? verifier.pre_check ?? [];
  return Array.isArray(values) ? values.filter(Boolean) : (values ? [values] : []);
}

function stateConditionsOf(state) {
  const values = [];
  if (state?.condition) values.push(state.condition);
  if (Array.isArray(state?.conditions)) values.push(...state.conditions);
  if (Array.isArray(state?.checks)) values.push(...state.checks);
  return values.filter(Boolean);
}

async function evalCheck(probe, check, before = null, after = null) {
  const expression = expressionOf(check);
  const result = await probe.evalCheck(expression, before, after);
  const tier = CHECK_TIERS.has(check?.tier ?? check?.level) ? (check.tier ?? check.level) : 'L2';
  return { expression, tier, passed: result.passed === true, detail: result.detail };
}

async function runSetup(page, setup, actionOptions) {
  if (setup === undefined || setup === null) return;
  if (Array.isArray(setup)) {
    if (!setup.length) throw new Error('Verifier setup action list is empty.');
    for (const item of setup) await runSetup(page, item, actionOptions);
    return;
  }
  if (typeof setup === 'object') {
    await executeAction(page, setup, actionOptions);
    return;
  }
  if (typeof setup !== 'string' || !setup.trim()) {
    throw new Error('Verifier setup script is empty.');
  }
  await evaluateScript(page, setup);
}

async function evaluateAffordance(page, probe, affordance) {
  const configured = affordance?.checks
    ?? (affordance?.check !== undefined ? [affordance.check]
      : (affordance?.condition !== undefined ? [affordance.condition] : []));
  const checks = Array.isArray(configured) ? configured : [configured];
  if (checks.length) {
    for (const check of checks) if (!(await evalCheck(probe, check)).passed) return false;
    return true;
  }
  if (affordance?.type !== 'dom_element') return false;
  for (const hint of affordance.locator_hints || []) {
    try {
      const selectorCount = await page.locator(`#${hint}, .${hint}, [data-${hint}]`).count();
      const textCount = await page.getByText(hint, { exact: false }).count();
      if (selectorCount > 0 || textCount > 0) return true;
    } catch {
      // A malformed hint is not a successful affordance match. Other hints may still be valid.
    }
  }
  return false;
}

async function stateReached(probe, state) {
  const conditions = stateConditionsOf(state);
  if (!conditions.length) return false;
  for (const condition of conditions) if (!(await evalCheck(probe, condition)).passed) return false;
  return true;
}

function failedChecks(transition, error) {
  return assertionsOfTransition(transition).map(check => ({
    expression: expressionOf(check),
    tier: CHECK_TIERS.has(check?.tier ?? check?.level) ? (check.tier ?? check.level) : 'L2',
    passed: false,
    detail: `Execution error: ${error.message}`,
  }));
}

function percent(numerator, denominator) {
  return denominator > 0 ? +(numerator / denominator * 100).toFixed(1) : 0;
}

function stateIdsOf(rubric) {
  const ids = new Set();
  const add = value => {
    if (typeof value === 'string' && value && value !== 'S_stable_60s') ids.add(value);
  };
  for (const state of rubric?.states || []) add(state?.id);
  for (const transition of rubric?.transitions || []) {
    add(transition?.from);
    add(transition?.to);
  }
  return ids;
}

export function zeroEvaluation(rubric, mode = 'CHECK_FAIL', diagnostic = null) {
  const affordances = rubric?.affordances || [];
  const stateIds = stateIdsOf(rubric);
  const transitions = rubric?.transitions || [];
  const verification = zeroAssertionCoverage(rubric);
  const coverage = {
    aCov: { found: 0, total: affordances.length, percent: 0 },
    sCov: { reached: 0, total: stateIds.size, percent: 0 },
    tCov: { passed: 0, total: transitions.length, percent: 0 },
    vCov: { passed: 0, total: verification.total, percent: 0, policy: 'unweighted_assertion_ratio' },
  };
  return {
    mode,
    diagnostic,
    coverage,
    aCov: 0,
    sCov: 0,
    tCov: 0,
    vCov: 0,
    vCovStats: { passed: 0, total: verification.total, policy: 'unweighted_assertion_ratio' },
    passed: 0,
    total: transitions.length,
    affordances: [],
    transitions: [],
    checks: [],
    affordanceStats: { pass: 0, total: affordances.length, results: [] },
    stateStats: { reached: 0, total: stateIds.size },
    tierStats: { L0: { pass: 0, total: 0 }, L1: { pass: 0, total: 0 }, L2: { pass: 0, total: verification.total } },
  };
}

export function summarizeEvaluation(rubric, affordances, transitions, reachedStates) {
  const affordanceFound = affordances.filter(item => item.found).length;
  const transitionPassed = transitions.filter(item => item.status === 'PASS').length;
  const checks = transitions.flatMap(item => item.checks || []);
  const verification = assertionCoverage(checks);
  const stateIds = stateIdsOf(rubric);
  const reached = [...reachedStates].filter(stateId => stateIds.has(stateId)).length;
  const coverage = {
    aCov: { found: affordanceFound, total: affordances.length, percent: percent(affordanceFound, affordances.length) },
    sCov: { reached, total: stateIds.size, percent: percent(reached, stateIds.size) },
    tCov: { passed: transitionPassed, total: transitions.length, percent: percent(transitionPassed, transitions.length) },
    vCov: { passed: verification.passed, total: verification.total, percent: +verification.percent.toFixed(1), policy: 'unweighted_assertion_ratio' },
  };
  const allTransitionsPassed = transitions.length > 0 && transitionPassed === transitions.length;
  const allAssertionsPassed = verification.total > 0 && verification.passed === verification.total;
  return {
    coverage,
    aCov: coverage.aCov.percent,
    sCov: coverage.sCov.percent,
    tCov: coverage.tCov.percent,
    vCov: coverage.vCov.percent,
    vCovStats: { passed: verification.passed, total: verification.total, policy: 'unweighted_assertion_ratio' },
    passed: transitionPassed,
    total: transitions.length,
    mode: allTransitionsPassed && allAssertionsPassed ? 'CHECK_PASS' : 'CHECK_FAIL',
  };
}

export async function evaluateRubric(page, rubric, options = {}) {
  const probe = options.probe || new StateProbeProtocol(page);
  const actionOptions = { defaultScriptWaitMs: 500, ...options.actionOptions };
  const affordances = [];
  for (const affordance of rubric.affordances || []) {
    affordances.push({
      id: affordance.id,
      name: affordance.name,
      found: await evaluateAffordance(page, probe, affordance),
    });
  }

  const states = (rubric.states || []).filter(state => state.id !== 'S_stable_60s');
  const stateIds = stateIdsOf(rubric);
  const reachedStates = new Set(stateIds.has('S0') ? ['S0'] : []);
  const sampleStates = async () => {
    for (const state of states) {
      if (!reachedStates.has(state.id) && await stateReached(probe, state)) reachedStates.add(state.id);
    }
  };
  await sampleStates();

  const transitions = [];
  for (const transition of rubric.transitions || []) {
    const row = {
      id: transition.id,
      severity: transition.severity,
      status: 'BLOCKED',
      checks: [],
      pre_checks: [],
    };
    try {
      if (transition.pre_action !== undefined) await executeAction(page, transition.pre_action, actionOptions);
      if (transition.pre_actions !== undefined) await executeAction(page, transition.pre_actions, actionOptions);
      const verifier = transition.verifier || transition.verification || transition.verify || {};
      if (verifier.pre_action !== undefined) await executeAction(page, verifier.pre_action, actionOptions);
      if (verifier.pre_actions !== undefined) await executeAction(page, verifier.pre_actions, actionOptions);
      await runSetup(page, verifier.setup_script ?? verifier.setup, actionOptions);
      const beforeWait = verifierDelay(verifier, 'before');
      if (beforeWait) await page.waitForTimeout(beforeWait);
      const before = await probe.snapshot();
      for (const check of verifierPreChecks(verifier)) {
        row.pre_checks.push(await evalCheck(probe, check, before, before));
      }
      await executeAction(page, transition.action, actionOptions);
      if (transition.post_actions !== undefined) await executeAction(page, transition.post_actions, actionOptions);
      if (verifier.post_actions !== undefined) await executeAction(page, verifier.post_actions, actionOptions);
      const afterWait = verifierDelay(verifier, 'after');
      if (afterWait) await page.waitForTimeout(afterWait);
      const after = await probe.snapshot();
      for (const check of assertionsOfTransition(transition)) {
        row.checks.push(await evalCheck(probe, check, before, after));
      }
      const checksPassed = row.checks.length > 0 && row.checks.every(check => check.passed);
      const preChecksPassed = row.pre_checks.every(check => check.passed);
      row.status = row.checks.length === 0 ? 'NO_VERIFIER' : (checksPassed && preChecksPassed ? 'PASS' : 'FAIL');
      if (row.status === 'PASS' && transition.to) reachedStates.add(transition.to);
  } catch (error) {
    row.status = 'ERROR';
    row.error = error.message;
    row.checks = failedChecks(transition, error);
  }
    transitions.push(row);
    await sampleStates();
  }

  const tierStats = { L0: { pass: 0, total: 0 }, L1: { pass: 0, total: 0 }, L2: { pass: 0, total: 0 } };
  for (const check of transitions.flatMap(row => row.checks || [])) {
    const tier = CHECK_TIERS.has(check.tier) ? check.tier : 'L2';
    tierStats[tier].total++;
    if (check.passed) tierStats[tier].pass++;
  }
  const summary = summarizeEvaluation(rubric, affordances, transitions, reachedStates);
  return {
    ...summary,
    affordances,
    transitions,
    checks: transitions,
    affordanceStats: {
      pass: summary.coverage.aCov.found,
      total: summary.coverage.aCov.total,
      results: affordances.map(item => ({ id: item.id, pass: item.found })),
    },
    stateStats: { reached: summary.coverage.sCov.reached, total: summary.coverage.sCov.total },
    tierStats,
  };
}

export async function evaluatePublicPage(page, rubric, options = {}) {
  const l0Checks = await runL0Checks(page);
  const probe = options.probe || new StateProbeProtocol(page);
  const probeStatus = await probe.detect();
  if (!probeStatus.available) {
    const mode = probeStatus.failureMode === 'RUNTIME_CRASH' ? 'RUNTIME_CRASH' : 'CHECK_FAIL';
    return { ...zeroEvaluation(rubric, mode, probeStatus.failureMode), l0Checks, l0_checks: l0Checks };
  }
  return { ...await evaluateRubric(page, rubric, { ...options, probe }), l0Checks, l0_checks: l0Checks };
}
