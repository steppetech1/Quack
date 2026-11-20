/**
 * A small evaluator of the SymPy-style expressions the teacher gives for a plot (`MathValue.sympy`): numbers, one
 * variable, `+ - * / ** ^`, parentheses, the usual functions and constants. No `eval`, no `Function`: the text is
 * tokenised and parsed here, and anything it does not know makes `compileExpr` return null (the caller then says it
 * cannot draw this one instead of drawing something wrong).
 */

type Tok = { t: "num"; v: number } | { t: "id"; v: string } | { t: "op"; v: string };

const FUNCS: Record<string, (x: number) => number> = {
  sin: Math.sin,
  cos: Math.cos,
  tan: Math.tan,
  asin: Math.asin,
  acos: Math.acos,
  atan: Math.atan,
  sinh: Math.sinh,
  cosh: Math.cosh,
  tanh: Math.tanh,
  exp: Math.exp,
  sqrt: Math.sqrt,
  cbrt: Math.cbrt,
  abs: Math.abs,
  floor: Math.floor,
  ceiling: Math.ceil,
  sign: Math.sign,
  log: Math.log,
  ln: Math.log,
  log2: Math.log2,
  log10: Math.log10,
};
const CONSTS: Record<string, number> = { pi: Math.PI, e: Math.E, E: Math.E, oo: Infinity };

function tokenize(src: string): Tok[] | null {
  const out: Tok[] = [];
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    if (/\s/.test(c)) {
      i++;
    } else if (/[0-9.]/.test(c)) {
      const m = /^(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?/.exec(src.slice(i));
      if (!m) return null;
      out.push({ t: "num", v: Number(m[0]) });
      i += m[0].length;
    } else if (/[A-Za-z_]/.test(c)) {
      const m = /^[A-Za-z_][A-Za-z0-9_]*/.exec(src.slice(i))!;
      out.push({ t: "id", v: m[0] });
      i += m[0].length;
    } else if (src.startsWith("**", i)) {
      out.push({ t: "op", v: "^" });
      i += 2;
    } else if ("+-*/^(),".includes(c)) {
      out.push({ t: "op", v: c });
      i++;
    } else {
      return null;
    }
  }
  return out;
}

type Node = (x: number) => number;

/** Recursive descent: sum → product → unary → power → atom. `^` is right-associative and binds tighter than unary minus */
function parse(tokens: Tok[], variable: string): Node | null {
  let pos = 0;
  const peek = () => tokens[pos];
  const isOp = (v: string) => peek()?.t === "op" && (peek() as { v: string }).v === v;
  let failed = false;
  const fail = (): Node => {
    failed = true;
    return () => NaN;
  };

  function atom(): Node {
    const tok = peek();
    if (!tok) return fail();
    if (tok.t === "num") {
      pos++;
      const v = tok.v;
      return () => v;
    }
    if (tok.t === "op" && tok.v === "(") {
      pos++;
      const inner = sum();
      if (!isOp(")")) return fail();
      pos++;
      return inner;
    }
    if (tok.t === "id") {
      pos++;
      if (isOp("(")) {
        const fn = FUNCS[tok.v] ?? (tok.v === "Abs" ? Math.abs : undefined);
        if (tok.v === "log" || tok.v === "Max" || tok.v === "Min") {
          // log(x, base) and Max/Min of two arguments
          pos++;
          const a = sum();
          let b: Node | null = null;
          if (isOp(",")) {
            pos++;
            b = sum();
          }
          if (!isOp(")")) return fail();
          pos++;
          if (tok.v === "log") return b ? (x) => Math.log(a(x)) / Math.log(b(x)) : (x) => Math.log(a(x));
          if (!b) return fail();
          return tok.v === "Max" ? (x) => Math.max(a(x), b(x)) : (x) => Math.min(a(x), b(x));
        }
        if (!fn) return fail();
        pos++;
        const arg = sum();
        if (!isOp(")")) return fail();
        pos++;
        return (x) => fn(arg(x));
      }
      if (tok.v === variable) return (x) => x;
      if (tok.v in CONSTS) {
        const v = CONSTS[tok.v];
        return () => v;
      }
      return fail();
    }
    return fail();
  }

  function power(): Node {
    const base = atom();
    if (isOp("^")) {
      pos++;
      const exp = unary(); // right side may carry its own sign: x**-2
      return (x) => Math.pow(base(x), exp(x));
    }
    return base;
  }

  function unary(): Node {
    if (isOp("-")) {
      pos++;
      const v = unary();
      return (x) => -v(x);
    }
    if (isOp("+")) {
      pos++;
      return unary();
    }
    return power();
  }

  function product(): Node {
    let left = unary();
    for (;;) {
      if (isOp("*")) {
        pos++;
        const l = left;
        const r = unary();
        left = (x) => l(x) * r(x);
      } else if (isOp("/")) {
        pos++;
        const l = left;
        const r = unary();
        left = (x) => l(x) / r(x);
      } else if (peek() && (peek().t === "num" || peek().t === "id" || isOp("("))) {
        // implicit product: 2x, 3(x+1), x sin(x)
        const l = left;
        const r = power();
        left = (x) => l(x) * r(x);
      } else {
        return left;
      }
    }
  }

  function sum(): Node {
    let left = product();
    for (;;) {
      if (isOp("+")) {
        pos++;
        const l = left;
        const r = product();
        left = (x) => l(x) + r(x);
      } else if (isOp("-")) {
        pos++;
        const l = left;
        const r = product();
        left = (x) => l(x) - r(x);
      } else {
        return left;
      }
    }
  }

  const root = sum();
  return failed || pos !== tokens.length ? null : root;
}

