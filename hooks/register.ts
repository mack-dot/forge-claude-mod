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
type Check = { name: string; spec: string; files: string[]; binding?: { file: string; prefix: string } }
type Config = { version: 1; checks: Check[]; dafny?: string }
type Verdict = string
type LastCheck = { at: string; verdicts: Record<string, Verdict>; detail: Record<string, string> }

const DIR = '.forge'
const CONFIG = `${DIR}/forge.json`
const APPROVED = `${DIR}/approved.json`
const LAST = `${DIR}/last-check.json`

let isStale = false
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

async function findDafny($: Api, root: string, configured?: string): Promise<string | undefined> {
  // The "dafny" path in .forge/forge.json, else Dafny on PATH, else FORGE_DAFNY.
  if (configured) {
    const path = join(root, configured)
    if (await $.fs.exists(path)) return path
  }
  const r = await $.process.run(
    ['/bin/sh', '-c', 'command -v dafny || { [ -x "$FORGE_DAFNY" ] && echo "$FORGE_DAFNY"; }'],
    { timeoutMs: 5_000 },
  )
  const path = r.stdout.trim()
  return r.exitCode === 0 && path ? path : undefined
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
      { cwd: root, timeoutMs: 900_000 },
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

// Hashes shown to the person by `/forge approve`; `/forge approve yes` approves only these.
let shown: Record<string, string> = {}

async function contractOf($: Api, path: string): Promise<string> {
  const text: string = await $.fs.read(path)
  const keep = text
    .split('\n')
    .filter(l => /^\s*(\/\/|requires\b|ensures\b|(ghost\s+)?(predicate|function|method)\b|datatype\b|\S.*::)/.test(l) || /^\s+\S/.test(l) && /ensures|requires/.test(l))
    .map(l => l.replace(/\s+$/, ''))
  return (keep.length > 80 ? [...keep.slice(0, 80), `… (${keep.length - 80} more lines in the file)`] : keep).join('\n')
}

async function showSpecs($: Api, onlyPending: boolean): Promise<{ text: string; specs: string[] }> {
  const root: string = await $.session.root()
  const cfg = await readJson<Config>($, join(root, CONFIG))
  if (!cfg?.checks?.length) return { text: 'No checks yet. Run /forge.', specs: [] }
  const approved = (await readJson<Record<string, string>>($, join(root, APPROVED))) ?? {}
  const parts: string[] = []
  const specs: string[] = []
  shown = {}
  const cfgHash = await sha256($, join(root, CONFIG))
  if (!cfgHash) return { text: `Cannot read ${CONFIG}.`, specs: [] }
  shown[CONFIG] = cfgHash
  if (!onlyPending || approved[CONFIG] !== cfgHash)
    parts.push(`=== which code is checked against which spec: ${CONFIG} (${approved[CONFIG] === cfgHash ? 'approved' : approved[CONFIG] ? 'CHANGED since you approved it' : 'not approved yet'}) ===\n${JSON.stringify(cfg, null, 2)}`)
  for (const c of cfg.checks) {
    const h = await sha256($, join(root, c.spec))
    if (!h) return { text: `Cannot read ${c.spec}.`, specs: [] }
    shown[c.spec] = h
    const state = approved[c.spec] === h ? 'approved' : approved[c.spec] ? 'CHANGED since you approved it' : 'not approved yet'
    if (onlyPending && state === 'approved') continue
    specs.push(c.spec)
    parts.push(`=== ${c.name}: ${c.spec} (${state}, sha256 ${h.slice(0, 12)}…) ===\n${await contractOf($, join(root, c.spec))}`)
  }
  return { text: parts.join('\n\n'), specs }
}

async function approve($: Api, isConfirmed: boolean): Promise<string> {
  const root: string = await $.session.root()
  const cfg = await readJson<Config>($, join(root, CONFIG))
  if (!cfg?.checks?.length) return 'Nothing to approve yet. Run /forge.'
  if (!isConfirmed) {
    const { text, specs } = await showSpecs($, false)
    if (specs.length)
      void $.prompt.submit({
        text: `${COACH}\nThe person typed /forge approve and is reading the contract of ${specs.join(', ')}. Explain it clause by clause in plain words, in a short table (clause | guarantee). Then list what the spec does NOT guarantee. Do not approve it yourself. End with: type /forge approve yes if this is what you want.`,
      })
    return `You are about to approve this contract. Claude will explain it in plain words below.\n\n${text}\n\nIf this is what you want: /forge approve yes`
  }
  if (!Object.keys(shown).length) return 'Run /forge approve first to read the contract, then /forge approve yes.'
  const approvedNow: Record<string, string> = {}
  const rows: string[] = []
  const cfgHash = await sha256($, join(root, CONFIG))
  if (shown[CONFIG] !== cfgHash) return `${CONFIG} changed after you read it. Run /forge approve again.`
  approvedNow[CONFIG] = cfgHash as string
  rows.push(`approved ${CONFIG}  sha256 ${(cfgHash as string).slice(0, 12)}…`)
  for (const c of cfg.checks) {
    const h = await sha256($, join(root, c.spec))
    if (!h) return `Cannot read ${c.spec}.`
    if (shown[c.spec] !== h) return `${c.spec} changed after you read it. Run /forge approve again to read the new version.`
    approvedNow[c.spec] = h
    rows.push(`approved ${c.spec}  sha256 ${h.slice(0, 12)}…`)
  }
  shown = {}
  await writeJson($, root, APPROVED, approvedNow)
  await refreshStatus($)
  return rows.join('\n') + '\nNext: /forge check'
}

const COACH = `You are guiding the person through Forge setup, one small step at a time. Use short sentences.
Forge rule: the person approves a short spec once; after that, code may change only if Dafny proves it still meets the spec.
Files: .forge/forge.json = {"version":1,"checks":[{"name":"<name>","spec":"<path to spec .dfy>","files":["<every .dfy file to verify>"]}]}.
Keep the spec (requires/ensures, 10-30 lines) in an abstract module in its own file; the implementation in another file that starts with include "<spec>" and "module X refines SpecModule {". Put that exact start as "binding": {"file": "<impl>", "prefix": "<exact first lines>"} in the check. Never use assume, {:verify false}, {:axiom} or {:extern}.
Never write .forge/approved.json and never run /forge approve yourself: approval is the person's act.`

async function wizard($: Api): Promise<{ text: string; prompt?: string }> {
  const root: string = await $.session.root()
  const cfg = await readJson<Config>($, join(root, CONFIG))
  const dafny = await findDafny($, root, cfg?.dafny)
  const approved = (await readJson<Record<string, string>>($, join(root, APPROVED))) ?? {}
  const last = await readJson<LastCheck>($, join(root, LAST))

  if (!dafny)
    return {
      text: 'Forge · step 1 of 5: install Dafny (the proof checker). Claude will help.',
      prompt: `${COACH}\nStep 1 of 5: Dafny is not on PATH. Explain in two lines what Dafny is. Offer to install it (macOS: brew install dafny), or, if a Dafny binary already exists, putting its path as "dafny" in .forge/forge.json, and ask before running the install. Then tell them to run /forge again.`,
    }
  if (!cfg?.checks?.length)
    return {
      text: 'Forge · step 2 of 5: pick one small, critical function to protect. Claude will help you choose.',
      prompt: `${COACH}\nStep 2 of 5: no .forge/forge.json in ${root}. Look at this project briefly and suggest up to three small, critical functions (money, limits, permissions, parsing of untrusted input). Ask the person to pick one with AskUserQuestion. Then write the spec file, a Dafny implementation, and .forge/forge.json. Show the spec to the person in plain words (what it guarantees, what it does not). Tell them the next step is to type /forge approve, which shows the contract; they approve with /forge approve yes.`,
    }
  const unapproved = cfg.checks.filter(c => !approved[c.spec])
  if (unapproved.length || !approved[CONFIG])
    return {
      text: `Forge · step 3 of 5: read and approve the spec (${unapproved.map(c => c.spec).join(', ')}). When it says what you want, type /forge approve yes.`,
      prompt: `${COACH}\nStep 3 of 5: explain the spec file(s) ${unapproved.map(c => c.spec).join(', ')} in plain words, clause by clause, and name one thing the spec does NOT guarantee. Do not approve it yourself. End by telling the person to type /forge approve yes if they agree.`,
    }
  if (!last || isStale)
    return { text: 'Forge · step 4 of 5: prove it. Running /forge check now…\n\n' + (await runChecks($)) }
  const bad = Object.entries(last.verdicts).filter(([, v]) => v !== 'VERIFIED')
  if (bad.length)
    return {
      text: `Forge · step 4 of 5: ${bad.length} check(s) not verified. Claude will explain.`,
      prompt: `${COACH}\nStep 4 of 5: the last /forge check gave ${JSON.stringify(last.verdicts)}. Details:\n${Object.values(last.detail).join('\n\n')}\nExplain in plain words why it failed. If the implementation is wrong, propose a fix to the implementation (never weaken the spec without the person's say). Then tell them to run /forge check.`,
    }
  return {
    text:
      'Forge · step 5 of 5: done. Every check is VERIFIED.\n' +
      'Now let Claude (or any agent) change that code freely. After each turn that touches a covered file, the status line says "code changed"; run /forge check.\n' +
      'If a spec file changes, the check says SPEC_CHANGED until you approve again.\n' +
      'Add more checks with /forge add.',
  }
}

// Guard: Claude's own edit tools may not touch approved specs or the approval file.
async function guard($: Api, path: string | undefined): Promise<string | undefined> {
  if (!path) return undefined
  const root: string = await $.session.root()
  const abs = join(root, path.startsWith(root) ? path.slice(root.length + 1) : path)
  if (abs === join(root, APPROVED)) return 'forge: only you approve specs (type /forge approve).'
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
  const files = new Set((cfg?.checks ?? []).flatMap(c => [c.spec, ...c.files]).map(f => join(root, f)))
  const abs = path.startsWith('/') ? path : join(root, path)
  if (files.has(abs)) {
    isStale = true
    await refreshStatus($)
  }
}


export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'forge',
      description: 'Forge proof gate: guided setup, then check / approve / status / add',
      argumentHint: '[spec|approve|check|status|add]',
    })
    const r = await next(e)
    void refreshStatus($).catch(() => undefined)
    return r
  })

  on('command.run', { command: 'forge' }, async ($, e) => {
    const arg = e.args.trim().split(/\s+/)[0] ?? ''
    if (arg === 'check') return { text: await runChecks($) }
    if (arg === 'approve') return { text: await approve($, /^approve\s+yes\b/.test(e.args.trim())) }
    if (arg === 'spec') {
      const { text, specs } = await showSpecs($, false)
      if (specs.length)
        void $.prompt.submit({
          text: `${COACH}\nThe person typed /forge spec. Explain the contract of ${specs.join(', ')} clause by clause in plain words, in a short table (clause | guarantee), then what it does NOT guarantee.`,
        })
      return { text }
    }
    if (arg === 'status') {
      const root: string = await $.session.root()
      const last = await readJson<LastCheck>($, join(root, LAST))
      return { text: last ? `${summary(last, 0)}\nlast run ${last.at}\n${JSON.stringify(last.verdicts, null, 2)}` : 'No check has run yet. Try /forge.' }
    }
    if (arg === 'add') {
      void $.prompt.submit({
        text: `${COACH}\nThe person wants to add one more check to .forge/forge.json. Suggest candidates, ask which with AskUserQuestion, write spec + implementation, add the entry, then tell them to run /forge approve to read the contract.`,
      })
      return { text: 'Forge · adding a check. Claude will help you pick the next function.' }
    }
    const step = await wizard($)
    if (step.prompt) void $.prompt.submit({ text: step.prompt })
    return { text: step.text }
  })

  for (const tool of ['Edit', 'Write'] as const) {
    on('tool.call', { tool }, async ($, e, next) => {
      const deny = await guard($, e.file_path)
      if (deny) return { deny }
      const ran = await next(e)
      await watch($, e.file_path)
      return ran
    }).catch(($, e, next) => (next.called ? next(e) : { deny: 'forge: its guard failed.' }))
  }
}
