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
type Check = { name: string; spec: string; files: string[]; binding?: { file: string; prefix: string }; build?: unknown; examples?: string; decisions?: string }
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
// (forge.json, each spec, and the person's answers and decision record).
let shown: string | undefined

async function showSpecs($: Api): Promise<{ text: string; isOk: boolean }> {
  const root: string = await $.session.root()
  const r = await $.process.run(['python3', `${$.plugin.root}/cli/forge_check.py`, 'spec', '--root', root, '--json'], {
    cwd: root,
    timeoutMs: 60_000,
  })
  try {
    const out = JSON.parse(r.stdout) as { text: string; hashes: Record<string, string> }
    shown = JSON.stringify(out.hashes)
    return { text: out.text, isOk: true }
  } catch {
    shown = undefined
    return { text: (r.stderr || r.stdout || `checker exit ${r.exitCode}`).trim(), isOk: false }
  }
}

async function approve($: Api, isConfirmed: boolean): Promise<string> {
  const root: string = await $.session.root()
  if (!isConfirmed) {
    const { text, isOk } = await showSpecs($)
    if (!isOk) return text
    void $.prompt.submit({
      text: `${COACH}\nThe person typed /forge approve and is reading the contract: forge.json, each spec, and, where present, their own answers (examples) and decision record. Explain the spec clause by clause in plain words, in a short table (clause | guarantee). If there is a decision record, check that each answer in it appears in the examples file. Then list what is NOT guaranteed. Do not approve it yourself. End with: type /forge approve yes if this is what you want.`,
    })
    return `You are about to approve this. Claude will explain it in plain words below.\n\n${text}\n\nIf this is what you want: /forge approve yes`
  }
  if (!shown) return 'Run /forge approve first to read everything, then /forge approve yes.'
  const r = await $.process.run(
    ['python3', `${$.plugin.root}/cli/forge_check.py`, 'approve', '--root', root, '--hashes', shown],
    { cwd: root, timeoutMs: 60_000 },
  )
  shown = undefined
  await refreshStatus($)
  return r.exitCode === 0 ? `${r.stdout.trim()}\nNext: /forge check` : (r.stdout || r.stderr).trim()
}

const COACH = `You are guiding the person through Forge setup, one small step at a time. Use short sentences.
Forge rule: the person approves a short spec once; after that, code may change only if Dafny proves it still meets the spec.
Files: .forge/forge.json = {"version":1,"checks":[{"name":"<name>","spec":"<path to spec .dfy>","files":["<every .dfy file to verify>"]}]}.
Keep the spec (requires/ensures, 10-30 lines) in an abstract module in its own file; the implementation in another file that starts with include "<spec>" and "module X refines SpecModule {". Put that exact start as "binding": {"file": "<impl>", "prefix": "<exact first lines>"} in the check. Never use assume, {:verify false}, {:axiom} or {:extern}.
Never write .forge/approved.json and never run /forge approve yourself: approval is the person's act.`

