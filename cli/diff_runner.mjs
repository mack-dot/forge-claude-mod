// forge diff runner for JavaScript / TypeScript. Started by `forge_check.py diff`; prints one JSON line.
//
// It loads the check's adapter (written once per check, usually by Claude with /forge:diff-setup). The
// adapter exports:
//   generate(rng)        -> one input (plain JSON value). rng helpers below; rng.weird() gives odd values.
//   reference(input)     -> the output of the compiled PROVED rule for that input (plain JSON value).
//   candidates           -> { name: input => output }: the code under test (the shipped app function,
//                           the wrapper around the proved code, ...).
//   inDomain(input)      -> optional; false skips the input (outside the rule's precondition: compiled
//                           Dafny code does not check `requires`).
// Before the adapter loads, the runner sets globalThis.forge = { loadDafny, require }. Use
// forge.loadDafny('<path to emitted .js>', ['ModuleName']) to get a Dafny module, relative to the adapter.
//
// Every candidate output is compared with the reference output (deep, as JSON). A disagreement is shrunk
// to a small input that still disagrees and saved as a regression case; saved cases run first next time.
// The adapter and this runner are test code: NOT proved.
import { createRequire, Module } from 'node:module'
import { readFileSync, readdirSync, writeFileSync, mkdirSync, existsSync } from 'node:fs'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const [adapterPath, seedArg, maxInputsArg, budgetMsArg, regDir] = process.argv.slice(2)
const started = Date.now()

// Load a Dafny-compiled JavaScript file (a script with no exports) and return the named modules.
function loadDafny(file, names) {
  const abs = path.resolve(path.dirname(adapterPath), file)
  const src = readFileSync(abs, 'utf8')
  const m = new Module(abs)
  m.filename = abs
  m.paths = [...(process.env.NODE_PATH || '').split(path.delimiter).filter(Boolean), ...Module._nodeModulePaths(path.dirname(abs))]
  m._compile(`${src}\nmodule.exports = { _dafny, ${names.join(', ')} };`, abs)
  return m.exports
}
globalThis.forge = { loadDafny, require: createRequire(import.meta.url) }

// Deterministic pseudo-random numbers (mulberry32) with helpers.
function makeRng(seed) {
  let a = seed >>> 0
  const next = () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
  const rng = {
    float: next,
    bool: () => next() < 0.5,
    int: (lo, hi) => lo + Math.floor(next() * (hi - lo + 1)),
    pick: arr => arr[Math.floor(next() * arr.length)],
    // Small numbers most of the time, boundaries sometimes.
    num: (lo, hi) => (next() < 0.25 ? rng.pick([lo, hi, lo + 1, hi - 1, 0, 1, -1].filter(v => v >= lo && v <= hi)) : rng.int(lo, hi)),
    string: (maxLen = 8, alphabet = 'ab c-_.A1é😀') => {
      const n = rng.int(0, maxLen)
      const chars = [...alphabet]
      let s = ''
      for (let i = 0; i < n; i++) s += rng.pick(chars)
      return s
    },
    array: (gen, maxLen = 5) => Array.from({ length: rng.int(0, maxLen) }, () => gen()),
    weird: () => rng.pick(['', ' ', '  x ', 'X', 'x', 'é', 'é', '😀', '\u0000', 'null', 'undefined', '0', '-0', 'NaN', 'a'.repeat(300)]),
  }
  return rng
}

const canon = v => JSON.stringify(v, (k, x) => (x && typeof x === 'object' && !Array.isArray(x) ? Object.fromEntries(Object.keys(x).sort().map(k2 => [k2, x[k2]])) : x))
function call(fn, input) {
  try {
    const out = fn(structuredClone(input))
    return out === undefined ? { __undefined__: true } : out
  } catch (e) {
    return { __throws__: e && e.name ? e.name : String(e) }
  }
}

