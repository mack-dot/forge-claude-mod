# policy.py - strict loader and evaluator for forge.yaml.
#
# Label: NATIVE CHECK (hand-written, not proved).
# Design rule: this file names no tool, no domain and no unit. Every tool name, path
# pattern, cap and argument rule comes from forge.yaml. Unknown keys, missing keys and
# contradictions are load errors (fail closed). Anything that forge.yaml does not list
# is blocked (default deny).
import hashlib
import json
import os
import posixpath
import re
import sys
import unicodedata

MONEY_MAX = (1 << 63) - 1
U64_MAX = (1 << 64) - 1


class PolicyError(Exception):
    pass


# ---------------------------------------------------------------- untrusted-value helpers
def clip(v, n=120):
    """repr of an untrusted value for a log line or an error message: at most n characters
    of it, plus its length and a hash when it is longer (F2: no log or reply amplification)."""
    r = repr(v)
    if len(r) <= n:
        return r
    h = hashlib.sha256(r.encode("utf-8", "surrogatepass")).hexdigest()[:12]
    return f"{r[:n]}...[{len(r)} chars, sha256 {h}]"


def _no_constant(name):
    raise ValueError(f"JSON constant {name} is not allowed")


def _finite_float(text):
    v = float(text)
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError(f"JSON number {text[:40]} is not finite")
    return v


def strict_loads(raw):
    """json.loads that refuses NaN, Infinity, -Infinity and numbers that overflow to inf (F5)."""
    return json.loads(raw, parse_constant=_no_constant, parse_float=_finite_float)


