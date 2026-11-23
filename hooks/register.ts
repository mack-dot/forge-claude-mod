import type { Register } from 'claude-code'

// Forge in Claude Code.
//   /forge          the next step of the beginner loop: a read-only scan, your answers, your approval, then the
//                   proof, the fuzz of your shipped code and INVARIANTS.md (automatic), with ONE suggested next step
//   /forge approve  you (not the agent) read and approve the rule; `/forge approve yes` approves it and runs the rest
//                   (/forge approve yes --hardware [--key KEY]: also sign the approval with a security key)
//   /forge check    proves every check in .forge/forge.json with Dafny; verdict in the status line
//   /forge status   prints the last verdicts
//   /forge more     every other command (advanced tools stay out of the day-1 surface)
// Files in the project: .forge/forge.json (checks), .forge/approved.json (your approvals),
// .forge/last-check.json (last verdicts).
// LIMIT: this is a feedback loop, not a security boundary. The guard blocks Claude's
// Edit/Write tools on approved specs, but a shell command can still change them.
// The real checks stay the pull request gate and the sidecar container.

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Api = any

// binding: the implementation file must start with exactly this text (its include and
// `module X refines Spec {` line), so it cannot switch to a weaker spec.
type DiffEntry = { lang: string; adapter: string; watch?: string[] }
type Check = {
  name: string
  spec: string
  files: string[]
  binding?: { file: string; prefix: string }
  build?: { usedBy?: Array<{ file: string }> }
  examples?: string
  decisions?: string
  diff?: DiffEntry | DiffEntry[]
  watchedFields?: string[]
}
type LastDiff = {
  checks: Record<string, { verdict: string; inputs?: number; disagreements?: unknown[]; decisions?: { failing?: unknown[] } }>
}
type Config = { version: 1; checks: Check[]; dafny?: string }
type Verdict = string
type LastCheck = {
  at: string
  verdicts: Record<string, Verdict>
  detail: Record<string, string>
  warnings?: string[]
  // places outside a rule that decide on its watched fields (written by `check` when a check has watchedFields)
  outside?: Record<string, unknown[]>
}

const DIR = '.forge'
const CONFIG = `${DIR}/forge.json`
const APPROVED = `${DIR}/approved.json`
const SIG = `${DIR}/approved.json.sig` // signed approvals: ssh-keygen -Y sign over approved.json
const SIGNERS = `${DIR}/allowed_signers` // the keys that may sign (part of the signed approval)
const LAST = `${DIR}/last-check.json`

let isStale = false
let isDiffStale = false // an edit touched a file that the differential fuzz covers
let isDiffing = false
let isChecking = false

function join(root: string, rel: string) { return rel.startsWith("/") ? rel : `${root}/${rel}` }

async function readJson<T>($: Api, path: string): Promise<T | undefined> {
  try {
    return JSON.parse(await $.fs.read(path)) as T
  } catch {
    return undefined
  }
}

async function writeJson($: Api, root: string, rel: string, value: unknown) {
  await $.process.run(['mkdir', '-p', `${root}/${DIR}`])
  await $.fs.write(join(root, rel), JSON.stringify(value, null, 2) + '\n')
}

async function sha256($: Api, path: string): Promise<string | undefined> {
  const r = await $.process.run(['shasum', '-a', '256', path], { timeoutMs: 10_000 })
  return r.exitCode === 0 ? r.stdout.split(/\s+/)[0] : undefined
}

// Finds Dafny the same way CI does, and downloads the pinned release once if none is found. On failure, `error` is
// the checker's one line that says exactly what to do (offline, no pinned download for this machine, ...).
async function findDafny($: Api, root: string): Promise<{ path?: string; error?: string }> {
  const r = await $.process.run(['python3', `${$.plugin.root}/cli/forge_check.py`, 'setup', '--root', root, '--json'], {
    cwd: root,
    timeoutMs: 600_000,
  }).catch(() => ({ exitCode: -1, stdout: '', stderr: 'python3 did not start' }))
  try {
    const out = JSON.parse(r.stdout) as { dafny?: string | null; version?: string | null; error?: string | null }
    if (out.version && out.dafny) return { path: out.dafny }
    return { error: out.error ?? 'Dafny did not run.' }
  } catch {
    const why = (r.stderr || r.stdout || '').trim().split('\n').pop() ?? ''
    return { error: /python3/.test(why) || r.exitCode === 127 || r.exitCode === -1
      ? 'Forge needs Python 3.8 or newer as `python3`. Install it (https://www.python.org/downloads/), then type /forge again.'
      : why.slice(0, 300) || `the checker stopped (exit ${r.exitCode}).` }
  }
}

