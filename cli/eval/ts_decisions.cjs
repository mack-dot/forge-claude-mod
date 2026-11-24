// Finds the places in TypeScript / JavaScript files where code READS a value to DECIDE something (an if
// condition, a comparison, a ternary, a switch, .includes, a filter predicate, ...). Used by `forge_check.py
// bypass` (cli/forge_ast.py). It only parses; it never runs the files.
//
// usage: node ts_decisions.cjs /path/to/typescript.js  < {"files": [{"path": "...", "text": "..."}]}
// out:   {"files": {path: {"ok": true, "spans": [[start, end, kind], ...], "excluded": [[start, end], ...]}
//                         | {"ok": false, "error": "line N: message"}}}
// Offsets are UTF-16 code units into the given text (JavaScript string indexes).
// A file with any parse error is reported as not ok: the caller then searches that file as text (fail closed).
'use strict'
const fs = require('fs')
const ts = require(process.argv[2])
const K = ts.SyntaxKind

const COMPARE = new Set([K.EqualsEqualsEqualsToken, K.ExclamationEqualsEqualsToken, K.EqualsEqualsToken,
  K.ExclamationEqualsToken, K.LessThanToken, K.LessThanEqualsToken, K.GreaterThanToken,
  K.GreaterThanEqualsToken, K.InstanceOfKeyword])
const INCLUDES = new Set(['includes', 'has', 'indexOf', 'lastIndexOf', 'startsWith', 'endsWith', 'hasOwnProperty',
  'hasOwn', 'contains', 'test', 'match'])
const PREDICATE = { filter: 'filter-predicate', find: 'find-predicate', findIndex: 'find-predicate',
  findLast: 'find-predicate', findLastIndex: 'find-predicate', some: 'predicate', every: 'predicate' }
const KEY_READERS = new Set(['get', 'has', 'hasOwnProperty', 'hasOwn', 'getAttribute', 'getItem', 'pick', 'omit'])
const EXIT = new Set([K.ReturnStatement, K.ThrowStatement, K.ContinueStatement, K.BreakStatement])

function scriptKind(path) {
  if (/\.tsx$/i.test(path)) return ts.ScriptKind.TSX
  if (/\.(ts|mts|cts)$/i.test(path)) return ts.ScriptKind.TS
  return ts.ScriptKind.JSX  // .js .jsx .mjs .cjs: JavaScript, JSX allowed
}

function isFunctionLike(n) {
  return n && (ts.isArrowFunction(n) || ts.isFunctionExpression(n) || ts.isFunctionDeclaration(n)
    || ts.isMethodDeclaration(n) || ts.isConstructorDeclaration(n) || ts.isGetAccessor(n) || ts.isSetAccessor(n))
}

function isLiteralDefault(n) {
  while (n && ts.isParenthesizedExpression(n)) n = n.expression
  return n && (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n) || ts.isNumericLiteral(n)
    || ts.isArrayLiteralExpression(n) || ts.isObjectLiteralExpression(n) || n.kind === K.NullKeyword
    || n.kind === K.TrueKeyword || n.kind === K.FalseKeyword
    || (ts.isIdentifier(n) && n.text === 'undefined'))
}

// A SQL text with a WHERE clause (a string or template literal).
function sqlWhere(n) {
  if (!n) return false
  let t = null
  if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) t = n.text
  else if (ts.isTemplateExpression(n)) t = n.head.text + n.templateSpans.map((s) => s.literal.text).join(' ')
  return t !== null && /\b(select|update|delete)\b[\s\S]*\bwhere\b/i.test(t)
}

function calleeName(call) {
  const e = call.expression
  if (ts.isPropertyAccessExpression(e)) return e.name.text
  if (ts.isIdentifier(e)) return e.text
  return null
}

