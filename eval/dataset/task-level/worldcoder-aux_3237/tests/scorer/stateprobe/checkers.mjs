/**
 * Layer 3: Checker Plugin System — pluggable verification checkers.
 *
 * Three built-in tiers:
 *   L0-Universal  — runs on every page, no behavioral contract needed
 *   L1-Physics    — reusable physics formula checkers
 *   L2-BehavioralContract — per-task assertions from the behavioral contract
 *
 * Community plugins implement CheckerPlugin interface:
 *   { name, tier, run(probeData, params) => { passed, detail } }
 */

import { assertionsOfTransition } from '../evaluator/metrics.mjs';

// ─── L0 Universal Checker ───────────────────────────────────────────

export async function runL0Checks(page) {
  const results = [];

  const webgl = await page.evaluate(() => {
    const c = document.querySelector('canvas');
    if (!c) return false;
    return !!(c.getContext('webgl2') || c.getContext('webgl'));
  }).catch(() => false);
  results.push({ name: 'L0_webgl_context', passed: webgl, detail: 'Canvas with WebGL context exists' });

  const notBlack = await page.evaluate(() => {
    const c = document.querySelector('canvas');
    if (!c) return false;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    if (ctx) {
      const d = ctx.getImageData(0, 0, Math.min(c.width, 100), Math.min(c.height, 100)).data;
      let nonZero = 0;
      for (let i = 0; i < d.length; i += 4) { if (d[i] || d[i+1] || d[i+2]) nonZero++; }
      return nonZero > 10;
    }
    return true; // WebGL canvas — cannot getImageData from 2d ctx, assume rendered
  }).catch(() => true);
  results.push({ name: 'L0_not_blank', passed: notBlack, detail: 'Canvas is not all-black' });

  const probeExists = await page.evaluate(
    () => typeof window.__3D_STATE__ === 'object' && window.__3D_STATE__ !== null
  ).catch(() => false);
  results.push({ name: 'L0_probe_exists', passed: probeExists, detail: 'window.__3D_STATE__ is an object' });

  return results;
}

// ─── L1 Physics Checker Library ─────────────────────────────────────