function summary(last: LastCheck | undefined, total: number): string {
  if (!last) return total ? 'forge: not checked yet · /forge check' : 'forge: not set up · /forge'
  const vs = Object.entries(last.verdicts)
  if (!vs.length) return 'forge: no checks · /forge'
  const ok = vs.filter(([, v]) => v === 'VERIFIED').length
  const bad = vs.find(([, v]) => v !== 'VERIFIED')
  if (!bad) return vs.length === 1 ? `forge ✓ ${vs[0]?.[0] ?? ""}: model PROVED` : `forge ✓ ${ok}/${vs.length} models PROVED`
  return `forge ✗ ${bad[1]} ${bad[0]} · ${ok}/${vs.length} verified`
}

// What a green check does NOT cover, said in the same line: the fuzz (a finite test of the code people run),
// code outside the rule that decides the same question, and decision notes that may be stale.
function scopeText(last: LastCheck | undefined, diff: LastDiff | undefined, cfg: Config | undefined): string {
  if (!last || !Object.values(last.verdicts).every(v => v === 'VERIFIED')) return ''
  let t = ''
  const fuzzed = cfg?.checks.some(c => c.diff) ?? false
  if (!fuzzed && !(diff && Object.keys(diff.checks).length)) t += ' · app code NOT fuzzed'
  // Bypasses: shown (also as 0) once a check names the fields it governs; unknown otherwise, so say nothing.
  const watched = cfg?.checks.some(c => (c as { watchedFields?: unknown[] }).watchedFields?.length) ?? false
  const outside = Object.values(last.outside ?? {}).reduce((n, xs) => n + (xs?.length ?? 0), 0)
  if (watched || outside) t += outside ? ` · ${outside} bypass${outside === 1 ? '' : 'es'}, /forge:bypass` : ' · 0 bypasses'
  const warnings = last.warnings ?? []
  const stale = warnings.filter(w => w.startsWith('DECISION_NOTE_STALE')).length
  const broken = warnings.filter(w => w.startsWith('APP_SYNTAX_ERROR')).length
  if (broken) t += ` · ✗ ${broken} app file${broken === 1 ? '' : 's'} do not parse`
  if (stale) t += ` · ${stale} decision record${stale === 1 ? '' : 's'} may be stale`
  else if (cfg?.checks.some(c => (c as { decisions?: string }).decisions)) t += ' · records current'
  return t
}

let lastStatus: string | undefined // the line last shown, so /forge can print it as the result
async function refreshStatus($: Api) {
  const root: string = await $.session.root()
  const cfg = await readJson<Config>($, join(root, CONFIG))
  const last = await readJson<LastCheck>($, join(root, LAST))
  let text = summary(last, cfg?.checks.length ?? 0)
  if (cfg && isStale) text = `forge: code changed · ${text.replace(/^forge:? ?/, '')} · /forge check`
  if (isChecking) text = 'forge: checking…'
  const diff = await readJson<LastDiff>($, join(root, '.forge/last-diff.json'))
  if (isDiffing) text += ' · fuzzing…'
  else if (diff && Object.keys(diff.checks).length) {
    const rs = Object.values(diff.checks)
    // A failing recorded answer is a disagreement too (CI counts both the same way: cli/forge_ci.py).
    const bad = rs.reduce((n, r) => n + (r.disagreements?.length ?? 0) + (r.decisions?.failing?.length ?? 0), 0)
    const inputs = rs.reduce((n, r) => n + (r.inputs ?? 0), 0)
    const broken = rs.find(r => !['DIFF_OK', 'DISAGREE'].includes(r.verdict))
    text += broken
      ? ` · fuzz ${broken.verdict}`
      : bad
        ? ` · fuzz ✗ ${bad} disagreement(s), /forge diff`
        : ` · fuzzed ${inputs.toLocaleString('en-US')} cases, wrapper NOT proved`
  }
  if (!isChecking && !isDiffing) text += scopeText(last, diff, cfg)
  lastStatus = cfg || last ? text : undefined
  $.ui.status(lastStatus)
}

