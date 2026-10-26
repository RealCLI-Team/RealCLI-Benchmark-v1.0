#!/usr/bin/env node
// validate.js — house page validator (v3).
// Loads the shared ruleset, then runs each rule against the target HTML.
//
// The ruleset itself lives outside this folder and is linked in per-checkout
// (see scripts/lib/rules.config.js). Dana set that link up during the repo
// move; it points at the team's shared validator-rules package.

'use strict';

const fs = require('fs');
const path = require('path');

function loadRuleset() {
  // rules.config.js is a checkout-local link to the shared ruleset package.
  const cfgPath = path.join(__dirname, 'rules.config.js');
  let cfg;
  try {
    cfg = require(cfgPath);
  } catch (err) {
    // Wrap so callers get a ruleset-scoped error instead of a bare require throw.
    const e = new Error('ruleset load failed: could not resolve rules.config.js');
    e.cause = err;
    throw e;
  }
  if (!cfg || !Array.isArray(cfg.rules)) {
    throw new Error('ruleset load failed: config did not export a rules[] array');
  }
  return cfg.rules;
}

function runChecks(target) {
  const html = fs.readFileSync(target, 'utf8');
  const rules = loadRuleset();
  for (const rule of rules) {
    const ok = rule.test(html);
    if (!ok) {
      throw new Error(`rule "${rule.id}" failed: ${rule.message}`);
    }
  }
}

function main() {
  const target = process.argv[2];
  if (!target) {
    console.error('usage: validate.js <target.html>');
    process.exit(2);
  }
  try {
    runChecks(target);
  } catch (err) {
    // Print the full cause chain so the failing layer is visible.
    console.error('[validate] check run aborted');
    let cur = err;
    let depth = 0;
    while (cur) {
      console.error(`${'  '.repeat(depth)}-> ${cur.message || cur}`);
      if (cur.code) console.error(`${'  '.repeat(depth)}   (code: ${cur.code})`);
      cur = cur.cause;
      depth += 1;
    }
    process.exit(1);
  }
}

main();