def norm_text(s):
    """Comparison form of a string for NEGATIVE rules (not_in, not_equals): NFKC, without
    format characters (zero-width and similar, category Cf), stripped, case-folded (F3)."""
    s = unicodedata.normalize("NFKC", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Cf")
    return s.strip().casefold()


class Refusal(Exception):
    """A policy refusal returned to the agent. code is a FORGE_* string."""

    def __init__(self, code, message, **data):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


# ---------------------------------------------------------------- glob matching
def glob_to_regex(pat):
    """Glob with gitignore-like anchoring.

    '**' matches any number of path segments (including none), '*' and '?' never
    cross '/'. A pattern without '/' matches a basename at any depth
    ('.env*' == '**/.env*'). A pattern with '/' is anchored at the root.
    A path also matches when one of its ancestor directories matches."""
    if not pat or pat.startswith("/"):
        raise PolicyError(f"bad path pattern {pat!r} (empty or absolute)")
    p = pat.rstrip("/")
    if "/" not in p:
        p = "**/" + p
    i, out = 0, []
    while i < len(p):
        if p.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif p.startswith("**", i):
            out.append(".*")
            i += 2
        elif p[i] == "*":
            out.append("[^/]*")
            i += 1
        elif p[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(p[i]))
            i += 1
    return re.compile("".join(out) + r"(?:/.*)?\Z", re.S)


class PathSet:
    def __init__(self, patterns, what):
        if not isinstance(patterns, list) or not all(isinstance(x, str) for x in patterns):
            raise PolicyError(f"paths.{what} must be a list of strings")
        self.patterns = list(patterns)
        self._re = [glob_to_regex(x) for x in patterns]

    def match(self, rel):
        rel = rel.strip("/")
        if rel in ("", "."):
            return False
        return any(r.match(rel) for r in self._re)

    def closes_dir(self, rel_dir):
        """True when every new child of rel_dir would match (e.g. 'tests' for 'tests/**')."""
        probe = (rel_dir.strip("/") + "/" if rel_dir.strip("/") else "") + "__forge_probe__/x"
        return self.match(probe)


# ---------------------------------------------------------------- schema helpers
def _keys(obj, where, required=(), optional=()):
    if not isinstance(obj, dict):
        raise PolicyError(f"{where} must be a mapping")
    extra = set(obj) - set(required) - set(optional)
    if extra:
        raise PolicyError(f"{where}: unknown keys {sorted(extra)}")
    missing = [k for k in required if k not in obj]
    if missing:
        raise PolicyError(f"{where}: missing keys {missing}")


def _int(v, where, lo=0, hi=U64_MAX):
    if type(v) is not int or not lo <= v <= hi:
        raise PolicyError(f"{where} must be an integer in [{lo}, {hi}]")
    return v


SSH_KEY_TYPES = ("sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com", "ssh-ed25519",
                 "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "rsa-sha2-256", "rsa-sha2-512",
                 "ssh-rsa")
RULE_KEYS = ("equals", "not_equals", "in", "not_in", "regex", "int_min", "int_max", "optional", "type")
SCALAR = {"string": lambda v: isinstance(v, str), "integer": lambda v: type(v) is int,
          "boolean": lambda v: isinstance(v, bool)}
DEFAULT_ID_REGEX = r"[A-Za-z0-9._:-]{1,128}"
PATH_MODES = ("read", "write", "git_revpath")


class ArgRule:
    def __init__(self, name, spec, where):
        _keys(spec, where, optional=RULE_KEYS)
        if not spec or set(spec) <= {"optional"}:
            raise PolicyError(f"{where}: empty rule")
        self.name, self.spec = name, dict(spec)
        self.optional = bool(spec.get("optional", False))
        for k in ("in", "not_in"):
            if k in spec and not isinstance(spec[k], list):
                raise PolicyError(f"{where}.{k} must be a list")
        self.regex = re.compile(spec["regex"]) if "regex" in spec else None
        for k in ("int_min", "int_max"):
            if k in spec and type(spec[k]) is not int:
                raise PolicyError(f"{where}.{k} must be an integer")
        if "type" in spec and spec["type"] not in SCALAR:
            raise PolicyError(f"{where}.type must be string, integer or boolean")
        # F3: a negative rule is a deny-list; without a scalar type, a list, an object or a
        # number that compares equal (1 == True) walks past it. Require the type, and every
        # listed value must have it.
        if "not_in" in spec or "not_equals" in spec:
            if "type" not in spec:
                raise PolicyError(f"{where}: not_in / not_equals need a scalar type (string, integer or boolean)")
            vals = list(spec.get("not_in") or []) + ([spec["not_equals"]] if "not_equals" in spec else [])
            if not all(SCALAR[spec["type"]](x) for x in vals):
                raise PolicyError(f"{where}: every not_in / not_equals value must be of type {spec['type']}")
        t = spec.get("type")
        self._neg = None
        if t == "string":
            self._neg = {norm_text(x) for x in list(spec.get("not_in") or [])
                         + ([spec["not_equals"]] if "not_equals" in spec else [])}

    def check(self, args):
        if self.name not in args:
            if self.optional:
                return
            raise Refusal("FORGE_ARG_REFUSED", f"argument {self.name!r} is required by policy", arg=self.name)
        v, s = args[self.name], self.spec

        def bad(why):
            raise Refusal("FORGE_ARG_REFUSED", f"argument {self.name!r} refused: {why}", arg=self.name)

        t = s.get("type")
        if t == "string" and not isinstance(v, str):
            bad("must be a string")
        if t == "boolean" and not isinstance(v, bool):
            bad("must be a boolean")
        if t == "integer" and type(v) is not int:
            bad("must be an integer")
        if t is None and not isinstance(v, (str, int, float, bool)) and v is not None:
            if any(k in s for k in ("equals", "in", "regex", "int_min", "int_max")):
                bad("must be a scalar")
        if "equals" in s and not (v == s["equals"] and type(v) is type(s["equals"])):
            bad(f"must equal {s['equals']!r}")
        if "in" in s and not any(v == x and type(v) is type(x) for x in s["in"]):
            bad(f"must be one of {s['in']!r}")
        if self._neg is not None and norm_text(v) in self._neg:
            bad(f"must not be one of {sorted(self._neg)!r} (compared case-folded, NFKC, stripped)")
        elif self._neg is None:
            if "not_equals" in s and v == s["not_equals"]:
                bad(f"must not equal {s['not_equals']!r}")
            if "not_in" in s and v in s["not_in"]:
                bad(f"must not be one of {s['not_in']!r}")
        if self.regex is not None and (not isinstance(v, str) or not self.regex.fullmatch(v)):
            bad(f"must fully match /{self.regex.pattern}/")
        if ("int_min" in s or "int_max" in s) and type(v) is not int:
            bad("must be an integer")
        if "int_min" in s and v < s["int_min"]:
            bad(f"must be >= {s['int_min']}")
        if "int_max" in s and v > s["int_max"]:
            bad(f"must be <= {s['int_max']}")


class ToolPolicy:
    """One allowed or gated tool."""

    def __init__(self, spec, where, gated):
        base = ("tool",)
        opt = ("upstream", "arg_rules", "path_args", "strict_args", "allow_args", "require_human_approval")
        gopt = ("log", "unit", "value_field", "cap", "per_call_max", "expires_in_s", "expires_at",
                "id_field", "id_regex", "checker")
        if gated:
            _keys(spec, where, required=base + ("cap",), optional=opt + gopt)
        else:
            _keys(spec, where, required=base, optional=opt)
        self.name = spec["tool"]
        if not isinstance(self.name, str) or not self.name:
            raise PolicyError(f"{where}.tool must be a non-empty string")
        self.upstream = spec.get("upstream")
        self.raw = self.name  # the name the upstream knows (differs when namespace: true)
        self.role_log = self.role_cap = None  # set by a role with a cap for this tool (an extra, smaller log)
        self.gated = gated
        # Held until a person signs this exact call with a hardware key (human_approval.py).
        rha = spec.get("require_human_approval")
        if rha not in (None, "hardware"):
            raise PolicyError(f"{where}.require_human_approval must be hardware")
        self.human_approval = rha == "hardware"
        self.rules = [ArgRule(k, v, f"{where}.arg_rules.{k}") for k, v in (spec.get("arg_rules") or {}).items()]
        pa = spec.get("path_args") or {}
        _keys(pa, f"{where}.path_args", optional=list(pa))
        for k, m in pa.items():
            if m not in PATH_MODES:
                raise PolicyError(f"{where}.path_args.{k} must be one of {PATH_MODES}")
        self.path_args = dict(pa)
        # F3: strict by default. Allowed argument names: the policy's own (rules, path_args,
        # value_field, id_field, allow_args) plus the properties the upstream declares in the
        # tool's inputSchema. Opt-out: strict_args: false (documented in README).
        if "strict_args" in spec and not isinstance(spec["strict_args"], bool):
            raise PolicyError(f"{where}.strict_args must be true or false")
        self.strict_args = spec.get("strict_args", True)
        aa = spec.get("allow_args") or []
        if not isinstance(aa, list) or not all(isinstance(x, str) for x in aa):
            raise PolicyError(f"{where}.allow_args must be a list of strings")
        self.allow_args = set(aa)
        if gated:
            self.unit = spec.get("unit", "count")
            if self.unit not in ("count", "value"):
                raise PolicyError(f"{where}.unit must be count or value")
            self.value_field = spec.get("value_field")
            if (self.unit == "value") != (self.value_field is not None):
                raise PolicyError(f"{where}: value_field is required for unit=value and only for it")
            self.cap = _int(spec["cap"], f"{where}.cap", 0, MONEY_MAX)
            self.per_call_max = spec.get("per_call_max")
            if self.per_call_max is not None:
                _int(self.per_call_max, f"{where}.per_call_max", 0, MONEY_MAX)
            if ("expires_in_s" in spec) == ("expires_at" in spec):
                raise PolicyError(f"{where}: give exactly one of expires_in_s or expires_at")
            self.expires_in_s = spec.get("expires_in_s")
            self.expires_at = spec.get("expires_at")
            if self.expires_in_s is not None:
                _int(self.expires_in_s, f"{where}.expires_in_s", 1)
            if self.expires_at is not None:
                _int(self.expires_at, f"{where}.expires_at", 1)
            self.log = spec.get("log", self.name)
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", self.log):
                raise PolicyError(f"{where}.log must match [A-Za-z0-9_.-]{{1,64}}")
            self.id_field = spec.get("id_field")
            if "id_regex" in spec and self.id_field is None:
                raise PolicyError(f"{where}: id_regex needs id_field")
            try:
                self.id_regex = re.compile(spec.get("id_regex", DEFAULT_ID_REGEX))
            except (re.error, TypeError) as e:
                raise PolicyError(f"{where}.id_regex does not compile: {e}") from None
            ck = spec.get("checker")
            if ck is not None:
                _keys(ck, f"{where}.checker", required=("command",), optional=("timeout_s",))
                if not isinstance(ck["command"], list) or not ck["command"]:
                    raise PolicyError(f"{where}.checker.command must be a non-empty list")
            self.checker = ck

    def known_args(self):
        names = {r.name for r in self.rules} | set(self.path_args) | self.allow_args
        if self.gated:
            names |= {x for x in (self.value_field, self.id_field) if x}
        return names


LLM_KEYS = ("provider_url", "key_env", "auth_header", "unit", "session_cap", "daily_cap", "per_request_max",
            "allowed_models", "prices", "bytes_per_input_token", "free_paths")
MODEL_RE = re.compile(r"[A-Za-z0-9._:@/-]{1,128}")


def parse_llm(spec):
    """forge.yaml `llm`: the airlock's model endpoint and its spending gate. None if absent.

    The key is named (key_env), never written. Caps are integers in the chosen unit:
    unit=tokens (input + cache + output tokens) or unit=cost (estimated cost units:
    micro-USD from the prices table, USD per million tokens). Label: NATIVE CHECK."""
    if spec is None:
        return None
    _keys(spec, "llm", required=("key_env", "session_cap", "allowed_models"), optional=LLM_KEYS)
    out = dict(spec)
    url = out.setdefault("provider_url", "https://api.anthropic.com")
    m = re.fullmatch(r"(https?)://([A-Za-z0-9.-]{1,253})(?::([0-9]{1,5}))?/?", url) if isinstance(url, str) else None
    if not m:
        raise PolicyError("llm.provider_url must be http(s)://host[:port] (no path)")
    out["scheme"], out["host"] = m.group(1), m.group(2).lower()
    out["port"] = int(m.group(3) or (443 if m.group(1) == "https" else 80))
    if not isinstance(out["key_env"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", out["key_env"]):
        raise PolicyError("llm.key_env must be the NAME of an environment variable (not its value)")
    if out.setdefault("auth_header", "x-api-key") not in ("x-api-key", "bearer"):
        raise PolicyError("llm.auth_header must be x-api-key or bearer")
    if out.setdefault("unit", "tokens") not in ("tokens", "cost"):
        raise PolicyError("llm.unit must be tokens or cost")
    _int(out["session_cap"], "llm.session_cap", 1, MONEY_MAX)
    if out.get("daily_cap") is not None:
        _int(out["daily_cap"], "llm.daily_cap", 1, MONEY_MAX)
    out.setdefault("daily_cap", None)
    out["per_request_max"] = _int(out.get("per_request_max", out["session_cap"]), "llm.per_request_max", 1, MONEY_MAX)
    am = out["allowed_models"]
    if not isinstance(am, list) or not am or not all(isinstance(x, str) and MODEL_RE.fullmatch(x) for x in am):
        raise PolicyError("llm.allowed_models must be a non-empty list of model names")
    bpt = out.setdefault("bytes_per_input_token", 1)
    if type(bpt) is not int or not 1 <= bpt <= 8:
        raise PolicyError("llm.bytes_per_input_token must be an integer in [1, 8] (1 = most conservative)")
    prices = out.setdefault("prices", {})
    _keys(prices, "llm.prices", optional=list(prices))
    for mname, pr in prices.items():
        _keys(pr, f"llm.prices.{mname}", required=("input_per_mtok", "output_per_mtok"))
        for k, v in pr.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 10 ** 6:
                raise PolicyError(f"llm.prices.{mname}.{k} must be a number in [0, 1e6] (USD per million tokens)")
    if out["unit"] == "cost":
        missing = [x for x in am if x not in prices]
        if missing:
            raise PolicyError(f"llm.unit=cost: no price for allowed models {missing}")
    fp = out.setdefault("free_paths", ["/v1/messages/count_tokens", "/v1/models"])
    if not isinstance(fp, list) or not all(isinstance(x, str) and x.startswith("/v1/") for x in fp):
        raise PolicyError("llm.free_paths must be a list of /v1/... paths")
    if "/v1/messages" in fp:
        raise PolicyError("llm.free_paths must not contain /v1/messages (that path is always gated)")
    return out


ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]{0,127})\}")