// The verdicts come from cli/forge_check.py, the same checker CI runs.
async function runChecks($: Api): Promise<string> {
  const root: string = await $.session.root()
  isChecking = true
  await refreshStatus($)
  let r
  try {
    r = await $.process.run(
      ['python3', `${$.plugin.root}/cli/forge_check.py`, 'check', '--root', root, '--json'],
      { cwd: root, timeoutMs: 600_000 },
    )
  } finally {
    isChecking = false
  }
  let last: (LastCheck & { error?: string }) | undefined
  try {
    last = JSON.parse(r.stdout)
  } catch {
    last = undefined
  }
  await refreshStatus($)
  if (!last) return `The checker did not run (exit ${r.exitCode}). It needs python3.\n${(r.stderr || r.stdout).slice(0, 600)}`
  if (last.error) return `forge: ${last.error}`
  isStale = false
  await refreshStatus($)
  const names = Object.keys(last.verdicts)
  const help = (last as { help?: Record<string, string> }).help ?? {}
  const lines = names.map(n => `${last.verdicts[n] === 'VERIFIED' ? '✓' : '✗'} ${n}: ${last.verdicts[n]}${help[n] ? `\n  ${help[n]}` : ''}`)
  const fails = names.filter(n => last.verdicts[n] !== 'VERIFIED').map(n => `\n--- ${n} ---\n${last.detail[n]}`)
  return [summary(last, names.length), ...lines, ...fails].join('\n')
}

// Compiles the proved code to the targets in forge.json, fuzzes the compiled code in every
// target, and checks that the app imports it. Same checker as CI.
async function runBuild($: Api, isCheckOnly: boolean): Promise<string> {
  const root: string = await $.session.root()
  const argv = ['python3', `${$.plugin.root}/cli/forge_check.py`, 'build', '--root', root]
  if (isCheckOnly) argv.push('--check')
  const r = await $.process.run(argv, { cwd: root, timeoutMs: 600_000 })
  await refreshStatus($)
  return (r.stdout || r.stderr).slice(0, 6000) || `The checker did not run (exit ${r.exitCode}). It needs python3.`
}

// What `/forge approve` showed, as hashes; `/forge approve yes` approves exactly these.
// Both steps go through cli/forge_check.py, so the mod and CI agree on what an approval covers
// (forge.json, each spec, and the person's answers and decision record). The shown hashes are kept on disk
// (.forge/pending-approval.json) so a plugin reload or restart between the two steps does not lose them.
const PENDING = `${DIR}/pending-approval.json`
const PENDING_MAX_AGE_MS = 24 * 3600 * 1000

async function showSpecs($: Api, isFull = false): Promise<{ text: string; isOk: boolean; hashes?: Record<string, string> }> {
  const root: string = await $.session.root()
  const args = ['python3', `${$.plugin.root}/cli/forge_check.py`, 'spec', '--root', root, '--json', ...(isFull ? ['--full'] : [])]
  const r = await $.process.run(args, {
    cwd: root,
    timeoutMs: 60_000,
  })
  try {
    const out = JSON.parse(r.stdout) as { text: string; hashes: Record<string, string> }
    return { text: out.text, isOk: true, hashes: out.hashes }
  } catch {
    return { text: (r.stderr || r.stdout || `checker exit ${r.exitCode}`).trim(), isOk: false }
  }
}

async function showForApproval($: Api, lead: string, isFull = false): Promise<string> {
  const root: string = await $.session.root()
  const { text, isOk, hashes } = await showSpecs($, isFull)
  if (!isOk || !hashes) return text
  await writeJson($, root, PENDING, { shownAt: new Date().toISOString(), hashes })
  // Signed approvals (forge.json "require_signed_approval"): the digest lists the files signed with the approval.
  const isSigned = text.includes('=== signed with the approval:')
  const how = isSigned
    ? '/forge approve yes --hardware (signs with FORGE_APPROVAL_KEY or --key KEY; touch your security key when it blinks)'
    : '/forge approve yes'
  return `${lead}\n\n${text}\n\nTo approve exactly this, type: ${how}\nThen Forge proves the rule and tests your shipped code against it, by itself.\n(Questions first? /forge:explain explains every line in plain words. Only you can approve; Claude cannot.)`
}

