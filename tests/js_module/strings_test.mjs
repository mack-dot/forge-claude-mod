// Test of the generated ES module's string, seq, set and int helpers (cli/js_wrapper.py). Test code, not proved.
// Usage: NODE_PATH=<folder with bignumber.js> node strings_test.mjs <generated Strings.mjs> <emitted Strings.js>
// Compares, on random strings that repeat (so the cache is hit), the generated module with the cache on, with the
// cache off, and the old loader (the emitted script evaluated with a require for bignumber.js). Exit 0: all equal.
import { Module } from 'node:module'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const [modPath, jsPath] = process.argv.slice(2).map(p => path.resolve(p))
const M = await import(pathToFileURL(modPath).href)

function oldLoader(abs) { // the way node_eval.mjs loads emitted code today
  const m = new Module(abs)
  m.filename = abs
  m.paths = [...(process.env.NODE_PATH || '').split(path.delimiter).filter(Boolean), ...Module._nodeModulePaths(path.dirname(abs))]
  m._compile(`${readFileSync(abs, 'utf8')}\nmodule.exports = { _dafny, Strs };`, abs)
  return m.exports
}
const O = oldLoader(jsPath)
const ostr = s => O._dafny.Seq.UnicodeFromString(s)
const ovs = q => q.toVerbatimString(false)

let seed = 12345
const rand = n => { seed = (Math.imul(seed, 1103515245) + 12345) >>> 0; return (seed >>> 8) % n }
const ALPHABET = [' ', '\t', '\n', ' ', ' ', '　', 'a', 'b', 'é', '😀', '*', 'companion', 'x', '漢']
const pool = Array.from({ length: 300 }, () => Array.from({ length: rand(8) }, () => ALPHABET[rand(ALPHABET.length)]).join(''))
const pick = () => pool[rand(pool.length)]

const failures = []
const check = (what, a, b) => { if (JSON.stringify(a) !== JSON.stringify(b)) failures.push(`${what}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`) }

function viaModule() {
  const S = M.Strs.__default
  const out = []
  for (let i = 0; i < 4000; i++) {
    const a = pick(), b = pick(), xs = [pick(), pick(), pick(), a]
    out.push([
      M.fromDafnyString(S.Trim(M.toDafnyString(a))),
      S.Reserved(M.toDafnyString(a)),
      M.fromDafnyString(S.Join(M.toDafnyString(a), M.toDafnyString(b))),
      S.InSet(M.toDafnyString(b), M.toDafnySet(xs, M.toDafnyString)),
      M.fromDafnyInt(S.Count(M.toDafnySeq(xs, M.toDafnyString), M.toDafnyString(a))),
      M.fromDafnyInt(S.Len(M.toDafnyString(a))),
      M.fromDafnySeq(S.Reverse(M.toDafnySeq(xs, M.toDafnyString)), M.fromDafnyString),
      M.fromDafnySet(M.toDafnySet(xs, M.toDafnyString), M.fromDafnyString).sort(),
    ])
  }
  return out
}
function viaOld() {
  const S = O.Strs.__default
  seed = 777
  const out = []
  for (let i = 0; i < 4000; i++) {
    const a = pick(), b = pick(), xs = [pick(), pick(), pick(), a]
    out.push([
      ovs(S.Trim(ostr(a))),
      S.Reserved(ostr(a)),
      ovs(S.Join(ostr(a), ostr(b))),
      S.InSet(ostr(b), O._dafny.Set.fromElements(...xs.map(ostr))),
      S.Count(O._dafny.Seq.of(...xs.map(ostr)), ostr(a)).toNumber(),
      S.Len(ostr(a)).toNumber(),
      [...S.Reverse(O._dafny.Seq.of(...xs.map(ostr)))].map(ovs),
      [...new Set(xs)].sort(),
    ])
  }
  return out
}

const old = viaOld()
seed = 777
const cached = viaModule()
const hits = M.stringCacheSize()
seed = 777
M.setStringCacheLimit(0)
const uncached = viaModule()
seed = 777
M.setStringCacheLimit(16)
const small = viaModule()
const smallSize = M.stringCacheSize()
for (let i = 0; i < old.length; i++) {
  check('cached vs old #' + i, cached[i], old[i])
  check('uncached vs old #' + i, uncached[i], old[i])
  check('cache of 16 vs old #' + i, small[i], old[i])
}
if (smallSize > 17) failures.push('the cache holds ' + smallSize + ' strings, over its limit of 16 + 1')
if (hits === 0) failures.push('the cache was never filled')

// a cached seq is frozen: a change throws (ES modules are strict) and the next call still gets the same value
M.setStringCacheLimit(100)
const q = M.toDafnyString('companion')
if (!Object.isFrozen(q) || !Object.isFrozen(q[0])) failures.push('a cached seq is not frozen')
try { q[0] = M.toDafnyString('x')[0]; failures.push('a cached seq could be changed') } catch { /* expected */ }
check('after a change attempt', M.fromDafnyString(M.toDafnyString('companion')), 'companion')
check('same object from the cache', M.toDafnyString('companion') === q, true)

// conversions refuse what they cannot hold exactly
const throwsOf = f => { try { f(); return 'none' } catch (e) { return e.name } }
check('lone surrogate', throwsOf(() => M.toDafnyString('a\uD800b')), 'RangeError')
check('unsafe number', throwsOf(() => M.toDafnyInt(2 ** 53)), 'RangeError')
check('fraction', throwsOf(() => M.toDafnyInt(1.5)), 'RangeError')
check('2^64 round trip', M.fromDafnyInt(M.toDafnyInt(2n ** 64n)) === 2n ** 64n, true)
check('-2^53+1 round trip', M.fromDafnyInt(M.toDafnyInt(-(2 ** 53) + 1)), -(2 ** 53) + 1)
check('string int', M.fromDafnyInt(M.toDafnyInt('-123456789012345678901234567890')) === -123456789012345678901234567890n, true)
check('astral round trip', M.fromDafnyString(M.toDafnyString('a😀b')), 'a😀b')
check('astral length', M.fromDafnyInt(M.Strs.__default.Len(M.toDafnyString('😀'))), 1)

if (failures.length) {
  process.stdout.write('FAIL ' + failures.length + '\n' + failures.slice(0, 5).join('\n') + '\n')
  process.exit(1)
}
process.stdout.write(`ok ${old.length} cases x 3 cache settings, cache held ${hits} strings\n`)