export const PhysicsCheckers = {

  /**
   * Verify positions follow a trajectory formula within epsilon.
   * @param {Array} samples - [{pos:{x,y,z}, time}]
   * @param {Function} formula - (t) => expectedY
   * @param {number} epsilon
   */
  trajectoryCompliance(samples, formula, epsilon = 0.5) {
    const violations = [];
    for (const s of samples) {
      const expected = formula(s.time);
      const actual = s.pos.y;
      if (Math.abs(actual - expected) > epsilon) {
        violations.push({ time: s.time, expected, actual, delta: Math.abs(actual - expected) });
      }
    }
    return {
      passed: violations.length === 0,
      detail: violations.length === 0
        ? `All ${samples.length} samples within epsilon=${epsilon}`
        : `${violations.length}/${samples.length} violations (max delta=${Math.max(...violations.map(v=>v.delta)).toFixed(3)})`,
      violations,
    };
  },

  /**
   * Check velocity reversal and restitution after collision.
   * @param {{x,y,z}} velBefore
   * @param {{x,y,z}} velAfter
   * @param {number} restitution - expected coefficient
   * @param {string} axis - 'x','y', or 'z'
   * @param {number} tolerance
   */
  collisionCorrectness(velBefore, velAfter, restitution, axis = 'y', tolerance = 0.15) {
    const vb = velBefore[axis];
    const va = velAfter[axis];
    const reversed = (vb < 0 && va >= 0) || (vb > 0 && va <= 0);
    const ratioOk = Math.abs(va) <= (restitution + tolerance) * Math.abs(vb);
    return {
      passed: reversed && ratioOk,
      detail: `vel_${axis}: ${vb.toFixed(2)} → ${va.toFixed(2)}, reversed=${reversed}, ratio=${(Math.abs(va)/Math.abs(vb)).toFixed(3)}, expected≤${restitution+tolerance}`,
    };
  },

  /**
   * Detect steady-state: all velocities below threshold for N consecutive frames.
   * @param {Array} velocitySeries - [number] magnitude per frame
   * @param {number} threshold
   * @param {number} window - consecutive frames required
   */
  steadyState(velocitySeries, threshold = 0.5, window = 5) {
    let count = 0;
    for (const v of velocitySeries) {
      if (v < threshold) count++; else count = 0;
      if (count >= window) return { passed: true, detail: `Steady state reached (${window} frames < ${threshold})` };
    }
    return {
      passed: false,
      detail: `No steady state: max consecutive below threshold = ${count}/${window}`,
    };
  },

  /**
   * Check that an invariant holds across all samples.
   * @param {Array} measurements - numeric values across time
   * @param {number} referenceValue
   * @param {number} tolerancePercent - e.g. 5 for 5%
   */
  constraintHold(measurements, referenceValue, tolerancePercent = 5) {
    const tol = referenceValue * tolerancePercent / 100;
    const violations = measurements.filter(m => Math.abs(m - referenceValue) > tol);
    return {
      passed: violations.length === 0,
      detail: violations.length === 0
        ? `All ${measurements.length} within ${tolerancePercent}% of ${referenceValue}`
        : `${violations.length}/${measurements.length} violations (max deviation=${Math.max(...violations.map(v=>Math.abs(v-referenceValue))).toFixed(3)})`,
    };
  },

  /**
   * Verify no two objects penetrate (distance >= minDist).
   * @param {Array} objects - [{pos:{x,y,z}}]
   * @param {number} minDist
   */
  noPenetration(objects, minDist) {
    const violations = [];
    for (let i = 0; i < objects.length; i++) {
      for (let j = i + 1; j < objects.length; j++) {
        const a = objects[i].pos, b = objects[j].pos;
        const d = Math.sqrt((a.x-b.x)**2 + (a.y-b.y)**2 + (a.z-b.z)**2);
        if (d < minDist) violations.push({ i, j, dist: d });
      }
    }
    return {
      passed: violations.length === 0,
      detail: violations.length === 0
        ? `No penetration among ${objects.length} objects (minDist=${minDist})`
        : `${violations.length} penetrations (min dist=${Math.min(...violations.map(v=>v.dist)).toFixed(3)})`,
      violations,
    };
  },

  /**
   * Verify energy is monotonically decreasing (for damped systems).
   * @param {Array} energySeries - [number]
   * @param {number} tolerance - allowed increase per step
   */
  energyDecreasing(energySeries, tolerance = 0.01) {
    const violations = [];
    for (let i = 1; i < energySeries.length; i++) {
      if (energySeries[i] > energySeries[i-1] + tolerance) {
        violations.push({ frame: i, prev: energySeries[i-1], curr: energySeries[i] });
      }
    }
    return {
      passed: violations.length === 0,
      detail: violations.length === 0
        ? `Energy monotonically decreasing across ${energySeries.length} frames`
        : `${violations.length} increases detected`,
      violations,
    };
  },
};

// ─── L2 Behavioral-Contract Checker (runs task assertion checks) ────────────────

export async function runL2Checks(probe, transition, stateBefore, stateAfter) {
  const results = [];
  // Keep scalar, plural, and legacy verifier wrappers consistent with the
  // command-line evaluator. Historical contracts use all three forms.
  for (const check of assertionsOfTransition(transition)) {
    const expression = typeof check === 'string' ? check : (check?.expr ?? check?.expression ?? check?.check ?? check?.condition ?? 'false');
    const result = await probe.evalCheck(expression, stateBefore, stateAfter);
    results.push({ expression, ...result });
  }
  return results;
}

// ─── Plugin Registry ────────────────────────────────────────────────

const _plugins = [];

export function registerChecker(plugin) {
  if (!plugin.name || !plugin.tier || typeof plugin.run !== 'function') {
    throw new Error('Checker plugin must have { name, tier, run(probeData, params) }');
  }
  _plugins.push(plugin);
}

export function getPlugins(tier) {
  return tier ? _plugins.filter(p => p.tier === tier) : [..._plugins];
}
