// Checks one explain-diff attestation against the pull request it claims to cover.
// Pure function, no I/O: the workflow (explain-diff-gate.yml) gathers the facts from GitHub and passes them in.
// Tested by validate.test.js (`node --test docs/gate/validate.test.js`).
'use strict';

const SHA = /^[0-9a-f]{40}$/;

/**
 * @param {object} a    the attestation JSON the reviewer posted
 * @param {object} pr   facts from GitHub, never from the attestation:
 *   headSha, baseRef ("main"), mergeBase, governanceFiles (paths in docs/governance/ at the base),
 *   policy ([{path, sha256}] of REVIEW.md and the governance files at the base), minQuiz (default 3),
 *   requireProvenance (default true)
 * @returns {{ok: boolean, reasons: string[]}}
 */
function validate(a, pr) {
  const reasons = [];
  const fail = (r) => reasons.push(r);
  if (!a || typeof a !== 'object' || Array.isArray(a)) return { ok: false, reasons: ['not a JSON object'] };
  if (a.source !== 'explain-diff') fail('source is not explain-diff');
  if (!SHA.test(String(pr.headSha || ''))) fail('the PR head sha is unknown');
  else if (a.commit !== pr.headSha) fail(`commit ${String(a.commit).slice(0, 12)} is not the PR head ${pr.headSha.slice(0, 12)}`);
  const base = String(a.base || '');
  if (base !== pr.baseRef && !base.endsWith('/' + pr.baseRef)) fail(`base ${base} is not the PR base ${pr.baseRef}`);
  if (!pr.mergeBase || a.mergeBase !== pr.mergeBase) fail('mergeBase is not the merge-base of the PR');
  if (a.uncommitted !== false) fail('the page explained uncommitted changes');

  const q = a.quiz || {};
  const minQuiz = pr.minQuiz ?? 3;
  if (!Number.isInteger(q.of) || q.of < minQuiz) fail(`the quiz had ${q.of} questions; at least ${minQuiz} are required`);
  else if (q.correct !== q.of) fail(`the quiz score is ${q.correct} of ${q.of}`);

  const marks = Array.isArray(a.governance) ? a.governance : [];
  if (a.governance !== undefined && !Array.isArray(a.governance)) fail('governance is not a list');
  for (const g of marks) if (!g || (g.mark !== 'met' && g.mark !== 'na')) fail(`control ${g && g.id} is marked ${g && g.mark}`);
  const docs = new Set(Array.isArray(a.governanceDocuments) ? a.governanceDocuments : []);
  const missingDocs = (pr.governanceFiles || []).filter((f) => !docs.has(f));
  if (missingDocs.length) fail(`the review did not read ${missingDocs.join(', ')}`);

  const prov = a.provenance;
  if (!prov || typeof prov !== 'object') {
    if (pr.requireProvenance !== false) fail('no provenance (explain-diff 1.5.0 or later records it)');
  } else {
    const seen = new Map((Array.isArray(prov.policy) ? prov.policy : []).map((p) => [p && p.path, p && p.sha256]));
    for (const p of pr.policy || []) {
      if (!seen.has(p.path)) fail(`the review did not use ${p.path}`);
      else if (seen.get(p.path) !== p.sha256) fail(`${p.path} differs from the base version the review should have used`);
    }
    if (!/^[0-9a-f]{64}$/.test(String(prov.pageSha256 || ''))) fail('provenance has no page hash');
  }
  return { ok: reasons.length === 0, reasons };
}

/** The attestation in a PR comment: a body starting `explain-diff attestation`, then a fenced JSON block. */
function parseComment(body) {
  if (typeof body !== 'string' || !body.startsWith('explain-diff attestation')) return null;
  const m = body.match(/```(?:json)?\n([\s\S]*?)```/);
  if (!m) return null;
  try { return JSON.parse(m[1]); } catch (e) { return null; }
}

module.exports = { validate, parseComment };