async function approve($: Api, isConfirmed: boolean, isFull = false, hardware: string[] = []): Promise<string> {
  const root: string = await $.session.root()
  if (!isConfirmed) return showForApproval($, 'You are about to approve this:', isFull)
  const pending = await readJson<{ shownAt: string; hashes: Record<string, string> }>($, join(root, PENDING))
  const isFresh = !!pending && Date.now() - Date.parse(pending.shownAt) < PENDING_MAX_AGE_MS
  if (!pending || !isFresh)
    // "yes" before anything was shown: show it now, approve nothing yet.
    return showForApproval($, 'Nothing was shown for approval yet (or it was shown more than a day ago). This is what you would approve:')
  const r = await $.process.run(
    ['python3', `${$.plugin.root}/cli/forge_check.py`, 'approve', '--root', root, '--hashes', JSON.stringify(pending.hashes), ...hardware],
    { cwd: root, timeoutMs: hardware.length ? 180_000 : 60_000 }, // a person may need time to touch the key
  )
  if (r.exitCode === 0) await $.process.run(['rm', '-f', join(root, PENDING)], { timeoutMs: 5_000 })
  await refreshStatus($)
  if (r.exitCode === 0) {
    // Approval is when rules change: keep INVARIANTS.md (every approved rule in plain words) in step.
    const inv = await $.process.run(['python3', `${$.plugin.root}/cli/forge_check.py`, 'invariants', '--root', root], {
      cwd: root,
      timeoutMs: 60_000,
    })
    const outside = await bypassCount($, root)
    const head = `Approved by you:\n${r.stdout.trim()}\n${inv.exitCode === 0 ? inv.stdout.trim() + '; commit it with the approval' : 'INVARIANTS.md was not updated: ' + (inv.stderr || inv.stdout).trim()}${outside}`
    // The rest of the beginner loop runs by itself: the proof, the build the fuzz needs, the fuzz, one status line.
    return `${head}\n\n${await withFill($, await proveAndFuzz($))}`
  }
  // Something changed since it was shown: show the new version, so the next "yes" approves that.
  return showForApproval($, `${(r.stdout || r.stderr).trim()}\nHere is the current version:`)
}

// After an approval: if a check names its watchedFields, count the places outside the rule that use them.
async function bypassCount($: Api, root: string): Promise<string> {
  const cfg = await readJson<Config>($, join(root, CONFIG))
  if (!cfg?.checks?.some(c => c.watchedFields?.length)) return ''
  const r = await $.process.run(['python3', `${$.plugin.root}/cli/forge_check.py`, 'bypass', '--root', root, '--json'], { cwd: root, timeoutMs: 60_000 })
  try {
    const k = (JSON.parse(r.stdout) as { unreviewed: number }).unreviewed
    return k ? `\nBypass search: ${k} place${k === 1 ? '' : 's'} outside the rule use its watched fields (/forge:bypass)` : '\nBypass search: no unreviewed place outside the rule uses its watched fields'
  } catch {
    return `\nBypass search did not run: ${(r.stderr || r.stdout).trim().slice(0, 300)}`
  }
}

// The steps where Claude talks with the person are Markdown commands in commands/ (/forge:scan,
// /forge:add, /forge:explain, /forge:fix, /forge:build-setup, /forge:doctor). A command.run hook cannot
// start a Claude turn: the host refuses $.prompt.submit there.

