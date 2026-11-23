import type { Register } from 'claude-code'

// Forge in Claude Code.
//   /forge          guided setup: shows the next step and asks Claude to walk you through it
//   /forge check    proves every check in .forge/forge.json with Dafny; verdict in the status line
//   /forge approve  you (not the agent) approve the current spec files
//   /forge status   prints the last verdicts
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
}
type LastDiff = { checks: Record<string, { verdict: string; inputs?: number; disagreements?: unknown[] }> }
type Config = { version: 1; checks: Check[]; dafny?: string }
type Verdict = string
type LastCheck = { at: string; verdicts: Record<string, Verdict>; detail: Record<string, string> }

const DIR = '.forge'
const CONFIG = `${DIR}/forge.json`
const APPROVED = `${DIR}/approved.json`
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

// Finds Dafny the same way CI does, and downloads the pinned release once if none is found.
async function findDafny($: Api, root: string, _configured?: string): Promise<string | undefined> {
  const r = await $.process.run(['python3', `${$.plugin.root}/cli/forge_check.py`, 'setup', '--root', root, '--json'], {
    cwd: root,
    timeoutMs: 600_000,
  })
  try {
    const out = JSON.parse(r.stdout) as { dafny: string | null; version: string | null }
    return out.version && out.dafny ? out.dafny : undefined
  } catch {
    return undefined
  }
}

function summary(last: LastCheck | undefined, total: number): string {
  if (!last) return total ? 'forge: not checked yet · /forge check' : 'forge: not set up · /forge'
  const vs = Object.entries(last.verdicts)
  if (!vs.length) return 'forge: no checks · /forge'
  const ok = vs.filter(([, v]) => v === 'VERIFIED').length
  const bad = vs.find(([, v]) => v !== 'VERIFIED')
  if (!bad) return `forge ✓ VERIFIED ${ok}/${vs.length}`
  return `forge ✗ ${bad[1]} ${bad[0]} · ${ok}/${vs.length} verified`
}

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
    const bad = rs.reduce((n, r) => n + (r.disagreements?.length ?? 0), 0)
    const inputs = rs.reduce((n, r) => n + (r.inputs ?? 0), 0)
    const broken = rs.find(r => !['DIFF_OK', 'DISAGREE'].includes(r.verdict))
    text += broken
      ? ` · fuzz ${broken.verdict}`
      : bad
        ? ` · fuzz ✗ ${bad} disagreement(s), /forge diff`
        : ` · fuzz ✓ ${inputs.toLocaleString('en-US')} inputs`
  }
  $.ui.status(cfg || last ? text : undefined)
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
  const lines = names.map(n => `${last.verdicts[n] === 'VERIFIED' ? '✓' : '✗'} ${n}: ${last.verdicts[n]}`)
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

