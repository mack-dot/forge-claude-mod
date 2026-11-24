// CRASH-CHECK fixture (tests/crash_cases.py): three planted crashes, one fail-closed handler, one private helper.
// Do not fix: the tests depend on these bugs. Plain TypeScript, no framework needed.

type Req = { body: any }
type Res = { status(c: number): Res; json(b: unknown): Res }

// planted 1: a missing field (body.item) -> TypeError reading .quantity of undefined
export function createOrder(req: Req, res: Res) {
  const qty = req.body.item.quantity
  return res.status(201).json({ qty })
}

// planted 2: a null deref (user: null) -> TypeError reading .name of null
export async function POST(request: Request) {
  const data = await request.json()
  if (data.user === undefined) return new Response('missing user', { status: 400 })
  return new Response(String(data.user.name.length))
}

// planted 3: JSON.parse on a value that is not text (an object arrives) -> SyntaxError
export function parseWebhook(raw: string) {
  const event = JSON.parse(raw)
  return typeof event === 'object' && event !== null ? event.type : 'unknown'
}

// negative control: checks everything first and answers 400, never throws
export function updateLimit(req: Req, res: Res) {
  const b = req?.body
  if (!b || typeof b !== 'object' || typeof b.limit !== 'number' || !Number.isFinite(b.limit) || typeof b.name !== 'string')
    return res.status(400).json({ error: 'bad request' })
  return res.status(200).json({ limit: b.limit, name: normalizeName(b.name) })
}

// negative control: a private helper that throws on undefined; only updateLimit calls it, after its checks
function normalizeName(s: string) {
  return s.trim().toLowerCase()
}