// The /forge add conversation. The rule must start from the person's words, not from Claude's
// reading of the code; every answer becomes a Dafny-checked example and a line in a decision record.
function interview(dafny: string | undefined, root: string): string {
  return `${COACH}
You are running the Forge rule conversation in ${root}. Follow these steps in order. Short sentences.

1. ASK FIRST. Ask the person to say, in their own words, one rule their software must never break.
   Do not suggest a rule and do not read the code before they answer. Only if they say they do not
   know, look at the code and offer up to three candidates (or suggest /forge scan), and let them pick
   or rephrase.
2. CLASSIFY and say it plainly:
   - pure rule (limits, prices, permissions, state changes, parsing): can be PROVED in a Dafny model;
   - rule mixed with I/O (database, network, time): split the rule out; only the rule is proved;
   - taste or judgment (UI, ML output, "good"): cannot be proved; say so, suggest tests or review, stop.
3. READ the code where this rule lives now (if any), so your questions use their real names and units.
4. DEFAULTS. Show a short checklist of fail-closed defaults (money in whole integer units; invalid input
   rejected with no change; time passed in as a value; no floats) and ask them to confirm or change.
5. QUESTIONS. Ask only real choices where both answers are sensible, always with concrete numbers
   ("cap $50.00, spent $40.00, spend $20.00: reject, or cut to $10.00?"). Use AskUserQuestion, at most
   4 questions per call, as many rounds as needed. No fixed number of questions. Cover: zero and
   negative values, exactly at each limit, one past each limit, units, which error wins when two apply,
   repeats and retries, ordering in time. Stop only when nothing is open.
6. WRITE three files and keep them consistent:
   a. spec/<Name>Spec.dfy: an abstract module with requires/ensures that captures every answer.
   b. spec/<Name>Examples.dfy: starts with include "<Name>Spec.dfy"; an abstract module with
      "import S : <Name>Spec"; one method per decision, named D1_..., D2_..., with a comment holding the
      concrete question, that calls S.<Method>(...) on the concrete values and asserts the answer given.
   c. .forge/decisions/<check-name>.md: the rule in their exact words; confirmed defaults; a table
      Id | Question | Answer; and "Out of scope (not guaranteed)".
7. PROBE for gaps the questions missed. For a few other inputs (boundaries, zero, maximum values), write a
   temporary Dafny file OUTSIDE the project (in a temp folder) that includes the spec and asserts one
   outcome, then another file asserting the other outcome. If neither verifies, the spec leaves that case
   open: ask the person as a new question, add it as a new D-method and decision, update the spec.
   Dafny: ${dafny ?? 'not found; ask the person to install it first'}.
8. CHECK before handing over: run dafny verify on the examples file; it must pass (exit 0). If it fails,
   the spec contradicts or leaves open one of their answers: fix the spec, never the answer, unless the
   person changes their answer.
9. IMPLEMENT the rule in impl/<Name>.dfy: starts with include "../spec/<Name>Spec.dfy", a blank line,
   then "module <Name> refines <Name>Spec {". Add the check to .forge/forge.json with "examples",
   "decisions" and "binding" (file and that exact first-lines prefix).
10. HAND OVER: list the decisions in a short table, name what is out of scope, and tell the person to
   type /forge approve to read everything, then /forge approve yes. Never approve for them.`
}