async function showSpecs($: Api): Promise<{ text: string; isOk: boolean; hashes?: Record<string, string> }> {
  const root: string = await $.session.root()
  const r = await $.process.run(['python3', `${$.plugin.root}/cli/forge_check.py`, 'spec', '--root', root, '--json'], {
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

async function showForApproval($: Api, lead: string): Promise<string> {
  const root: string = await $.session.root()
  const { text, isOk, hashes } = await showSpecs($)
  if (!isOk || !hashes) return text
  await writeJson($, root, PENDING, { shownAt: new Date().toISOString(), hashes })
  return `${lead}\n\n${text}\n\nFor a plain-words explanation: /forge:explain\nTo approve exactly this: /forge approve yes`
}

async function approve($: Api, isConfirmed: boolean): Promise<string> {
  const root: string = await $.session.root()
  if (!isConfirmed) return showForApproval($, 'You are about to approve this:')
  const pending = await readJson<{ shownAt: string; hashes: Record<string, string> }>($, join(root, PENDING))
  const isFresh = !!pending && Date.now() - Date.parse(pending.shownAt) < PENDING_MAX_AGE_MS
  if (!pending || !isFresh)
    // "yes" before anything was shown: show it now, approve nothing yet.
    return showForApproval($, 'Nothing was shown for approval yet (or it was shown more than a day ago). This is what you would approve:')
  const r = await $.process.run(
    ['python3', `${$.plugin.root}/cli/forge_check.py`, 'approve', '--root', root, '--hashes', JSON.stringify(pending.hashes)],
    { cwd: root, timeoutMs: 60_000 },
  )
  if (r.exitCode === 0) await $.process.run(['rm', '-f', join(root, PENDING)], { timeoutMs: 5_000 })
  await refreshStatus($)
  if (r.exitCode === 0) {
    // Approval is when rules change: keep INVARIANTS.md (every approved rule in plain words) in step.
    const inv = await $.process.run(['python3', `${$.plugin.root}/cli/forge_check.py`, 'invariants', '--root', root], {
      cwd: root,
      timeoutMs: 60_000,
    })
    return `${r.stdout.trim()}\n${inv.exitCode === 0 ? inv.stdout.trim() + ' (commit it with the approval)' : 'INVARIANTS.md was not updated: ' + (inv.stderr || inv.stdout).trim()}\nNext: /forge check`
  }
  // Something changed since it was shown: show the new version, so the next "yes" approves that.
  return showForApproval($, `${(r.stdout || r.stderr).trim()}\nHere is the current version:`)
}

// The steps where Claude talks with the person are Markdown commands in commands/ (/forge:scan,
// /forge:add, /forge:explain, /forge:fix, /forge:build-setup, /forge:doctor). A command.run hook cannot
// start a Claude turn: the host refuses $.prompt.submit there.

// Bare /forge runs whatever comes next, so a beginner needs only this one command.
async function wizard($: Api): Promise<{ text: string; fill?: string }> {
  const root: string = await $.session.root()
  const cfg = await readJson<Config>($, join(root, CONFIG))
  const dafny = await findDafny($, root, cfg?.dafny)
  const approved = (await readJson<Record<string, string>>($, join(root, APPROVED))) ?? {}

  if (!dafny)
    return { text: 'Forge: Dafny (the proof checker) could not be set up automatically.', fill: '/forge:doctor ' }
  if (!cfg?.checks?.length) {
    const hasCandidates = await $.fs.exists(join(root, '.forge/candidates.md'))
    return hasCandidates
      ? { text: 'Forge: pick a rule from your last scan (.forge/candidates.md) by its number. Nothing in your code changes until you approve.', fill: '/forge:add ' }
      : { text: 'Forge: first, a read-only scan of this codebase for the rules worth protecting (nothing is changed).', fill: '/forge:scan ' }
  }
  const unapproved = cfg.checks.filter(c => [c.spec, c.examples, c.decisions].some(f => f && !approved[f]))
  if (unapproved.length || !approved[CONFIG])
    return { text: await showForApproval($, 'Forge: read what you are about to approve (only you can):') }
  // Approved: prove it, then fuzz the untouched shipped code against the proved rule (if set up).
  const proof = await runChecks($)
  const last = await readJson<LastCheck>($, join(root, LAST))
  const bad = Object.entries(last?.verdicts ?? {}).filter(([, v]) => v !== 'VERIFIED')
  if (bad.length) return { text: `${proof}\n\nForge: something is not verified. Claude can explain and propose a fix:`, fill: '/forge:fix ' }
  const hasDiff = cfg.checks.some(c => c.diff)
  const fuzz = hasDiff ? await runDiff($) : ''
  const next = hasDiff
    ? 'Every disagreement above is a bug in your shipped code or a gap in the rule. Your app files are unchanged.\nOptional next steps: switch your app to the proved code (ask Claude: "switch my app to the proved rule"); /forge add for another rule; /forge mutate; /forge invariants.'
    : 'Next: fuzz your shipped code against the proved rule. Claude sets it up without changing your app files:'
  return { text: `${proof}${fuzz ? '\n\n' + fuzz : ''}\n\n${next}`, fill: hasDiff ? undefined : '/forge:fuzz ' }
}

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
  if (abs === join(root, APPROVED) || abs === join(root, PENDING))
    return 'forge: only you approve specs (type /forge approve).'
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
      description: 'Forge proof gate: next step, or check / approve / build / spec / status (conversations: /forge:scan, /forge:add)',
      argumentHint: '[check|approve|approve yes|build|diff|invariants|mutate|spec|status]',
    })
    const r = await next(e)
    void refreshStatus($).catch(() => undefined)
    void fuzzedFilesChanged($).catch(() => undefined)
    return r
  })

  on('command.run', { command: 'forge' }, async ($, e) => {
    const arg = e.args.trim().split(/\s+/)[0] ?? ''
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
      const r = await $.process.run(['python3', `${$.plugin.root}/cli/forge_check.py`, 'mutate', '--root', root, '--max', '30'], {
        cwd: root,
        timeoutMs: 600_000,
      })
      return { text: (r.stdout || r.stderr).trim().slice(0, 6000) || `The checker did not run (exit ${r.exitCode}).` }
    }
    if (arg === 'approve') return { text: await approve($, /^approve\s+yes\b/.test(e.args.trim())) }
    if (arg === 'spec') {
      const { text } = await showSpecs($)
      return { text: `${text}\n\nFor a plain-words explanation: /forge:explain` }
    }
    if (arg === 'status') {
      const root: string = await $.session.root()
      const last = await readJson<LastCheck>($, join(root, LAST))
      return { text: last ? `${summary(last, 0)}\nlast run ${last.at}\n${JSON.stringify(last.verdicts, null, 2)}` : 'No check has run yet. Try /forge.' }
    }
    // Steps where Claude talks with the person are Markdown commands (/forge:scan …). A command.run hook
    // cannot start a Claude turn, so put the right command in the prompt box: the person presses Enter.
    const moved = ['scan', 'add', 'explain', 'fix', 'doctor', 'build-setup', 'fuzz']
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
    if (step.fill) {
      const isFilled = await $.prompt.fill({ text: step.fill }).then(() => true, () => false)
      return { text: `${step.text}\n${isFilled ? 'Press Enter to run ' : 'Type '}${step.fill.trim()}` }
    }
    return { text: step.text }
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