def env_refs(doc):
    """Every ${NAME} named in upstream command, env, url or headers (names only)."""
    names = set()
    for u in (doc.get("upstreams") or []) if isinstance(doc, dict) else []:
        if not isinstance(u, dict):
            continue
        vals = list(u.get("command") or []) + [u.get("url") or ""]
        vals += list((u.get("env") or {}).values()) + list((u.get("headers") or {}).values())
        for v in vals:
            if isinstance(v, str):
                names.update(ENV_REF.findall(v))
    return sorted(names)


def _resolve(v, environ, where):
    if not isinstance(v, str):
        return v

    def sub(m):
        if m.group(1) not in environ:
            raise PolicyError(f"{where}: environment variable {m.group(1)} is not set (fail closed)")
        return environ[m.group(1)]
    return ENV_REF.sub(sub, v)


SEP = "__"
UPSTREAM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}")
ROLE_RE = re.compile(r"[a-z][a-z0-9_-]{0,23}")


def check_url(url, where):
    """An http upstream URL after ${NAME} substitution: http or https, a host, no user:pass@
    (credentials go in headers), no fragment."""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(url)
        port = u.port
    except ValueError as e:
        raise PolicyError(f"{where}: {e}") from None
    if u.scheme not in ("http", "https") or not u.hostname or "@" in u.netloc or u.fragment:
        raise PolicyError(f"{where} must be http(s)://host[:port]/path without user info or fragment")
    return port