// Smaller variants of a JSON value, smallest first.
function* shrinks(v) {
  if (Array.isArray(v)) {
    if (v.length) yield []
    for (let i = 0; i < v.length; i++) yield [...v.slice(0, i), ...v.slice(i + 1)]
    for (let i = 0; i < v.length; i++) for (const s of shrinks(v[i])) yield [...v.slice(0, i), s, ...v.slice(i + 1)]
  } else if (typeof v === 'string') {
    if (v.length) yield ''
    if (v.length > 1) yield v.slice(0, Math.floor(v.length / 2))
    for (let i = 0; i < v.length && i < 16; i++) yield v.slice(0, i) + v.slice(i + 1)
  } else if (typeof v === 'number' && Number.isInteger(v) && v !== 0) {
    yield 0
    if (v < 0) yield -v
    // move toward 0 by v/2, v/4, ..., 1
    for (let d = Math.trunc(Math.abs(v) / 2); d >= 1; d = Math.trunc(d / 2)) yield v > 0 ? v - d : v + d
  } else if (typeof v === 'boolean' && v) {
    yield false
  } else if (v && typeof v === 'object') {
    for (const k of Object.keys(v)) for (const s of shrinks(v[k])) yield { ...v, [k]: s }
  }
}

const adapter = await import(pathToFileURL(path.resolve(adapterPath)).href)
const inDomain = adapter.inDomain || (() => true)
const names = Object.keys(adapter.candidates)

function disagrees(name, input) {
  if (!inDomain(input)) return null
  const ref = call(adapter.reference, input)
  const got = call(adapter.candidates[name], input)
  return canon(ref) === canon(got) ? null : { ref, got }
}

// How two outputs differ, without the numbers: which fields differ, and booleans/short strings as values.
// Shrinking keeps this fixed, so a disagreement cannot slide into a different bug while it shrinks.
function signature(a, b) {
  const leaf = x => (x === null || typeof x === 'boolean' || (typeof x === 'string' && x.length <= 20) ? JSON.stringify(x) : typeof x)
  if (a && b && typeof a === 'object' && typeof b === 'object' && !Array.isArray(a) && !Array.isArray(b)) {
    const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])].sort()
    return keys.filter(k => canon(a[k]) !== canon(b[k])).map(k => `${k}:${signature(a[k], b[k])}`).join(',')
  }
  return `${leaf(a)}>${leaf(b)}`
}

function shrink(name, input) {
  const first = disagrees(name, input)
  const sig = signature(first.ref, first.got)
  let best = input
  let steps = 0
  for (let improved = true; improved && steps < 300; ) {
    improved = false
    for (const s of shrinks(best)) {
      if (++steps > 300) break
      const d = disagrees(name, s)
      if (d && signature(d.ref, d.got) === sig) {
        best = s
        improved = true
        break
      }
    }
  }
  return best
}

const result = { inputs: 0, out_of_domain: 0, replayed: 0, disagreements: [], seed: Number(seedArg) }
const seen = new Set()
function record(name, input, isNew) {
  const d = disagrees(name, input)
  if (!d) return false
  const key = name + canon(input)
  if (seen.has(key)) return true
  seen.add(key)
  const file = path.join(regDir, `${name}-${createHash('sha256').update(key).digest('hex').slice(0, 10)}.json`)
  if (isNew && !existsSync(file)) {
    mkdirSync(regDir, { recursive: true })
    writeFileSync(file, JSON.stringify({ candidate: name, input, reference: d.ref, got: d.got, seed: Number(seedArg), found_at: new Date().toISOString() }, null, 2) + '\n')
  }
  result.disagreements.push({ candidate: name, input, reference: d.ref, got: d.got, file: path.basename(file), new: isNew })
  return true
}

try {
  // 1. Saved regression cases first.
  if (existsSync(regDir))
    for (const f of readdirSync(regDir).filter(f => f.endsWith('.json')).sort()) {
      const c = JSON.parse(readFileSync(path.join(regDir, f), 'utf8'))
      if (!names.includes(c.candidate)) continue
      result.replayed++
      record(c.candidate, c.input, false)
    }
  // 2. Fresh random inputs within the input and time budget.
  const rng = makeRng(Number(seedArg))
  const maxInputs = Number(maxInputsArg)
  const budget = Number(budgetMsArg)
  while (result.inputs < maxInputs && Date.now() - started < budget) {
    const input = adapter.generate(rng)
    result.inputs++
    if (!inDomain(input)) {
      result.out_of_domain++
      continue
    }
    for (const name of names) {
      if (result.disagreements.filter(d => d.candidate === name).length >= 5) continue
      if (disagrees(name, input)) record(name, shrink(name, input), true)
    }
  }
} catch (e) {
  result.error = String(e && e.stack ? e.stack : e).slice(0, 800)
}
result.ms = Date.now() - started
result.candidates = names
result.per_candidate = Object.fromEntries(names.map(n => [n, result.disagreements.filter(d => d.candidate === n).length]))
process.stdout.write(JSON.stringify(result) + '\n')