// Bare /forge runs whatever comes next, so a beginner needs only this one command (and `/forge approve yes`).
// The day-1 loop has four steps; everything else is under `/forge more`.
async function wizard($: Api): Promise<{ text: string; fill?: string }> {
  const root: string = await $.session.root()
  const cfg = await readJson<Config>($, join(root, CONFIG))
  const dafny = await findDafny($, root)
  const approved = (await readJson<Record<string, string>>($, join(root, APPROVED))) ?? {}

  if (!dafny.path)
    return { text: `Forge needs Dafny, the proof checker, and could not set it up:\n${dafny.error}\nThen type /forge again.` }
  if (!cfg?.checks?.length) {
    const hasCandidates = await $.fs.exists(join(root, '.forge/candidates.md'))
    return hasCandidates
      ? { text: 'Forge · step 2 of 4: pick a rule from the scan by its number (for example 1). Claude says it back to you and asks a few questions with real numbers. Nothing in your app changes.', fill: '/forge:add ' }
      : { text: 'Forge · step 1 of 4: Claude reads your code and lists up to 5 rules worth protecting (it changes nothing).', fill: '/forge:scan ' }
  }
  const unapproved = cfg.checks.filter(c => [c.spec, c.examples, c.decisions].some(f => f && !approved[f]))
  if (unapproved.length || !approved[CONFIG])
    return { text: await showForApproval($, 'Forge · step 3 of 4: read what you are about to approve.') }
  return proveAndFuzz($)
}

// Step 4, after an approval (and on every bare /forge after that): prove the rule, build the compiled rule if the
// fuzz needs it, fuzz the untouched shipped code against it, then one honest status line and at most ONE next step.
async function proveAndFuzz($: Api): Promise<{ text: string; fill?: string }> {
  const root: string = await $.session.root()
  const cfg = await readJson<Config>($, join(root, CONFIG))
  const proof = await runChecks($)
  const last = await readJson<LastCheck>($, join(root, LAST))
  const bad = Object.entries(last?.verdicts ?? {}).filter(([, v]) => v !== 'VERIFIED')
  if (!last || bad.length)
    return { text: `${proof}\n\nNext: Claude explains what failed and proposes a fix (press Enter).`, fill: '/forge:fix ' }
  let fuzz = ''
  if (cfg?.checks.some(c => c.diff)) {
    fuzz = await runDiff($)
    if (/: (BUILD_STALE|NO_BUILD):/.test(fuzz) && cfg.checks.some(c => c.build)) {
      // The fuzz runs against the compiled proved rule: compile it (same checker as CI), then fuzz.
      const built = await runBuild($, false)
      const lines = built.split('\n')
      const at = lines.findIndex(l => /^[✓✗] build /.test(l))
      const buildPart = at >= 0 ? lines.slice(at).join('\n') : built
      fuzz = /^✗ build /m.test(buildPart) ? buildPart : `${buildPart}\n\n${await runDiff($)}`
    }
  }
  await refreshStatus($)
  const status = lastStatus ?? ''
  const next = await nextStep($, cfg, fuzz)
  return { text: `${proof}${fuzz ? '\n\n' + fuzz : ''}\n\nStatus: ${status}\n${next.text}`, fill: next.fill }
}

// At most one suggestion, and only when it is relevant. Optional tools are offered once per session, in order.
const offered = new Set<string>()
async function nextStep($: Api, cfg: Config | undefined, fuzz: string): Promise<{ text: string; fill?: string }> {
  const root: string = await $.session.root()
  const diff = await readJson<LastDiff>($, join(root, '.forge/last-diff.json'))
  const results = Object.values(diff?.checks ?? {})
  if (/^✗ build /m.test(fuzz))
    return { text: 'Next: the compiled rule could not be built (the reason and the fix are above). Then type /forge again.' }
  if (results.some(r => r.verdict === 'DISAGREE'))
    return {
      text: 'Next: your shipped code disagrees with the proved rule (each one is a bug, or a gap in the rule). Press Enter: Claude explains each one. Your app files stay unchanged until you say yes.',
      fill: 'Explain each disagreement in the last Forge fuzz report in plain words, and propose a fix. Do not change any file until I say yes.',
    }
  const broken = results.find(r => !['DIFF_OK', 'DISAGREE'].includes(r.verdict))
  if (broken)
    return broken.verdict === 'NO_BUILD'
      ? { text: 'Next: the fuzz needs the rule compiled for your language. Press Enter: Claude sets it up.', fill: '/forge:build-setup ' }
      : { text: `Next: the fuzz did not run (${broken.verdict}; the reason and the fix are above). Then type /forge again.` }
  if (!cfg?.checks.some(c => c.diff))
    return {
      text: 'Next: test your shipped code against the proved rule on thousands of random inputs. Press Enter: Claude connects them (your app files stay unchanged).',
      fill: '/forge:fuzz ',
    }
  const optional: Array<{ id: string; when: () => Promise<boolean>; text: string; fill: string }> = [
    {
      id: 'airlock',
      when: async () => !(await $.fs.exists(join(root, 'forge.yaml'))),
      text: 'Want to keep secret files away from the agent too? /forge airlock (optional; needs Docker)',
      fill: '/forge:airlock ',
    },
    {
      id: 'bypass',
      when: async () => cfg?.checks.some(c => !c.watchedFields?.length) ?? false,
      text: 'Want Forge to look for other code that decides the same thing without the proved rule? /forge:bypass (optional)',
      fill: '/forge:bypass ',
    },
  ]
  for (const o of optional) {
    if (offered.has(o.id) || !(await o.when())) continue
    offered.add(o.id)
    return { text: o.text, fill: o.fill }
  }
  return { text: 'Done: every rule is proved and your shipped code agrees with it on every input tried. Other tools: /forge more' }
}

