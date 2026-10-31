/** Plain language: `simple` is one line for the graph card, `about` is 2-3 sentences on "what this topic is about" */
export const topicTexts: Record<string, { simple: string; about: string }> = {
  sets: {
    simple: "You learn to say exactly what a collection of objects contains and how to build new collections out of old ones.",
    about:
      "A set is just a collection of objects, and the quantifiers \"for all\" and \"there exists\" let you make a claim about every object at once. You also learn to negate such statements correctly. All of calculus is written in this language, and without it the definitions look like a pile of squiggles.",
  },
  functions: {
    simple: "A function is a rule that gives every input number exactly one output number.",
    about:
      "Here you work out which numbers a function can even be evaluated at, how to nest one function inside another, and how to run it backwards. Graphs let you see all of this with your own eyes. After this, every limit and every derivative is a conversation about functions.",
  },
  ineq: {
    simple: "You learn to estimate how much bigger one number is than another and to handle the absolute value, which is just distance from zero.",
    about:
      "The absolute value |x| tells you how far a number is from zero, and the triangle inequality says two sides of a triangle together are never shorter than the third. Together they give you analysis's main tool: \"this expression is at most that one\". The definition of a limit is basically a chain of such estimates.",
  },
  induction: {
    simple: "You check a statement for the first number, then prove that \"true for n\" gives \"true for n + 1\", and suddenly it holds for all of them.",
    about:
      "It's a way to prove something for all natural numbers at once, like a row of falling dominoes. You need two steps: the base case and the inductive step. Induction shows up all the time in proofs about sequences and sums.",
  },
  seq: {
    simple: "A sequence is an infinite list of numbers, and what you care about is how it behaves: does it grow, stay bounded, or bounce around?",
    about:
      "You look at the properties of a list of numbers: is it bounded, does it keep growing, can you pull out a sublist that behaves the way you want? These properties hint at whether the sequence will have a limit. Without them, neither limits nor series will work out.",
  },
  "lim-seq": {
    simple: "A limit is the number the terms of a sequence get close to and then stay close to.",
    about:
      "The ε–N definition says exactly what \"get close\" means: however small an accuracy you demand, from some index on every term meets it. This is the first rigorous definition in the course, and all the others look like it. Later it carries over to functions and to series.",
  },
  "lim-fn": {
    simple: "The limit of a function tells you what value it approaches as x gets close to a given point.",
    about:
      "The definition is the same as for sequences, except the accuracy is checked for x near the point instead of for large indices. What matters is that the value at the point itself can be different, or not defined at all. Continuity and the derivative both rest on limits of functions.",
  },
  "one-sided": {
    simple: "You look at what the function approaches when x goes to the point only from the left or only from the right.",
    about:
      "Sometimes the function behaves differently on the left and on the right: then the ordinary limit doesn't exist, but the one-sided ones do. The ordinary limit exists exactly when both one-sided limits exist and are equal. It comes in handy for finding discontinuities and for working with absolute values and piecewise functions.",
  },
  remarkable: {
    simple: "Two ready-made limits, sin x / x as x → 0 and (1 + 1/n)ⁿ as n → ∞, that a whole pile of other limits can be built from.",
    about:
      "The first limit equals 1, and the second leads to the number e. If you see a sine next to a small argument, or something like (1 + small)ⁿ, it usually comes down to one of them. Equivalents and the derivatives of sine and the exponential are derived from them later.",
  },
  infinitesimal: {
    simple: "An infinitesimal is a quantity that tends to zero, and the interesting part is comparing which one gets there faster.",
    about:
      "Of two quantities that tend to zero, one can do it faster than the other, and that's measured by the order of smallness. The notation o(x) is a handy way to say \"something much smaller than x\". You need this language for equivalent infinitesimals and Taylor's formula.",
  },
  equiv: {
    simple: "Near zero sin x is almost x and ln(1 + x) is almost x too, so inside limits you can swap the complicated thing for the simple one.",
    about:
      "Two quantities are equivalent if their ratio tends to 1, and in a product or a quotient you can replace one with the other. This cuts down the work on limits a lot. In a sum, though, such a swap is dangerous, and that's a classic trap.",
  },
  indet: {
    simple: "An indeterminate form is an expression like 0/0 where you can't tell what the limit is just by looking, so you have to rewrite it first.",
    about:
      "When the numerator and denominator both tend to zero, the answer can be anything, and the form \"0/0\" decides nothing by itself. You have to rewrite the expression: factor, multiply by a conjugate, use equivalents. ∞/∞, ∞ − ∞ and 1^∞ work the same way.",
  },
  continuity: {
    simple: "A function is continuous if you can draw its graph without lifting your pencil: a small change in x gives a small change in the value.",
    about:
      "Strictly, it means the limit at a point equals the value of the function there. Sums, products and compositions of continuous functions are continuous again. The theorems about intermediate values and extrema stand on continuity, and so does the existence of the derivative.",
  },
  discont: {
    simple: "A discontinuity is a spot where the graph \"breaks\": there's a hole, a jump, or the function shoots off to infinity.",
    about:
      "Discontinuities come as removable (a hole you can patch), jumps, and discontinuities of the second kind, where at least one one-sided limit doesn't exist or is infinite. To find the type, you compute the one-sided limits. It matters for sketching graphs and for integrals.",
  },
  weierstrass: {
    simple: "If a function is continuous on a closed interval, it doesn't run off to infinity there and it definitely hits a largest and a smallest value somewhere.",
    about:
      "The key point is that the interval is closed: on (0, 1) the function 1/x is continuous but unbounded. The theorems guarantee that the largest and smallest values exist, and you then find where they are using the derivative. They're also needed for many proofs about integrals.",
  },
  bolzano: {
    simple: "A continuous function that is negative at one point and positive at another has to hit zero somewhere in between.",
    about:
      "If a graph doesn't break, it can't jump over the axis without crossing it. It follows that a continuous function takes every intermediate value. This theorem is how you prove an equation has a root, and then you can find it by repeatedly halving the interval.",
  },
  uniform: {
    simple: "Uniform continuity means one and the same accuracy works on the whole interval at once, not separately for each point.",
    about:
      "In ordinary continuity the size of δ can depend on the point, but here one δ works for everyone. For example, x² can't do that on the whole line, but it can on a closed interval: that's the Heine–Cantor theorem. The property comes in handy in the theory of the integral.",
  },
  "deriv-def": {
    simple: "The derivative shows how fast a function changes at a point: it's the slope of the tangent line to the graph.",
    about:
      "You take the ratio of the change in the function to the change in x and see what it approaches as the change goes to zero. Physically, it's instantaneous speed. Almost everything else in calculus, such as optimization, graphs and Taylor series, is built on the derivative.",
  },
  rules: {
    simple: "A set of ready-made rules for quickly finding the derivative of a sum, product, quotient and standard functions without computing a limit every time.",
    about:
      "The table of derivatives together with the sum, product and quotient rules lets you differentiate almost any formula mechanically. It's worth learning once and for good. Without it, every derivative problem turns into a long slog of limits.",
  },
  chain: {
    simple: "If one function is nested inside another, the derivative is the derivative of the outer one times the derivative of the inner one.",
    about:
      "This is the chain rule: first you differentiate the \"outside\" without touching the inside, then you multiply by the derivative of what's inside. It also gives the derivative of an inverse function. You use it constantly, both in derivatives and in substitutions in integrals.",
  },
  extrema: {
    simple: "The sign of the derivative tells you whether the function is rising or falling, and the zeros of the derivative are the suspicious spots where a max or min might be.",
    about:
      "If the derivative is positive, the function is increasing, and if it's negative, decreasing. An extremum can only occur where the derivative is zero or doesn't exist. This is how you find the largest and smallest values in optimization problems.",
  },
  lhopital: {
    simple: "If a limit of a ratio gives 0/0 or ∞/∞, you can try taking the derivatives of the numerator and denominator separately.",
    about:
      "The rule works only for indeterminate forms of these two types, and only if the limit of the new ratio exists. Other indeterminate forms are first reduced to these. It's very handy for limits with logarithms and powers, but you have to check the conditions every time.",
  },
  taylor: {
    simple: "Near a point, you can replace a complicated function with a polynomial that has the same derivatives there.",
    about:
      "The more terms you take, the closer the polynomial follows the function, and the remainder shows how much error you have. This is how sin, exp and ln are computed without a calculator. Taylor's formula leads straight to Taylor series and power series.",
  },
  convex: {
    simple: "The second derivative shows which way the graph bends, like a bowl or like a dome, and where the bend switches.",
    about:
      "If the second derivative is positive, the function is convex (curves upward) like a bowl, and if it's negative, concave like a dome. The points where the bend switches are called inflection points. You need this to sketch a function's graph properly.",
  },
  asymptotes: {
    simple: "An asymptote is a line the graph gets closer and closer to as it heads off to infinity or toward a point of discontinuity.",
    about:
      "There are vertical, horizontal and slant asymptotes, and all of them are found with limits. Together with extrema and convexity, asymptotes give the full picture of how a graph behaves. This is the last step before you draw the whole graph.",
  },
  "num-series": {
    simple: "A series is an infinite sum of numbers, and the question is whether it ends up as a finite number.",
    about:
      "You add the terms one at a time and watch the partial sums: if they approach a number, the series converges and that number is its sum. Otherwise it diverges. Every convergence test starts from this definition.",
  },
  necessary: {
    simple: "If the terms of a series don't tend to zero, the series definitely diverges: you can't add up infinitely many things that aren't tiny.",
    about:
      "It's a quick check: if the limit of the terms isn't zero, you can stop thinking. But the converse is false: for the harmonic series 1/n the terms tend to zero and it diverges anyway. That's why you need other tests.",
  },
  comparison: {
    simple: "You compare your series with one you already know converges or diverges, and draw a conclusion.",
    about:
      "If the terms of your series are no bigger than those of a convergent series, it converges too, and if they're no smaller than those of a divergent one, it diverges. The benchmarks are the geometric series and the series 1/nᵖ. This trick saves you when the ratio and root tests give nothing.",
  },
  dalembert: {
    simple: "You look at how many times bigger each term is than the previous one, and that tells you whether the series converges.",
    about:
      "You take the limit of the ratio of consecutive terms: below 1 the series converges, above 1 it diverges, and at exactly 1 the test says nothing. It's especially good when the terms have factorials or exponential factors. For series with powers like 1/n² it's useless.",
  },
  "cauchy-root": {
    simple: "You take the n-th root of the n-th term and see what it tends to: if it's a number below 1, the series converges.",
    about:
      "It works like the ratio test: limit below 1 means convergence, above 1 divergence, equal to 1 no answer. It's convenient when the term itself is something raised to the n-th power. The same trick underlies the formula for the radius of convergence.",
  },
  alternating: {
    simple: "If the signs of the terms alternate plus-minus, the series can converge even when the series of absolute values diverges.",
    about:
      "The alternating series test says: if the absolute values decrease to zero, the alternating series converges. You tell apart absolute convergence (the series of absolute values converges) and conditional convergence (it converges only thanks to the alternating signs). For example, 1 − 1/2 + 1/3 − … converges, but 1 + 1/2 + 1/3 + … doesn't.",
  },
  power: {
    simple: "A power series is an infinite polynomial, and for each x it either adds up to a number or it doesn't.",
    about:
      "It's a series with terms aₙ(x − a)ⁿ, and the main question is for which x it converges. The answer always looks like an interval around the center, plus maybe the endpoints. Power series are how you write sin, exp and many other functions.",
  },
  radius: {
    simple: "The radius of convergence is the distance from the center within which a power series definitely converges and outside which it definitely diverges.",
    about:
      "You find a number R, and then the series converges for |x − a| < R and diverges for |x − a| > R. At the endpoints of the interval you have to check everything separately, since it can go either way. This is the final step when you analyze any power series.",
  },
  antideriv: {
    simple: "An antiderivative is a function whose derivative is the given one; basically, you run differentiation backwards.",
    about:
      "If F′ = f, then F is an antiderivative of f, and there's a whole family of them, differing by a constant. The indefinite integral is the notation for that whole family. This is the basis for computing areas and for all integration techniques.",
  },
  substitution: {
    simple: "In a tough integral you replace part of the expression with a new letter so that it becomes a standard one.",
    about:
      "It's the chain rule read backwards: if you see a function and its derivative under the integral, replace the function with t. The main thing is not to forget to convert dx and to go back to the original variable at the end. It's the most common integration technique.",
  },
  "by-parts": {
    simple: "If the integrand is a product of two functions, you can shift part of it onto a derivative to make the integral simpler.",
    about:
      "The formula ∫u dv = uv − ∫v du comes from the product rule for derivatives. You choose u so that its derivative gets simpler, and dv so that it's easy to integrate. It works for x·eˣ, x·sin x, ln x and the like.",
  },
  rational: {
    simple: "You break a fraction of polynomials into simple pieces, and each piece can be integrated from the table.",
    about:
      "First you pull out the whole part, then factor the denominator and write the fraction as a sum of partial fractions. Each partial fraction is integrated separately. The method is universal: every rational function can be integrated in the end.",
  },
  hyperbolic: {
    simple: "Sinh and cosh are built from e^x and e^-x, and they behave a lot like sin and cos, just without the circles.",
    about:
      "The hyperbolic sine and cosine are plain combinations of e^x and e^-x. They have their own identities that look like the trig ones with a sign flipped, and their derivatives are just as tidy. You'll run into them in integrals, in limits, and anywhere a hanging chain shows up.",
  },
  newton: {
    simple: "Pick a guess for a root, slide down the tangent line to the axis, and repeat until you land on it.",
    about:
      "At each step you replace the curve with its tangent line and take the point where that line crosses the x-axis as your next guess. It usually homes in on the root very fast, but a bad starting point or a flat tangent can send it off somewhere strange. It's the derivative doing real work for you.",
  },
  stirling: {
    simple: "It tells you roughly how big n! gets, using only square roots, powers and e.",
    about:
      "Factorials blow up so quickly that computing them directly is hopeless for big n. Stirling's formula swaps n! for a neat expression that is accurate when n is large. It's handy in limits with factorials and in the ratio test for series.",
  },
  parametric: {
    simple: "Instead of y = f(x), you describe a curve by saying where you are at each moment t.",
    about:
      "A parametric curve gives x and y as separate functions of a parameter t, so it can loop, cross itself or go backwards. You find the tangent slope as dy/dx = y'(t) / x'(t) by using the chain rule. Circles, spirals and cycloids are all easiest this way.",
  },
};