def pattern_inside(p, b):
    """Conservative test: every path that glob p matches is also matched by glob b. False
    when unsure (so a role is refused rather than allowed to widen)."""
    p, b = p.rstrip("/"), b.rstrip("/")
    if p == b or b == "**":
        return True
    if "/" not in p:  # a basename pattern matches at any depth: inside only '**' or itself
        return False
    if b.endswith("/**"):
        return p.startswith(b[:-2])  # b 'src/**': p must start with the same leading segments 'src/'
    return False


ISOLATION_BACKENDS = ("docker", "gvisor", "microvm")  # the same names as airlock/backends


class Policy:
    def __init__(self, doc, environ=None, resolve=True, role=None):
        """resolve=True replaces ${NAME} in upstreams with the value from environ (the
        airlock side). resolve=False keeps the names (host side: no secret is read).
        role: the name of one entry of `roles:` (or None for the base policy). A role can
        only narrow the base policy; an unknown role is a load error (fail closed)."""
        _keys(doc, "forge.yaml", required=("version", "upstreams", "tools"),
              optional=("paths", "canary", "agent", "llm", "human_approval", "isolation", "namespace", "roles"))
        if doc["version"] != 1:
            raise PolicyError("version must be 1")
        if "namespace" in doc and not isinstance(doc["namespace"], bool):
            raise PolicyError("namespace must be true or false")
        self.namespace = doc.get("namespace", False)
        # upstreams
        if not isinstance(doc["upstreams"], list) or not doc["upstreams"]:
            raise PolicyError("upstreams must be a non-empty list")
        self.upstreams = {}
        folded = {}
        for i, u in enumerate(doc["upstreams"]):
            w = f"upstreams[{i}]"
            _keys(u, w, required=("name",), optional=("transport", "command", "url", "env", "headers", "timeout_s",
                                                      "user"))
            if not isinstance(u["name"], str) or not u["name"]:
                raise PolicyError(f"{w}.name must be a non-empty string")
            if self.namespace and not UPSTREAM_RE.fullmatch(u["name"]):
                raise PolicyError(f"{w}.name must match {UPSTREAM_RE.pattern} with namespace: true")
            if u["name"] in self.upstreams:
                raise PolicyError(f"{w}: duplicate upstream {u['name']!r}")
            # namespacing collision: with namespace: true the tool fs__x belongs to upstream fs.
            # An upstream named 'a__b' would make 'a__b__c' ambiguous ('a' + 'b__c' or 'a__b' + 'c'),
            # and 'fs' / 'FS' are confusable; both are load errors.
            if self.namespace and (SEP in u["name"] or u["name"].endswith("_")):
                raise PolicyError(f"{w}.name {u['name']!r}: contains {SEP!r} or ends in '_'; with namespace: true the tool names "
                                  f"<upstream>{SEP}<tool> would collide")
            if u["name"].casefold() in folded:
                raise PolicyError(f"{w}: upstream {u['name']!r} collides with {folded[u['name'].casefold()]!r} "
                                  "(names differ only in case)")
            folded[u["name"].casefold()] = u["name"]
            if "transport" not in u:  # the kind follows from the keys: url -> http, command -> stdio
                if ("url" in u) == ("command" in u):
                    raise PolicyError(f"{w}: give transport, or exactly one of url (http) and command (stdio)")
                u = dict(u, transport="http" if "url" in u else "stdio")
            if "timeout_s" in u and (isinstance(u["timeout_s"], bool) or not isinstance(u["timeout_s"], (int, float))
                                     or not 0 < u["timeout_s"] <= 3600):
                raise PolicyError(f"{w}.timeout_s must be a number in (0, 3600]")
            if u["transport"] == "stdio":
                if "user" in u:
                    _int(u["user"], f"{w}.user", 1, 65534)
                if not isinstance(u.get("command"), list) or "url" in u or "headers" in u:
                    raise PolicyError(f"{w}: stdio needs command (list) and no url/headers")
            elif u["transport"] == "http":
                if not isinstance(u.get("url"), str) or "command" in u or "env" in u or "user" in u:
                    raise PolicyError(f"{w}: http needs url and no command/env")
                if not re.match(r"https?://", u["url"]):
                    raise PolicyError(f"{w}.url must start with http:// or https://")
            else:
                raise PolicyError(f"{w}.transport must be stdio or http")
            if resolve:
                env = os.environ if environ is None else environ
                u = dict(u)
                if isinstance(u.get("command"), list):
                    u["command"] = [_resolve(x, env, f"{w}.command") for x in u["command"]]
                if "url" in u:
                    u["url"] = _resolve(u["url"], env, f"{w}.url")
                    check_url(u["url"], f"{w}.url")
                for k in ("env", "headers"):
                    if k in u:
                        if not isinstance(u[k], dict):
                            raise PolicyError(f"{w}.{k} must be a mapping")
                        u[k] = {kk: _resolve(vv, env, f"{w}.{k}.{kk}") for kk, vv in u[k].items()}
            self.upstreams[u["name"]] = u
        # tools
        t = doc["tools"]
        _keys(t, "tools", optional=("allow", "deny", "gate"))
        self.deny = list(t.get("deny") or [])
        if not all(isinstance(x, str) for x in self.deny):
            raise PolicyError("tools.deny must be a list of strings")
        self.tools = {}
        for gated, key in ((False, "allow"), (True, "gate")):
            for i, spec in enumerate(t.get(key) or []):
                tp = ToolPolicy(spec, f"tools.{key}[{i}]", gated)
                if tp.name in self.tools:
                    raise PolicyError(f"tool {tp.name!r} listed twice")
                if tp.name in self.deny:
                    raise PolicyError(f"tool {tp.name!r} is both denied and allowed/gated")
                if tp.upstream is not None and tp.upstream not in self.upstreams:
                    raise PolicyError(f"tool {tp.name!r}: unknown upstream {tp.upstream!r}")
                if self.namespace:
                    up, raw = self.split_name(tp.name, f"tools.{key}[{i}].tool")
                    if tp.upstream is not None and tp.upstream != up:
                        raise PolicyError(f"tool {tp.name!r}: upstream {tp.upstream!r} does not match its "
                                          f"namespace {up!r}")
                    tp.upstream, tp.raw = up, raw
                self.tools[tp.name] = tp
        if self.namespace:
            for i, n in enumerate(self.deny):
                self.split_name(n, f"tools.deny[{i}]")
        # human_approval: the keys that may sign a held call (allowed-signers lines) and how long a call waits
        ha = doc.get("human_approval")
        self.approval_signers, self.approval_timeout_s = [], 300
        if ha is not None:
            _keys(ha, "human_approval", required=("allowed_signers",), optional=("timeout_s",))
            lines = ha["allowed_signers"]
            if not isinstance(lines, list) or not lines or not all(isinstance(x, str) and x.strip() for x in lines):
                raise PolicyError("human_approval.allowed_signers must be a non-empty list of allowed-signers lines")
            for x in lines:
                parts = x.split()
                if "\n" in x or len(parts) < 3 or not any(t in SSH_KEY_TYPES for t in parts[1:-1]):
                    raise PolicyError(f"human_approval.allowed_signers: {clip(x)} is not 'principal [options] "
                                      "keytype base64'")
            self.approval_signers = list(lines)
            if "timeout_s" in ha:
                self.approval_timeout_s = _int(ha["timeout_s"], "human_approval.timeout_s", 1, 3600)
        if any(tp.human_approval for tp in self.tools.values()) and not self.approval_signers:
            raise PolicyError("a tool has require_human_approval: hardware, but human_approval.allowed_signers "
                              "is missing (fail closed)")
        # logs shared by several gated tools must agree on cap and expiry
        self.logs = {}
        for tp in self.tools.values():
            if not tp.gated:
                continue
            key = (tp.cap, tp.expires_in_s, tp.expires_at)
            if self.logs.setdefault(tp.log, key) != key:
                raise PolicyError(f"log {tp.log!r}: gated tools that share it disagree on cap/expiry")
        roles = doc.get("roles")
        if roles is not None:
            _keys(roles, "roles", optional=list(roles))
            if not roles:
                raise PolicyError("roles must not be empty (omit it instead)")
            for rn in roles:
                if not isinstance(rn, str) or not ROLE_RE.fullmatch(rn):
                    raise PolicyError(f"roles: role name {rn!r} must match {ROLE_RE.pattern}")
        self.role_names = sorted(roles or {})
        self.role = role or None
        # paths
        p = doc.get("paths") or {}
        _keys(p, "paths", optional=("hidden_paths", "visible_paths", "readonly_paths", "mcp_root", "agent_root",
                                    "keep_commit_messages"))
        self.hidden = PathSet(p.get("hidden_paths") or [], "hidden_paths")
        # visible_paths absent: everything except hidden_paths. Present: ONLY matches, minus hidden.
        self.visible = PathSet(p["visible_paths"], "visible_paths") if "visible_paths" in p else None
        if self.visible is not None and not self.visible.patterns:
            raise PolicyError("paths.visible_paths must not be empty (omit it instead)")
        ro = p.get("readonly_paths") or []
        if isinstance(ro, list) and "forge.yaml" not in ro:
            ro = list(ro) + ["forge.yaml"]  # the policy file is always read-only in the box
        self.readonly = PathSet(ro, "readonly_paths")
        # every role is checked at load (a widening role is an error even when not selected)
        for rn in self.role_names:
            self._role(rn, roles[rn], apply=False)
        if self.role is not None:
            if self.role not in self.role_names:
                raise PolicyError(f"role {self.role!r} is not defined in roles: {self.role_names} (fail closed)")
            self._role(self.role, roles[self.role], apply=True)
        self.mcp_root = p.get("mcp_root", "/ws")
        self.agent_root = p.get("agent_root", "/workspace")
        self.keep_commit_messages = bool(p.get("keep_commit_messages", False))
        c = doc.get("canary") or {}
        _keys(c, "canary", optional=("egress_targets", "extra_secrets"))
        self.egress_targets = list(c.get("egress_targets") or ["1.1.1.1:443", "8.8.8.8:53", "example.com:443"])
        self.extra_secrets = list(c.get("extra_secrets") or [])
        a = doc.get("agent") or {}
        _keys(a, "agent", optional=("egress_allow",))
        self.egress_allow = list(a.get("egress_allow") or [])
        for hp in self.egress_allow:
            if not isinstance(hp, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}:[0-9]{1,5}", hp):
                raise PolicyError(f"agent.egress_allow entry {hp!r} must be host:port")
        # isolation backend of the agent container (forge_airlock.py checks that it is available)
        self.isolation = doc.get("isolation")
        if self.isolation is not None and self.isolation not in ISOLATION_BACKENDS:
            raise PolicyError(f"isolation must be one of {', '.join(ISOLATION_BACKENDS)}, not {self.isolation!r}")
        self.llm = parse_llm(doc.get("llm"))
        self.env_names = sorted(set(env_refs(doc)) | ({self.llm["key_env"]} if self.llm else set()))
        self.llm_key = None
        if self.llm and resolve:
            env = os.environ if environ is None else environ
            self.llm_key = env.get(self.llm["key_env"])
            if not self.llm_key:
                raise PolicyError(f"llm.key_env: environment variable {self.llm['key_env']} is not set (fail closed)")
        # The policy file itself is never writable by the agent and never synced back.
        self.protected = PathSet(["forge.yaml"], "protected")

    # ------------------------------------------------------------ namespacing and roles
    def split_name(self, name, where):
        """namespace: true. '<upstream>__<tool>' -> (upstream, tool); anything else is an error."""
        up, sep, raw = name.partition(SEP) if isinstance(name, str) else ("", "", "")
        if not sep or not raw or up not in self.upstreams:
            raise PolicyError(f"{where} {name!r}: with namespace: true a tool is named <upstream>{SEP}<tool> "
                              f"with a known upstream ({sorted(self.upstreams)})")
        return up, raw

    def _role(self, rn, spec, apply):
        """Check one role and, with apply=True, narrow this policy to it. A role can only
        remove tools, lower caps, add hidden paths and restrict visible paths. Every attempt
        to widen (a tool the base does not allow or gate, a cap above the base cap, a
        visible pattern outside the base visible_paths) is a load error."""
        w = f"roles.{rn}"
        _keys(spec, w, optional=("tools", "hidden_paths", "visible_paths", "cap"))
        names = spec.get("tools")
        if names is not None:
            if not isinstance(names, list) or not all(isinstance(x, str) for x in names):
                raise PolicyError(f"{w}.tools must be a list of tool names")
            for n in names:
                if n in self.deny:
                    raise PolicyError(f"{w}.tools: {n!r} is denied by the base policy (a role cannot widen)")
                if n not in self.tools:
                    raise PolicyError(f"{w}.tools: {n!r} is not allowed or gated by the base policy "
                                      "(a role cannot widen)")
        keep = set(self.tools) if names is None else set(names)
        caps = spec.get("cap")
        if caps is not None:
            gated = [n for n in keep if self.tools[n].gated]
            if type(caps) is int:
                caps = {n: caps for n in gated}
            if not isinstance(caps, dict):
                raise PolicyError(f"{w}.cap must be an integer or a mapping tool -> integer")
            for n, c in caps.items():
                tp = self.tools.get(n)
                if tp is None or not tp.gated or n not in keep:
                    raise PolicyError(f"{w}.cap.{n}: not a gated tool of this role")
                _int(c, f"{w}.cap.{n}", 0, MONEY_MAX)
                if c > tp.cap:
                    raise PolicyError(f"{w}.cap.{n} = {c} is above the base cap {tp.cap} (a role cannot widen)")
            rlogs = {}
            for n, c in caps.items():
                tp = self.tools[n]
                ln = f"{tp.log}.role-{rn}"
                if len(ln) > 64:
                    raise PolicyError(f"{w}: log name {ln!r} is longer than 64 characters")
                key = (c, tp.expires_in_s, tp.expires_at)
                if rlogs.setdefault(ln, key) != key:
                    raise PolicyError(f"{w}.cap: tools that share log {tp.log!r} need the same role cap")
        hid = spec.get("hidden_paths")
        if hid is not None:
            PathSet(hid, f"{w}.hidden_paths")
        vis = spec.get("visible_paths")
        if vis is not None:
            PathSet(vis, f"{w}.visible_paths")
            if not vis:
                raise PolicyError(f"{w}.visible_paths must not be empty (omit it instead)")
            if self.visible is not None:
                for pat in vis:
                    if not any(pattern_inside(pat, b) for b in self.visible.patterns):
                        raise PolicyError(f"{w}.visible_paths: {pat!r} is not inside the base visible_paths "
                                          f"{self.visible.patterns} (a role cannot widen)")
        if not apply:
            return
        self.tools = {n: tp for n, tp in self.tools.items() if n in keep}
        for n, c in (caps or {}).items():
            tp = self.tools[n]
            tp.role_log = f"{tp.log}.role-{rn}"
            tp.role_cap = c
            self.logs[tp.role_log] = (c, tp.expires_in_s, tp.expires_at)
        if hid:
            self.hidden = PathSet(self.hidden.patterns + [x for x in hid if x not in self.hidden.patterns],
                                  "hidden_paths")
        if vis is not None:
            # every role pattern lies inside a base pattern (checked above), so the role set
            # alone is the intersection
            self.visible = PathSet(list(vis), "visible_paths")

    @staticmethod
    def load_doc(path):
        import yaml  # only the airlock image needs PyYAML; the host side passes parsed JSON
        with open(path, "rb") as f:
            raw = f.read()
        try:
            return yaml.safe_load(raw)
        except yaml.YAMLError as e:
            raise PolicyError(f"forge.yaml does not parse: {e}") from None

    @staticmethod
    def load(path, resolve=True, role=None):
        return Policy(Policy.load_doc(path), resolve=resolve, role=role)

    def secrets(self):
        """Every credential value held on the airlock side (for the canary)."""
        out = list(self.extra_secrets) + ([self.llm_key] if self.llm_key else [])
        for u in self.upstreams.values():
            for d in (u.get("env") or {}, u.get("headers") or {}):
                for v in d.values():
                    v = str(v)
                    out.append(v)
                    out.extend(x for x in re.split(r"[\s:=]+", v) if len(x) >= 8)
        return sorted(set(x for x in out if len(x) >= 8))

    # ------------------------------------------------------------ path checks
    def is_hidden(self, rel):
        """A file path the agent must not see: hidden wins over visible; with visible_paths
        set, anything that does not match it is hidden too (default deny for code)."""
        rel = rel.strip("/")
        if rel in ("", "."):
            return False
        if self.hidden.match(rel):
            return True
        return self.visible is not None and not self.visible.match(rel)

    def visible_dir(self, rel, basename_ok=True):
        """A directory that may be listed because a visible pattern may lie below it.
        Callers apply this only to directories (see _path_hidden)."""
        if self.visible is None:
            return True
        rel = rel.strip("/")
        if rel in ("", "."):
            return True
        for pat in self.visible.patterns:
            if "/" not in pat.rstrip("/"):
                if basename_ok:
                    return True  # basename pattern: may match at any depth
                continue
            lit = []
            for seg in pat.split("/"):
                if any(c in seg for c in "*?["):
                    break
                lit.append(seg)
            prefix = "/".join(lit)
            if prefix == rel or prefix.startswith(rel + "/"):
                return True
        return False

    def _path_hidden(self, rel, is_dir=True, basename_ok=True):
        if self.hidden.match(rel):
            return True
        return self.is_hidden(rel) and not (is_dir and self.visible_dir(rel, basename_ok))

    def check_path(self, raw, mode):
        """Canonicalize a path argument against mcp_root and apply hidden/readonly rules.

        Returns the path relative to the root. Both the lexical form and the
        realpath form (after symlinks) must pass."""
        if not isinstance(raw, str) or "\x00" in raw:
            raise Refusal("FORGE_PATH_REFUSED", "path argument must be a string without NUL")
        root = os.path.realpath(self.mcp_root)
        p = raw
        if p.startswith(self.agent_root.rstrip("/") + "/") or p == self.agent_root:
            p = p[len(self.agent_root):]
        elif p.startswith("/"):
            raise Refusal("FORGE_PATH_OUTSIDE", f"absolute path {clip(raw)} is outside the workspace")
        lexical = posixpath.normpath("/" + p.lstrip("/"))  # resolves '..' without escaping '/'
        if posixpath.normpath(p.lstrip("/") or ".").startswith(".."):
            raise Refusal("FORGE_PATH_OUTSIDE", f"path {clip(raw)} escapes the workspace")
        lex_rel = lexical.lstrip("/")
        real = os.path.realpath(os.path.join(root, p.lstrip("/")))
        if real != root and not real.startswith(root + "/"):
            raise Refusal("FORGE_PATH_OUTSIDE", f"path {clip(raw)} resolves outside the workspace")
        real_rel = os.path.relpath(real, root)
        real_rel = "" if real_rel == "." else real_rel
        is_dir = os.path.isdir(real)
        for rel in (lex_rel, real_rel):
            if self._path_hidden(rel, is_dir):
                raise Refusal("FORGE_PATH_HIDDEN", f"path {clip(raw)} is hidden by policy")
            if rel == ".git" or rel.startswith(".git/"):
                raise Refusal("FORGE_PATH_REFUSED", f"path {clip(raw)} is inside .git")
            if mode == "write" and (self.readonly.match(rel) or self.readonly.closes_dir(rel)
                                    or self.protected.match(rel)):
                raise Refusal("FORGE_PATH_READONLY", f"path {clip(raw)} is read-only by policy")
        return real_rel

    def check_revpath(self, raw):
        if not isinstance(raw, str) or ":" not in raw:
            raise Refusal("FORGE_PATH_REFUSED", "git spec must be '<rev>:<path>'")
        rev, path = raw.split(":", 1)
        if not re.fullmatch(r"[A-Za-z0-9_./~^@{}-]{1,100}", rev) or rev.startswith("-"):
            raise Refusal("FORGE_PATH_REFUSED", "bad revision in git spec")
        lexical = posixpath.normpath("/" + path).lstrip("/")
        if path.startswith("/") or posixpath.normpath(path or ".").startswith(".."):
            raise Refusal("FORGE_PATH_OUTSIDE", "git path escapes the repository")
        if lexical not in ("", ".") and self._path_hidden(lexical, True, basename_ok=False):
            raise Refusal("FORGE_PATH_HIDDEN", f"git path {clip(path)} is hidden by policy")
        return raw

    # ------------------------------------------------------------ call check
    def check_call(self, name, args, declared=()):
        """Static checks for one tools/call. Returns (ToolPolicy, arguments to forward).
        declared: argument names the upstream's inputSchema lists for this tool.

        A path argument may be a string or a list of strings; every element is checked."""
        if not isinstance(name, str):
            raise Refusal("FORGE_TOOL_BLOCKED", "tool name must be a string", tool=clip(name))
        if name in self.deny:
            raise Refusal("FORGE_TOOL_BLOCKED", f"tool {clip(name)} is denied by policy", tool=clip(name))
        tp = self.tools.get(name)
        if tp is None:
            raise Refusal("FORGE_TOOL_BLOCKED", f"tool {clip(name)} is not listed in policy (default deny)",
                          tool=clip(name))
        if not isinstance(args, dict):
            raise Refusal("FORGE_ARG_REFUSED", "arguments must be an object")
        if tp.strict_args:
            extra = set(args) - tp.known_args() - set(declared or ())
            if extra:
                raise Refusal("FORGE_ARG_REFUSED", f"arguments not declared in policy or in the tool's "
                              f"inputSchema: {clip(sorted(extra))}")
        for r in tp.rules:
            r.check(args)
        fwd = dict(args)
        root = os.path.realpath(self.mcp_root)
        for k, mode in tp.path_args.items():
            if k not in args:
                continue
            vals = args[k] if isinstance(args[k], list) else [args[k]]
            out = []
            for v in vals:
                if mode == "git_revpath":
                    out.append(self.check_revpath(v))
                else:  # forward the canonical absolute path, not the agent's spelling
                    rel = self.check_path(v, mode)
                    out.append(os.path.join(root, rel) if rel else root)
            fwd[k] = out if isinstance(args[k], list) else out[0]
        return tp, fwd


def canonical_json(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def main(argv):
    """policy.py check <forge.yaml> [role]: validate without secrets and print the parsed document
    as JSON (names of ${VARS} kept). Used by forge_airlock.py on the host. Exit 2 if invalid."""
    if len(argv) not in (3, 4) or argv[1] != "check":
        print("usage: policy.py check <forge.yaml> [role]", file=sys.stderr)
        return 2
    try:
        doc = Policy.load_doc(argv[2])
        Policy(doc, resolve=False, role=argv[3] if len(argv) == 4 else None)
    except (PolicyError, OSError) as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    print(json.dumps({"ok": True, "doc": doc}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