// Puts the one next step in the prompt box, so the person only presses Enter. A command.run hook cannot start a
// Claude turn itself (the host refuses $.prompt.submit there).
async function withFill($: Api, step: { text: string; fill?: string }): Promise<string> {
  if (!step.fill) return step.text
  const isFilled = await $.prompt.fill({ text: step.fill }).then(() => true, () => false)
  const isCommand = step.fill.startsWith('/')
  if (isFilled) return isCommand && !/Press Enter/.test(step.text) ? `${step.text}\nPress Enter to run ${step.fill.trim()}` : step.text
  return `${step.text}\nType: ${step.fill.trim()}`
}

// `/forge more`: the basic commands first, then the advanced ones (kept out of the day-1 surface).
const MORE = `Forge · the basics
  /forge                   the next step: scan → your answers → approve → proof and fuzz
  /forge approve           read what you would approve; /forge approve yes approves it
  /forge check             prove the rules again
  /forge status            the last result
  /forge:explain           Claude explains an approved rule in plain words
  /forge:fix               Claude explains a failed check and proposes a fix
  /forge:doctor            Claude finds out why Forge does not run here

More, when you need them
  /forge:add <rule>        protect a rule in your own words, without a scan
  /forge:fuzz              connect a rule to your shipped code, or a deep fuzz run (50,000 inputs)
  /forge diff              a quick fuzz run now (it also runs by itself after an edit)
  /forge build             compile the proved rule for your app (/forge:build-setup sets it up)
  /forge:bypass            find code that decides the same thing without the proved rule
  /forge invariants        write INVARIANTS.md: every approved rule in plain words
  /forge spec              show the approved rules
  /forge mutate            plant small bugs in the proved code: does the proof catch them?
  /forge mutate app        plant small bugs in your shipped code: does the fuzz catch them?
  /forge:airlock           run Claude in a box: secret files hidden, MCP tools capped (needs Docker)
  /forge approve yes --hardware   sign the approval with a security key
Details: README.md and docs/ in the plugin (CI Action: docs/ci.md; airlock: docs/airlock.md).`

// Files whose change should trigger the differential fuzz: the proved code, the files that import it, the
// adapters, and each diff entry's "watch" list (the shipped code under test).
async function fuzzedFiles($: Api): Promise<string[]> {
  const root: string = await $.session.root()
  const cfg = await readJson<Config>($, join(root, CONFIG))
  const diffs = (c: Check): DiffEntry[] => (c.diff ? (Array.isArray(c.diff) ? c.diff : [c.diff]) : [])
  return (cfg?.checks ?? [])
    .filter(c => diffs(c).length)
    .flatMap(c => [...c.files, ...(c.build?.usedBy ?? []).map(u => u.file), ...diffs(c).flatMap(d => [d.adapter, ...(d.watch ?? [])])])
    .map(f => join(root, f))
}

