'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { validate, parseComment, fromComment } = require('./validate.js');

const H = 'a'.repeat(40), M = 'b'.repeat(40), P = 'c'.repeat(64), R = 'd'.repeat(64);
const PR = { headSha: H, baseRef: 'main', mergeBase: M, governanceFiles: ['docs/governance/GOV.md'],
  policy: [{ path: 'REVIEW.md', sha256: R }, { path: 'docs/governance/GOV.md', sha256: P }] };
const good = () => ({
  source: 'explain-diff', change: 'x', commit: H, base: 'origin/main', baseCommit: 'e'.repeat(40), mergeBase: M,
  uncommitted: false, quiz: { correct: 5, of: 5, attempt: 1 }, governance: [{ id: 'GOV-1', mark: 'met' }, { id: 'GOV-2', mark: 'na' }],
  governanceDocuments: ['docs/governance/GOV.md'],
  provenance: { skillVersion: '1.5.0', pageSha256: 'f'.repeat(64), policy: [{ path: 'REVIEW.md', sha256: R }, { path: 'docs/governance/GOV.md', sha256: P }] },
  at: '2026-11-23T00:00:00Z' });
const reasons = (patch, pr = PR) => { const a = good(); patch(a); return validate(a, pr).reasons.join('; '); };

test('a good attestation passes', () => assert.deepStrictEqual(validate(good(), PR), { ok: true, reasons: [] }));
test('an old head commit fails', () => assert.match(reasons((a) => { a.commit = '1'.repeat(40); }), /not the PR head/));
test('a different base fails', () => assert.match(reasons((a) => { a.base = 'origin/release'; }), /not the PR base/));
test('a wrong merge-base fails', () => assert.match(reasons((a) => { a.mergeBase = '2'.repeat(40); }), /merge-base/));
test('uncommitted changes fail', () => assert.match(reasons((a) => { a.uncommitted = true; }), /uncommitted/));
test('a wrong answer fails', () => assert.match(reasons((a) => { a.quiz.correct = 4; }), /4 of 5/));
test('a short quiz fails', () => assert.match(reasons((a) => { a.quiz = { correct: 1, of: 1 }; }), /at least 3/));
test('a Not met control fails', () => assert.match(reasons((a) => { a.governance[0].mark = 'notmet'; }), /GOV-1 is marked notmet/));
test('skipping a governance document fails', () => assert.match(reasons((a) => { a.governanceDocuments = []; }), /did not read docs\/governance\/GOV.md/));
test('rules from the changed branch fail', () => assert.match(reasons((a) => { a.provenance.policy[0].sha256 = '9'.repeat(64); }), /REVIEW.md differs from the base/));
test('a missing policy file fails', () => assert.match(reasons((a) => { a.provenance.policy = []; }), /did not use REVIEW.md/));
test('no provenance fails unless allowed', () => {
  assert.match(reasons((a) => { delete a.provenance; }), /no provenance/);
  assert.strictEqual(reasons((a) => { delete a.provenance; }, { ...PR, policy: [], requireProvenance: false }), '');
});
test('an unknown head sha fails closed', () => assert.match(reasons(() => {}, { ...PR, headSha: undefined }), /head sha is unknown/));
test('a non-object fails', () => assert.strictEqual(validate([], PR).ok, false));
test('parseComment reads the fenced JSON and ignores other comments', () => {
  assert.deepStrictEqual(parseComment('explain-diff attestation\n```json\n{"source":"explain-diff"}\n```'), { source: 'explain-diff' });
  assert.strictEqual(parseComment('LGTM\n```json\n{}\n```'), null);
  assert.strictEqual(parseComment('explain-diff attestation\n```json\n{bad\n```'), null);
});

const comment = (over = {}) => ({ body: 'explain-diff attestation\n```json\n{"source":"explain-diff"}\n```', user: { login: 'rev' },
  created_at: '2026-11-26T10:00:00Z', updated_at: '2026-11-26T10:00:00Z', ...over });
test('an unedited attestation comment counts for its author', () => {
  assert.deepStrictEqual(fromComment(comment()), { attestation: { source: 'explain-diff' }, login: 'rev' });
});
test('an edited attestation comment does not count', () => {
  const r = fromComment(comment({ updated_at: '2026-11-27T09:00:00Z' }));
  assert.strictEqual(r.attestation, null);
  assert.match(r.why, /rev: the attestation comment was edited/);
});
test('a comment without timestamps counts as edited', () => {
  assert.strictEqual(fromComment(comment({ created_at: undefined, updated_at: undefined })).attestation, null);
});
test('other comments are ignored without a reason', () => {
  assert.deepStrictEqual(fromComment(comment({ body: 'LGTM' })), { attestation: null });
});