/** The function of `variable` the text describes, or null when the text is not something this evaluator understands */
export function compileExpr(src: string, variable: string): ((x: number) => number) | null {
  const tokens = tokenize(src);
  if (!tokens || !tokens.length) return null;
  const fn = parse(tokens, variable);
  if (!fn) return null;
  // a last check on the way: the function must give a number somewhere in a small window (a constant is a number too)
  return fn;
}

export type PlotSeries = {
  /** points per segment: a break (undefined, a pole, a jump off the visible range) starts a new segment */
  segments: [number, number][][];
  yMin: number;
  yMax: number;
};

/** Samples `fn` over [xMin, xMax]; the y range is the middle of what the curve does, so a pole does not flatten the rest */
export function samplePlot(fn: (x: number) => number, xMin: number, xMax: number, n = 400): PlotSeries | null {
  if (!(xMax > xMin) || !Number.isFinite(xMin) || !Number.isFinite(xMax)) return null;
  const pts: [number, number][] = [];
  for (let i = 0; i <= n; i++) {
    const x = xMin + ((xMax - xMin) * i) / n;
    const y = fn(x);
    pts.push([x, Number.isFinite(y) ? y : NaN]);
  }
  const ys = pts.map((p) => p[1]).filter((y) => Number.isFinite(y)).sort((a, b) => a - b);
  if (ys.length < 2) return null;
  const q = (p: number) => ys[Math.min(ys.length - 1, Math.max(0, Math.round(p * (ys.length - 1))))];
  let lo = q(0.02);
  let hi = q(0.98);
  if (hi - lo < 1e-9) {
    lo -= 1;
    hi += 1;
  }
  const pad = (hi - lo) * 0.12;
  const yMin = lo - pad;
  const yMax = hi + pad;
  const span = yMax - yMin;
  const segments: [number, number][][] = [];
  let cur: [number, number][] = [];
  let prev: number | null = null;
  for (const [x, y] of pts) {
    const visible = Number.isFinite(y) && y >= yMin - span && y <= yMax + span;
    // a jump over most of the picture between neighbours is a pole or a step: do not join the two sides
    const jump = prev !== null && Number.isFinite(y) && Math.abs(y - prev) > span * 0.8;
    if (!visible || jump) {
      if (cur.length > 1) segments.push(cur);
      cur = visible ? [[x, y]] : [];
    } else {
      cur.push([x, y]);
    }
    prev = Number.isFinite(y) ? y : null;
  }
  if (cur.length > 1) segments.push(cur);
  return segments.length ? { segments, yMin, yMax } : null;
}