// Modification times seen at the last check, so an edit by any means (Claude's tools, a shell command, the
// person's editor) is noticed at the end of the next turn.
let seenTimes: Record<string, number> | undefined
async function fuzzedFilesChanged($: Api): Promise<boolean> {
  const now: Record<string, number> = {}
  for (const f of await fuzzedFiles($)) now[f] = await $.fs.stat(f).then((st: { mtimeMs: number }) => st.mtimeMs, () => -1)
  const before = seenTimes
  seenTimes = now
  if (!before) return false
  return Object.keys(now).some(f => before[f] !== now[f])
}

// The differential fuzz: the code under test against the compiled proved rule (cli/forge_check.py diff).
async function runDiff($: Api): Promise<string> {
  const root: string = await $.session.root()
  isDiffing = true
  await refreshStatus($)
  let r
  try {
    r = await $.process.run(['python3', `${$.plugin.root}/cli/forge_check.py`, 'diff', '--root', root], { cwd: root, timeoutMs: 120_000 })
  } finally {
    isDiffing = false
  }
  isDiffStale = false
  await refreshStatus($)
  return (r.stdout || r.stderr).trim().slice(0, 6000) || `The checker did not run (exit ${r.exitCode}).`
}

// Guard: Claude's own edit tools may not touch approved specs or the approval file.
async function guard($: Api, path: string | undefined): Promise<string | undefined> {
  if (!path) return undefined
  const root: string = await $.session.root()
  const abs = join(root, path.startsWith(root) ? path.slice(root.length + 1) : path)
  if ([APPROVED, PENDING, SIG, SIGNERS].some(f => abs === join(root, f)))
    return 'forge: only you approve specs and keep the signing keys (type /forge approve).'
  const approved = (await readJson<Record<string, string>>($, join(root, APPROVED))) ?? {}
  for (const spec of Object.keys(approved))
    if (abs === join(root, spec))
      return `forge: ${spec} is an approved spec. Ask the person first; they must re-approve after any change.`
  return undefined
}
async function watch($: Api, path: string | undefined) {
  if (!path) return
  const root: string = await $.session.root()
  const cfg = await readJson<Config>($, join(root, CONFIG))
  const checks = cfg?.checks ?? []
  const files = new Set(checks.flatMap(c => [c.spec, ...c.files]).map(f => join(root, f)))
  const fuzzed = new Set(await fuzzedFiles($))
  const abs = path.startsWith('/') ? path : join(root, path)
  if (files.has(abs)) isStale = true
  if (fuzzed.has(abs)) isDiffStale = true
  if (files.has(abs) || fuzzed.has(abs)) await refreshStatus($)
}