function analyze(path, text) {
  const sf = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true, scriptKind(path))
  if (sf.parseDiagnostics && sf.parseDiagnostics.length) {
    const d = sf.parseDiagnostics[0]
    const line = d.start !== undefined ? sf.getLineAndCharacterOfPosition(d.start).line + 1 : 0
    return { ok: false, error: 'line ' + line + ': ' + ts.flattenDiagnosticMessageText(d.messageText, ' ') }
  }
  const spans = []
  const excluded = []
  const seenComments = new Set()
  const names = new Map()  // identifier -> positions where it is read inside a decision (for the alias pass)

  const span = (n, kind) => { if (n) spans.push([n.getStart(sf), n.end, kind]) }
  const comments = (ranges) => {
    for (const r of ranges || []) {
      if (!seenComments.has(r.pos)) { seenComments.add(r.pos); excluded.push([r.pos, r.end]) }
    }
  }

  // The decision kind of `child` as a part of `parent`, or null.
  function partKind(parent, child, pred) {
    switch (parent.kind) {
      case K.IfStatement:
        if (child !== parent.expression) return null
        const t = parent.thenStatement
        const first = t && ts.isBlock(t) ? t.statements[0] : t
        return first && EXIT.has(first.kind) ? 'early-return-guard' : 'if-condition'
      case K.ConditionalExpression: return child === parent.condition ? 'ternary' : null
      case K.WhileStatement: case K.DoStatement: return child === parent.expression ? 'loop-condition' : null
      case K.ForStatement: return child === parent.condition ? 'loop-condition' : null
      case K.SwitchStatement: return child === parent.expression ? 'switch' : null
      case K.CaseClause: return child === parent.expression ? 'case' : null
      case K.ReturnStatement: return pred && child === parent.expression ? pred : null
      case K.ArrowFunction: return pred && child === parent.body && !ts.isBlock(child) ? pred : null
      case K.BinaryExpression: {
        const op = parent.operatorToken.kind
        if (op === K.AmpersandAmpersandToken && child === parent.left) return 'logical-guard'
        if (op === K.BarBarToken && child === parent.left && !isLiteralDefault(parent.right)) return 'logical-guard'
        return null
      }
    }
    return null
  }

  function visit(node, parent, inDecision, pred) {
    comments(ts.getLeadingCommentRanges(text, node.pos))
    comments(ts.getTrailingCommentRanges(text, node.end))
    if (ts.isTypeNode(node) && !ts.isExpressionWithTypeArguments(node)) {
      excluded.push([node.getStart(sf), node.end])
      return  // a type annotation or type expression: never a read
    }
    if (ts.isInterfaceDeclaration(node) || ts.isTypeAliasDeclaration(node) || ts.isImportDeclaration(node)
        || ts.isImportEqualsDeclaration(node) || (ts.isExportDeclaration(node) && node.moduleSpecifier)) {
      excluded.push([node.getStart(sf), node.end])
      return
    }
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || node.kind === K.TemplateHead
        || node.kind === K.TemplateMiddle || node.kind === K.TemplateTail || ts.isRegularExpressionLiteral(node)
        || ts.isJsxText(node)) {
      // A string is text, not a read, except where it names the key being read: obj["f"], "f" in obj, m.get("f").
      const keyRead = parent && ((ts.isElementAccessExpression(parent) && parent.argumentExpression === node)
        || (ts.isBinaryExpression(parent) && parent.operatorToken.kind === K.InKeyword && parent.left === node)
        || (ts.isCallExpression(parent) && KEY_READERS.has(calleeName(parent) || '') && parent.arguments.includes(node)))
      if (!keyRead) { excluded.push([node.getStart(sf), node.end]); return }
    }
    if (parent && ts.isPropertyAssignment(parent) && parent.name === node) {
      excluded.push([node.getStart(sf), node.end])  // { f: value } builds an object: the key f is not a read
      return
    }
    if (inDecision && ts.isIdentifier(node)) {
      if (!names.has(node.text)) names.set(node.text, [])
      names.get(node.text).push(node.getStart(sf))
    }
    let own = null
    if (ts.isBinaryExpression(node)) {
      const op = node.operatorToken.kind
      if (COMPARE.has(op)) own = 'comparison'
      else if (op === K.InKeyword) own = 'in'
    } else if (ts.isCallExpression(node)) {
      const name = calleeName(node)
      if (name && INCLUDES.has(name) && ts.isPropertyAccessExpression(node.expression)) own = 'includes'
    }
    if (ts.isCallExpression(node) && node.arguments.some(sqlWhere)) {
      // db.query("... WHERE tenant_id = $1", [row.tenantId]): the database decides with the parameters
      for (const a of node.arguments) if (!sqlWhere(a)) span(a, 'sql-where')
    } else if (ts.isTaggedTemplateExpression(node) && sqlWhere(node.template) && ts.isTemplateExpression(node.template)) {
      for (const sp of node.template.templateSpans) span(sp.expression, 'sql-where')
    }
    if (own) span(node, own)
    const decides = inDecision || own !== null
    // A callback given to filter/find/some/every: its returned value is a predicate.
    let childPred = isFunctionLike(node) ? null : pred
    if (isFunctionLike(node) && parent && ts.isCallExpression(parent) && parent.arguments[0] === node) {
      const name = calleeName(parent)
      if (name && PREDICATE[name] && ts.isPropertyAccessExpression(parent.expression)) childPred = PREDICATE[name]
    }
    ts.forEachChild(node, (child) => {
      const kind = partKind(node, child, childPred)
      if (kind) span(child, kind)
      visit(child, node, decides || kind !== null, childPred)
    })
  }
  visit(sf, null, false, null)

  // Alias pass: `const v = row.f` (or `v = row.f`, or `const { f: v } = row`) where v is read in a decision in the
  // same function (or a function inside it). Only a plain read is an alias: a call or an object built from the
  // field is not. A name-based over-approximation, not data flow.
  const scopes = []  // [start, end] of every function, innermost found by size
  const collect = (n) => { if (isFunctionLike(n)) scopes.push([n.getStart(sf), n.end]); ts.forEachChild(n, collect) }
  collect(sf)
  const scopeOf = (pos) => {
    let best = [0, text.length]
    for (const s of scopes) if (s[0] <= pos && pos < s[1] && s[1] - s[0] < best[1] - best[0]) best = s
    return best
  }
  const readInDecision = (name, pos) => {
    const s = scopeOf(pos)
    return (names.get(name) || []).some((p) => s[0] <= p && p < s[1])
  }
  const plainRead = (n) => {
    for (;;) {
      if (!n) return false
      if (ts.isParenthesizedExpression(n) || ts.isAwaitExpression(n) || ts.isAsExpression(n)
          || ts.isNonNullExpression(n) || ts.isSatisfiesExpression(n) || ts.isTypeAssertionExpression(n)) { n = n.expression; continue }
      if (ts.isBinaryExpression(n) && [K.QuestionQuestionToken, K.BarBarToken].includes(n.operatorToken.kind)) { n = n.left; continue }
      if (!(ts.isPropertyAccessExpression(n) || ts.isElementAccessExpression(n))) return false
      // a chain of member reads down to a name: row.body.f, this.x.f, row["f"]; not f(...).g or {..}.g
      let base = n
      while (ts.isPropertyAccessExpression(base) || ts.isElementAccessExpression(base) || ts.isNonNullExpression(base)
             || ts.isParenthesizedExpression(base)) base = base.expression
      return ts.isIdentifier(base) || base.kind === K.ThisKeyword
    }
  }
  function aliases(node) {
    if (ts.isVariableDeclaration(node) && node.initializer) {
      if (ts.isIdentifier(node.name)) {
        if (plainRead(node.initializer) && readInDecision(node.name.text, node.getStart(sf))) span(node.initializer, 'alias')
      } else if (ts.isObjectBindingPattern(node.name)) {
        for (const el of node.name.elements) {
          // const { f: v } = row: the read of f under another name
          if (el.propertyName && ts.isIdentifier(el.name) && readInDecision(el.name.text, node.getStart(sf))) span(el.propertyName, 'alias')
        }
      }
    } else if (ts.isBinaryExpression(node) && node.operatorToken.kind === K.EqualsToken && ts.isIdentifier(node.left)
        && plainRead(node.right) && readInDecision(node.left.text, node.getStart(sf))) {
      span(node.right, 'alias')
    }
    ts.forEachChild(node, aliases)
  }
  aliases(sf)
  return { ok: true, spans, excluded }
}

const input = JSON.parse(fs.readFileSync(0, 'utf8'))
const out = { typescript: ts.version, files: {} }
for (const f of input.files) {
  try { out.files[f.path] = analyze(f.path, f.text) } catch (e) { out.files[f.path] = { ok: false, error: 'parser crashed: ' + String(e && e.message || e).slice(0, 200) } }
}
process.stdout.write(JSON.stringify(out))