async function wizard($: Api): Promise<{ text: string; prompt?: string }> {
  const root: string = await $.session.root()
  const cfg = await readJson<Config>($, join(root, CONFIG))
  const dafny = await findDafny($, root, cfg?.dafny)
  const approved = (await readJson<Record<string, string>>($, join(root, APPROVED))) ?? {}
  const last = await readJson<LastCheck>($, join(root, LAST))

  if (!dafny)
    return {
      text: 'Forge · step 1 of 5: Dafny (the proof checker) could not be set up automatically. Claude will help.',
      prompt: `${COACH}\nStep 1 of 5: the automatic Dafny download failed (it needs python3 and internet access; or this machine is not macOS arm64/x64, Linux x64 or Windows x64). Run: python3 "${$.plugin.root}/cli/forge_check.py" setup --root "${root}" and read its error. Explain it in two lines. Offer the fix (for example: brew install dafny), ask before installing anything, then tell the person to run /forge again.`,
    }
  if (!cfg?.checks?.length)
    return {
      text: 'Forge · step 2 of 5: tell Claude, in your own words, one rule your software must never break. Claude will ask about the edge cases.',
      prompt: interview(dafny, root),
    }
  const unapproved = cfg.checks.filter(c => [c.spec, c.examples, c.decisions].some(f => f && !approved[f]))
  if (unapproved.length || !approved[CONFIG])
    return {
      text: `Forge · step 3 of 5: type /forge approve to read the spec and your answers (${unapproved.map(c => c.name).join(', ')}); then /forge approve yes if they say what you want.`,
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
      'To use the proved code in your app: /forge build (compiles it to py, js, go, java, cs or rs, fuzzes the compiled code, checks the app imports it).\n' +
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
      argumentHint: '[scan|add|approve|check|build|spec|status]',
    })
    const r = await next(e)
    void refreshStatus($).catch(() => undefined)
    return r
  })

  on('command.run', { command: 'forge' }, async ($, e) => {
    const arg = e.args.trim().split(/\s+/)[0] ?? ''
    if (arg === 'check') return { text: await runChecks($) }
    if (arg === 'build') {
      const root: string = await $.session.root()
      const cfg = await readJson<Config>($, join(root, CONFIG))
      if (cfg?.checks?.length && !cfg.checks.some(c => c.build)) {
        void $.prompt.submit({
          text: `${COACH}\nThe person typed /forge build, but no check in .forge/forge.json has a "build" entry yet. Ask with AskUserQuestion which languages their app needs (py, js, go, java, cs, rs; several allowed). Then: (1) write a fuzz harness in Dafny for the check, a file with a module that has "method Main()", which calls the proved method on boundary values (0, 1, each limit and its neighbours, 2^31, 2^32, 2^53, 2^63, 2^64) and about 2000 pseudo-random inputs from a linear congruential generator, repeats each ensures clause as an "expect", and prints one line per case; the module name must differ from the file name. (2) Add "build": {"targets": [...], "out": "verified", "fuzz": "<harness>", "usedBy": [{"file": "<app file>", "contains": "<import text>"}]} to the check. (3) Write a thin wrapper in the app's language that converts plain values to the generated types, and make the app call it; say plainly that the wrapper is unproved. (4) Tell the person that forge.json changed, so they run /forge approve, then /forge approve yes, then /forge build.`,
        })
        return { text: 'Forge · no build is set up yet. Claude will ask which languages you need.' }
      }
      return { text: await runBuild($, /^build\s+check\b/.test(e.args.trim())) }
    }
    if (arg === 'approve') return { text: await approve($, /^approve\s+yes\b/.test(e.args.trim())) }
    if (arg === 'spec') {
      const { text, isOk } = await showSpecs($)
      shown = undefined
      if (isOk)
        void $.prompt.submit({
          text: `${COACH}\nThe person typed /forge spec. Explain each contract shown above clause by clause in plain words, in a short table (clause | guarantee), then what it does NOT guarantee.`,
        })
      return { text }
    }
    if (arg === 'status') {
      const root: string = await $.session.root()
      const last = await readJson<LastCheck>($, join(root, LAST))
      return { text: last ? `${summary(last, 0)}\nlast run ${last.at}\n${JSON.stringify(last.verdicts, null, 2)}` : 'No check has run yet. Try /forge.' }
    }
    if (arg === 'scan') {
      const root: string = await $.session.root()
      void $.prompt.submit({
        text: `${COACH}
The person typed /forge scan in ${root}. Survey the codebase and recommend what is worth proving. Do NOT write or change any file.
1. Read the layout, then the code that decides money, limits, quotas, permissions and access, state changes (status fields, lifecycles), retries and idempotency, parsing of untrusted input, and scoring or ranking rules.
2. Rank at most 7 candidates in one table: rank | file:line | function | the rule in one plain sentence | what goes wrong if it breaks | kind (pure rule: provable / rule mixed with I/O: split the rule out / judgment: not provable) | effort (small, medium, large).
3. Prefer small pure rules with a high cost of failure. Say plainly if the codebase has none.
4. Name up to 3 things that look important but should NOT be proved, and why (UI, ML output, glue).
5. End with: to start on one, type /forge add and say the rule in your own words; the table is a suggestion, not the rule.`,
      })
      return { text: 'Forge · scanning the codebase for rules worth proving. Nothing will be changed.' }
    }
    if (arg === 'add') {
      const root: string = await $.session.root()
      const cfg = await readJson<Config>($, join(root, CONFIG))
      void $.prompt.submit({ text: interview(await findDafny($, root, cfg?.dafny), root) })
      return { text: 'Forge · new rule. Tell Claude, in your own words, one rule your software must never break.' }
    }
    const step = await wizard($)
    if (step.prompt) void $.prompt.submit({ text: step.prompt })
    return { text: step.text }
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