export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'forge',
      description: 'Forge: the next step to prove the rules your code must never break (/forge more lists every command)',
      argumentHint: '[approve | approve yes | check | status | more]',
    })
    const r = await next(e)
    void refreshStatus($).catch(() => undefined)
    void fuzzedFilesChanged($).catch(() => undefined)
    return r
  })

  on('command.run', { command: 'forge' }, async ($, e) => {
    const arg = e.args.trim().split(/\s+/)[0] ?? ''
    if (arg === 'more' || arg === 'help') return { text: MORE }
    if (arg === 'check') return { text: await runChecks($) }
    if (arg === 'build') {
      const root: string = await $.session.root()
      const cfg = await readJson<Config>($, join(root, CONFIG))
      if (cfg?.checks?.length && !cfg.checks.some(c => c.build)) {
        // A command cannot start a Claude turn: put the set-up conversation in the prompt box instead.
        const langs = e.args.trim().split(/\s+/).slice(1).join(' ')
        const line = `/forge:build-setup ${langs}`.trimEnd() + ' '
        const isFilled = await $.prompt.fill({ text: line }).then(() => true, () => false)
        return { text: `Forge · no build is set up yet. ${isFilled ? 'Press Enter to set it up' : 'Type ' + line.trim()} (languages: py, js, go, java, cs, rs).` }
      }
      return { text: await runBuild($, /^build\s+check\b/.test(e.args.trim())) }
    }
    if (arg === 'diff') return { text: await runDiff($) }
    if (arg === 'invariants') {
      const root: string = await $.session.root()
      const r = await $.process.run(['python3', `${$.plugin.root}/cli/forge_check.py`, 'invariants', '--root', root], { cwd: root, timeoutMs: 60_000 })
      return { text: (r.stdout || r.stderr).trim() + (r.exitCode === 0 ? '\nEvery approved rule in plain words, in one file: review it in pull requests.' : '') }
    }
    if (arg === 'mutate') {
      const root: string = await $.session.root()
      // `/forge mutate app`: mutants of the shipped code, scored by the diff (cli/forge_mutate_app.py)
      const app = /^mutate\s+(--)?app\b/.test(e.args.trim()) ? ['--app'] : []
      const r = await $.process.run(['python3', `${$.plugin.root}/cli/forge_check.py`, 'mutate', ...app, '--root', root, '--max', '30'], {
        cwd: root,
        timeoutMs: 600_000,
      })
      return { text: (r.stdout || r.stderr).trim().slice(0, 6000) || `The checker did not run (exit ${r.exitCode}).` }
    }
    if (arg === 'approve') {
      const key = /--key\s+(\S+)/.exec(e.args)?.[1]
      const hardware = /--hardware\b/.test(e.args) ? ['--hardware', ...(key ? ['--key', key] : [])] : []
      return { text: await approve($, /^approve\s+yes\b/.test(e.args.trim()), /--full\b/.test(e.args), hardware) }
    }
    if (arg === 'spec') {
      const { text } = await showSpecs($, /--full\b/.test(e.args))
      return { text: `${text}\n\nFor a plain-words explanation: /forge:explain` }
    }
    if (arg === 'status') {
      const root: string = await $.session.root()
      const last = await readJson<LastCheck>($, join(root, LAST))
      const cfg = await readJson<Config>($, join(root, CONFIG))
      const diff = await readJson<LastDiff>($, join(root, '.forge/last-diff.json'))
      if (!last) return { text: 'No check has run yet. Try /forge.' }
      const extra = [...(last.warnings ?? []).map(w => `! ${w}`)]
      return { text: `${summary(last, 0)}${scopeText(last, diff, cfg)}\nlast run ${last.at}\n${JSON.stringify(last.verdicts, null, 2)}${extra.length ? '\n' + extra.join('\n') : ''}` }
    }
    // Steps where Claude talks with the person are Markdown commands (/forge:scan …). A command.run hook
    // cannot start a Claude turn, so put the right command in the prompt box: the person presses Enter.
    const moved = ['scan', 'add', 'explain', 'fix', 'doctor', 'build-setup', 'fuzz', 'bypass', 'airlock']
    if (moved.includes(arg)) {
      const rest = e.args.trim().slice(arg.length).trim()
      const line = `/forge:${arg}${rest ? ' ' + rest : ' '}`
      const isFilled = await $.prompt.fill({ text: line }).then(
        () => true,
        () => false,
      )
      return { text: isFilled ? `Press Enter to run ${line.trim()}.` : `Type ${line.trim()} (Claude runs it as a conversation).` }
    }
    const step = await wizard($)
    return { text: await withFill($, step) }
  })

  // End of a Claude turn: if an edit touched a file the differential fuzz covers, run it (well under two
  // seconds by default; fresh random inputs each time). The result lands in the status line.
  on('turn.complete', async ($, e, next) => {
    const r = await next(e)
    const isChanged = await fuzzedFilesChanged($).catch(() => false)
    if ((isDiffStale || isChanged) && !isDiffing) await runDiff($).catch(() => undefined)
    return r
  })

  // One registration per tool: the hook API types each tool's input by its literal name.
  on('tool.call', { tool: 'Edit' }, async ($, e, next) => {
    const deny = await guard($, e.file_path)
    if (deny) return { deny }
    const ran = await next(e)
    await watch($, e.file_path)
    return ran
  }).catch(($, e, next) => (next.called ? next(e) : { deny: 'forge: its guard failed.' }))

  on('tool.call', { tool: 'Write' }, async ($, e, next) => {
    const deny = await guard($, e.file_path)
    if (deny) return { deny }
    const ran = await next(e)
    await watch($, e.file_path)
    return ran
  }).catch(($, e, next) => (next.called ? next(e) : { deny: 'forge: its guard failed.' }))
}
