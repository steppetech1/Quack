import * as THREE from "three";
import { createDuckRig, GROUND, type DuckPose } from "../hero-duck/duck-rig";
import type { CompareSide, DuckScreenAnchor, FlowStep, LandingStageMutableState } from "./landing-context";

export type LandingStage = {
  resize(widthCss: number, heightCss: number, dpr?: number): void;
  render(timeSec: number, dt: number, pointerX: number, pointerY: number, pointerActive: boolean, reduced: boolean): void;
  triggerQuack(): void;
  dispose(): void;
};

// Classic 3/4 profile facing viewer-right so bill, mallard head, collar, chest, wing & feet read unmistakably
const FACING_FRONT_R = -0.58;
const FACING_FINALE_L = -Math.PI + 0.62;
const RIGHT_LOOK = -0.52;
const LEFT_LOOK = -Math.PI + 0.52;

const TRAIL_LEN = 20;

const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const smoothstep = (a: number, b: number, x: number) => {
  const t = clamp01((x - a) / (b - a));
  return t * t * (3 - 2 * t);
};
const approach = (v: number, target: number, rate: number, dt: number) =>
  dt <= 0 ? target : v + (target - v) * (1 - Math.exp(-rate * dt));
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), a | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------- Pass 2: Full-Screen Shader (Radial Tick Field + Liquid Obsidian Wake + Halftone 3/4 Duck + Reeds) ----------

const QUAD_VERT = /* glsl */ `
void main() {
  gl_Position = vec4(position.xy, 0.0, 1.0);
}
`;

const QUAD_FRAG = /* glsl */ `
precision highp float;

uniform sampler2D uDuck;
uniform sampler2D uBrandTex;
uniform vec2 uSize;        // CSS px
uniform vec2 uRes;         // device px
uniform float uCell;       // fine hex step in CSS px (4.5..6.0)
uniform float uTime;
uniform float uDisperse;   // 0..1 intro dispersion
uniform float uAway;       // 0..1 scroll dispersion
uniform float uHeroWeight; // 0..1 reed framing visibility on Hero
uniform float uDuckScale;  // 0..1 overall duck visibility
uniform float uTickOpacity;// 0..1 DeepBook radial tick field intensity
uniform vec2 uDuckCenter;  // normalized screen coords (0..1, y from top)
uniform vec2 uPointer;     // -1..1
uniform float uReduced;
uniform vec4 uTrail[20];   // (uvX, uvY, strength, ageSec)

const float SQRT3 = 1.7320508;
const float PI = 3.14159265;

float h21(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}

vec2 h22(vec2 p) {
  return vec2(h21(p), h21(p + vec2(41.7, 19.3)));
}

float sdSegment(vec2 p, vec2 a, vec2 b) {
  vec2 pa = p - a;
  vec2 ba = b - a;
  float h = clamp(dot(pa, ba) / max(dot(ba, ba), 1e-5), 0.0, 1.0);
  return length(pa - ba * h);
}

float sdStar6(vec2 p, float r, float w) {
  float d = sdSegment(p, vec2(-r, 0.0), vec2(r, 0.0));
  vec2 d1 = vec2(0.5, 0.8660254) * r;
  vec2 d2 = vec2(-0.5, 0.8660254) * r;
  d = min(d, sdSegment(p, -d1, d1));
  d = min(d, sdSegment(p, -d2, d2));
  return d - w;
}

float sdY(vec2 p, float r, float w, float flip) {
  vec2 up = vec2(0.0, -r * flip);
  vec2 l = vec2(-0.8660254, 0.5 * flip) * r;
  vec2 rg = vec2(0.8660254, 0.5 * flip) * r;
  float d = sdSegment(p, vec2(0.0), up);
  d = min(d, sdSegment(p, vec2(0.0), l));
  d = min(d, sdSegment(p, vec2(0.0), rg));
  return d - w;
}

// Crisp analytical SDF halftone glyph inside hex cell
float glyphCoverage(vec2 p, float lum, float h, float scale) {
  vec2 q = p / max(scale, 0.05);
  float aa = 1.35 / uCell;
  float d = 1.0;

  if (lum > 0.76) {
    float r = mix(0.25, 0.39, clamp((lum - 0.76) / 0.24, 0.0, 1.0));
    if (h < 0.85) {
      d = length(q) - r;
    } else {
      d = sdStar6(q, 0.40, 0.058);
    }
  } else if (lum > 0.52) {
    if (h < 0.50) {
      d = sdStar6(q, 0.38, 0.055);
    } else {
      d = length(q) - mix(0.21, 0.30, (lum - 0.52) / 0.24);
    }
  } else if (lum > 0.30) {
    if (h < 0.35) {
      d = sdStar6(q, 0.34, 0.05);
    } else if (h < 0.74) {
      d = sdY(q, 0.35, 0.05, h < 0.54 ? 1.0 : -1.0);
    } else {
      d = length(q) - 0.20;
    }
  } else if (lum > 0.14) {
    d = sdY(q, 0.31, 0.046, h < 0.5 ? 1.0 : -1.0);
  } else {
    d = length(q) - 0.15;
  }

  return 1.0 - smoothstep(-aa, aa, d);
}

// Mallard-in-amber semantic palette (matches glyphs.ts DUCK_RAMPS)
vec3 duckPartColor(vec4 s, float lum) {
  float mx = max(s.r, max(s.g, s.b));
  float thr = mx * 0.5;
  bool R = s.r > thr;
  bool G = s.g > thr;
  bool B = s.b > thr;

  vec3 shadow = vec3(0.165, 0.110, 0.070);
  vec3 nearShadow = vec3(0.353, 0.251, 0.165);
  vec3 hilite = vec3(0.955, 0.865, 0.720);

  vec3 mid0;
  vec3 mid1;
  vec3 warmHi;

  if (R && G && B) {
    // body: warm amber-bronze
    mid0 = vec3(0.714, 0.431, 0.184);
    mid1 = vec3(0.910, 0.604, 0.275);
    warmHi = vec3(0.973, 0.720, 0.514);
  } else if (G && !R && !B) {
    // head: mallard emerald-teal
    mid0 = vec3(0.298, 0.651, 0.486);
    mid1 = vec3(0.494, 0.831, 0.667);
    warmHi = vec3(0.831, 0.902, 0.753);
  } else if (R && G) {
    // beak: burnished gold
    mid0 = vec3(0.808, 0.627, 0.235);
    mid1 = vec3(0.941, 0.761, 0.322);
    warmHi = vec3(0.980, 0.871, 0.588);
  } else if (R && B) {
    // legs: terracotta orange
    mid0 = vec3(0.839, 0.431, 0.157);
    mid1 = vec3(0.965, 0.573, 0.235);
    warmHi = vec3(0.988, 0.753, 0.502);
  } else if (R) {
    // chest: russet amber
    mid0 = vec3(0.667, 0.353, 0.157);
    mid1 = vec3(0.839, 0.510, 0.243);
    warmHi = vec3(0.941, 0.690, 0.486);
  } else if (G && B) {
    // collar: creamy ivory
    mid0 = vec3(0.667, 0.588, 0.494);
    mid1 = vec3(0.914, 0.851, 0.761);
    warmHi = vec3(0.957, 0.894, 0.808);
  } else {
    // wing & back: deep warm umber
    mid0 = vec3(0.471, 0.314, 0.188);
    mid1 = vec3(0.659, 0.439, 0.251);
    warmHi = vec3(0.839, 0.643, 0.439);
  }

  vec3 c = mix(shadow, nearShadow, smoothstep(0.0, 0.16, lum));
  c = mix(c, mid0, smoothstep(0.16, 0.34, lum));
  c = mix(c, mid1, smoothstep(0.34, 0.60, lum));
  c = mix(c, warmHi, smoothstep(0.60, 0.85, lum));
  c = mix(c, hilite, smoothstep(0.85, 1.0, lum));
  return c;
}

vec4 sampleDuck(vec2 sp, float bias) {
  vec2 uv = vec2(sp.x / uSize.x, 1.0 - sp.y / uSize.y);
  if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) return vec4(0.0);
  return texture2D(uDuck, uv, bias);
}

// DeepBook-inspired concentric radial tick-mark precision field
vec3 radialTickField(vec2 pos, vec2 uvTop, float t, float opacity) {
  if (opacity <= 0.01) return vec3(0.0);
  vec2 origin = vec2(uSize.x * 0.5, uSize.y * 1.18);
  vec2 d = pos - origin;
  float r = length(d);
  float ang = atan(d.y, d.x);

  // Perspective radial rings
  float ringStep = 40.0;
  float ringIdx = floor(r / ringStep);
  float rLocal = fract(r / ringStep) - 0.5;
  float arcLen = max(ringIdx * ringStep, 120.0);
  float colCount = floor((arcLen * PI) / 28.0);
  float colF = (ang / PI) * colCount;
  float colIdx = floor(colF);
  float cLocal = (fract(colF) - 0.5) * 28.0;

  // Subtle reactive pointer sway
  vec2 pCss = vec2((uPointer.x * 0.5 + 0.5) * uSize.x, (0.5 - uPointer.y * 0.5) * uSize.y);
  float pDist = length(pos - pCss);
  float pReact = exp(-pDist * pDist / 26000.0) * (1.0 - uReduced);

  // Tick mark rectangle aligned vertically/radially
  float tickW = smoothstep(1.25, 0.25, abs(cLocal));
  float tickH = smoothstep(0.24, 0.14, abs(rLocal));
  float tick = tickW * tickH;
  if (tick <= 0.01) return vec3(0.0);

  // Fade softly around central Hero duck & top/bottom edges
  vec2 dc = (uvTop - uDuckCenter) / vec2(0.22, 0.28);
  float centerAtten = mix(1.0, smoothstep(0.65, 1.35, length(dc)), uHeroWeight * 0.7);
  float vFade = smoothstep(0.02, 0.22, uvTop.y) * smoothstep(0.99, 0.80, uvTop.y);

  float seed = h21(vec2(ringIdx * 13.7, colIdx * 7.3));
  float wave = 0.5 + 0.5 * sin(t * 1.2 - ringIdx * 0.55 + seed * 12.0);
  bool isAmberSignal = seed > 0.962;
  bool isSageSignal = seed > 0.938 && seed <= 0.962;

  vec3 col = vec3(0.94, 0.89, 0.82) * (0.07 + 0.05 * wave + pReact * 0.16);
  if (isAmberSignal) {
    col = vec3(0.93, 0.72, 0.40) * (0.35 + 0.55 * wave + pReact * 0.35);
  } else if (isSageSignal) {
    col = vec3(0.56, 0.78, 0.60) * (0.28 + 0.45 * wave);
  }

  return col * tick * opacity * centerAtten * vFade;
}

// Adrien Quechon-inspired cursor-reactive liquid obsidian-amber ink wake
vec3 liquidInkWake(vec2 uvTop, float t, vec3 baseCol) {
  if (uReduced > 0.5) return baseCol;
  float aspect = uSize.x / max(uSize.y, 1.0);
  vec2 p = vec2(uvTop.x * aspect, uvTop.y);

  // Smooth organic domain distortion
  vec2 warp = vec2(
    sin(p.y * 14.0 + t * 1.6) * 0.012 + cos(p.x * 21.0 - t * 1.1) * 0.006,
    cos(p.x * 16.0 - t * 1.4) * 0.012 + sin(p.y * 19.0 + t * 0.9) * 0.006
  );
  vec2 pw = p + warp;

  float field = 0.0;
  vec2 gradDir = vec2(0.0);

  for (int i = 0; i < 20; i++) {
    vec4 tr = uTrail[i];
    if (tr.z <= 0.005) continue;
    vec2 tp = vec2(tr.x * aspect, tr.y);
    vec2 diff = pw - tp;
    float d2 = dot(diff, diff);
    float rad = mix(0.018, 0.068, tr.z) * (1.0 - tr.w * 0.55);
    float contrib = tr.z * exp(-d2 / max(rad * rad * 0.36, 1e-5));
    field += contrib;
    gradDir += diff * contrib;
  }

  if (field < 0.08) return baseCol;

  float edge = smoothstep(0.18, 0.52, field);
  float rim = smoothstep(0.16, 0.36, field) * (1.0 - smoothstep(0.42, 0.85, field));
  float crest = sin(field * 14.0 - t * 2.2) * 0.5 + 0.5;
  float spec = pow(clamp(dot(normalize(gradDir + 1e-4), vec2(0.6, -0.8)) * 0.5 + 0.5, 0.0, 1.0), 3.0);

  // Dark glossy obsidian body with iridescent amber + cool steel edge sheen
  vec3 obsidian = vec3(0.025, 0.018, 0.015);
  vec3 iridAmber = vec3(0.93, 0.72, 0.40);
  vec3 iridSteel = vec3(0.52, 0.66, 0.79);
  vec3 sheen = mix(iridAmber, iridSteel, crest * 0.38) * (0.42 * rim + 0.28 * spec * edge);

  vec3 inkCol = obsidian + sheen;
  return mix(baseCol, inkCol, clamp(edge * 0.82 + rim * 0.45, 0.0, 0.92));
}

// Procedural swaying reeds along lower left/right Hero margins
float marshReeds(vec2 uvTop, float t) {
  if (uHeroWeight <= 0.01) return 0.0;
  float sx = uvTop.x < 0.5 ? uvTop.x : 1.0 - uvTop.x;
  if (sx > 0.31 || uvTop.y < 0.34) return 0.0;

  vec2 d = (uvTop - uDuckCenter) / vec2(0.24, 0.34);
  float ovalMask = smoothstep(0.75, 1.25, length(d));
  float topFade = smoothstep(0.34, 0.64, uvTop.y);
  float edgeFade = 1.0 - smoothstep(0.14, 0.31, sx);
  float mask = ovalMask * topFade * edgeFade * uHeroWeight;
  if (mask <= 0.02) return 0.0;

  float lum = 0.0;
  float par = (uvTop.x < 0.5 ? 1.0 : -1.0) * uPointer.x * 0.012;
  for (int i = 0; i < 6; i++) {
    float fi = float(i);
    float baseX = 0.02 + fi * 0.04 + 0.01 * sin(fi * 3.1);
    float stalkH = 0.25 + 0.14 * fract(sin(fi * 17.3) * 437.5);
    float yLocal = clamp((1.0 - uvTop.y) / stalkH, 0.0, 1.0);
    if (1.0 - uvTop.y > stalkH) continue;

    float sway = (0.014 + 0.008 * sin(fi * 2.1)) * sin(t * (0.75 + fi * 0.11) + fi * 1.7) * (1.0 - uReduced);
    float curveX = baseX + (0.022 * (0.5 - fract(fi * 0.37)) + sway + par) * yLocal * yLocal;
    float dist = abs(sx - curveX);
    float w = mix(0.0040, 0.0014, yLocal);
    float stalk = 1.0 - smoothstep(w * 0.5, w * 1.4, dist);

    float head = 0.0;
    if (mod(fi, 2.0) < 0.5 && yLocal > 0.66 && yLocal < 0.88) {
      float hw = 0.009 * sin(PI * (yLocal - 0.66) / 0.22);
      head = (1.0 - smoothstep(hw * 0.6, hw * 1.2, dist)) * 0.92;
    }

    float bladeY = clamp((1.0 - uvTop.y) / (stalkH * 0.72), 0.0, 1.0);
    float bladeX = baseX + 0.052 * pow(bladeY, 1.65) + sway * 0.6 * bladeY;
    float blade = (1.0 - smoothstep(0.0015, 0.0042, abs(sx - bladeX))) * (1.0 - bladeY * 0.65) * step(bladeY, 0.98);

    float depthShade = mix(0.42, 0.90, fi / 5.0);
    lum = max(lum, max(stalk * 0.65, max(head, blade * 0.55)) * depthShade);
  }
  return clamp(lum * mask, 0.0, 1.0);
}

void main() {
  vec2 k = uSize / uRes;
  vec2 pos = vec2(gl_FragCoord.x, uRes.y - gl_FragCoord.y) * k; // CSS px from top-left
  vec2 uvTop = pos / uSize;

  // Base warm obsidian ground + subtle pool glow beneath duck
  vec3 bg = vec3(0.031, 0.020, 0.016); // #080504
  vec2 poolVec = (uvTop - vec2(uDuckCenter.x, uDuckCenter.y + 0.06)) * vec2(1.0, 1.45);
  float poolGlow = exp(-dot(poolVec, poolVec) * 7.2) * uDuckScale;
  bg += vec3(0.13, 0.065, 0.022) * poolGlow;

  // Radial tick-mark precision instrument field
  bg += radialTickField(pos, uvTop, uTime, uTickOpacity);

  // Cursor-reactive liquid obsidian-amber ink wake
  bg = liquidInkWake(uvTop, uTime, bg);

  // Monumental architectural QUACK watermark directly behind the 3D duck
  vec2 brandUv = vec2(uvTop.x, 1.0 - uvTop.y);
  brandUv += vec2(-uPointer.x * 0.012, uPointer.y * 0.008) * (1.0 - uReduced);
  brandUv.y -= (1.0 - uHeroWeight) * 0.05;
  if (uHeroWeight > 0.01 && brandUv.x > 0.0 && brandUv.x < 1.0 && brandUv.y > 0.0 && brandUv.y < 1.0) {
    float mask = texture2D(uBrandTex, brandUv).a;
    if (mask > 0.01) {
      float hatch = mix(0.42, 1.0, step(0.36, fract(pos.x / 4.0)));
      float duckBoost = exp(-dot(poolVec, poolVec) * 4.5);
      vec3 brandCol = mix(
        vec3(0.95, 0.91, 0.85),
        vec3(0.93, 0.73, 0.40),
        clamp(0.28 + duckBoost * 0.65, 0.0, 1.0)
      );
      float brandAlpha = mask * hatch * (0.16 + 0.16 * duckBoost) * uHeroWeight * (1.0 - uDisperse * 0.65);
      bg = mix(bg, brandCol, clamp(brandAlpha, 0.0, 0.36));
    }
  }

  vec3 outCol = bg;

  // Fine hex grid coordinates
  vec2 q = pos / uCell;
  vec2 s = vec2(1.0, SQRT3);
  vec2 a = mod(q, s) - s * 0.5;
  vec2 b = mod(q - s * 0.5, s) - s * 0.5;
  vec2 gv = dot(a, a) < dot(b, b) ? a : b;
  vec2 id = q - gv;

  float r1 = h21(id * 1.31 + 3.7);
  float r2 = h21(id * 0.77 + 91.1);
  float anim = 1.0 - uReduced;
  vec2 wob = 0.04 * anim * vec2(sin(uTime * 0.95 + r1 * 40.0), cos(uTime * 0.75 + r2 * 40.0));
  vec2 local = gv + wob;
  vec2 center = id * uCell;

  float dd = max(uDisperse, uAway);
  float appear = clamp(((1.0 - dd) - r2 * 0.70) / 0.30, 0.0, 1.0);
  vec2 jitter = (h22(id + 11.0) - 0.5) * 2.0 * vec2(260.0, 190.0);
  vec2 samplePos = center + jitter * (dd * dd) - vec2(0.0, dd * dd * 110.0);

  vec4 dk = sampleDuck(samplePos, 0.0);
  vec4 dkSolid = sampleDuck(pos, 0.0);
  bool isDuck = dk.a > 0.45 && appear > 0.01 && uDuckScale > 0.01;

  // GPU multi-scale mipmap halo behind duck
  if (uDuckScale > 0.01 && dd < 0.95) {
    vec4 h1 = sampleDuck(pos, 3.2);
    vec4 h2 = sampleDuck(pos, 4.8);
    float l1 = max(h1.r, max(h1.g, h1.b));
    float l2 = max(h2.r, max(h2.g, h2.b));
    float haze = (l1 * h1.a * 0.65 + l2 * h2.a * 0.45) * (1.0 - dd) * uDuckScale;
    outCol += vec3(0.93, 0.56, 0.18) * clamp(haze * 0.44, 0.0, 0.38);
  }

  // Solid sculptural 3D base underneath halftone glyphs so background QUACK never bleeds through the duck
  if (dkSolid.a > 0.35 && uDuckScale > 0.01 && dd < 0.75) {
    float maskA = smoothstep(0.35, 0.65, dkSolid.a) * (1.0 - smoothstep(0.0, 0.75, dd)) * uDuckScale;
    float mxS = max(dkSolid.r, max(dkSolid.g, dkSolid.b));
    float lumS = clamp(pow(mxS, 0.80), 0.0, 1.0);
    vec3 baseCol = duckPartColor(dkSolid, max(lumS, 0.08));
    outCol = mix(outCol, vec3(0.04, 0.025, 0.018), maskA * 0.90);
    outCol = mix(outCol, baseCol * 0.90, maskA * 0.74);
  }

  float flick = anim * (0.04 * sin(uTime * 1.3 + r1 * 40.0)) + (r2 - 0.5) * 0.07;

  if (isDuck) {
    float mx = max(dk.r, max(dk.g, dk.b));
    float lum = clamp(pow(mx, 0.76) + flick, 0.0, 1.0);
    if (lum > 0.04) {
      float glyphScale = mix(0.72, 1.08, appear);
      float cov = glyphCoverage(local, lum, r1, glyphScale);
      vec3 gCol = duckPartColor(dk, min(1.0, lum * 1.08 + 0.04));
      float alpha = cov * appear * mix(0.90, 1.0, smoothstep(0.05, 0.25, lum)) * (1.0 - uAway * 0.82) * uDuckScale;
      outCol = mix(outCol, gCol, clamp(alpha, 0.0, 1.0));
    }
  } else if (uHeroWeight > 0.01) {
    float reedLum = marshReeds(center / uSize, uTime);
    if (reedLum > 0.08) {
      float reedAppear = clamp(((1.0 - uDisperse) - r2 * 0.6) / 0.4, 0.0, 1.0);
      float lum = clamp(reedLum + flick * 0.7, 0.0, 1.0);
      float cov = glyphCoverage(local, lum, r1, mix(0.65, 0.95, reedAppear));
      vec3 rDark = vec3(0.24, 0.18, 0.13);
      vec3 rMid = vec3(0.58, 0.46, 0.33);
      vec3 rHi = vec3(0.84, 0.70, 0.52);
      vec3 rCol = mix(rDark, rMid, smoothstep(0.1, 0.6, lum));
      rCol = mix(rCol, rHi, smoothstep(0.6, 0.95, lum));
      float rAlpha = cov * reedAppear * uHeroWeight * (0.40 + 0.42 * lum);
      outCol = mix(outCol, rCol, clamp(rAlpha, 0.0, 1.0));
    }
  }

  // Tactile analog film grain
  float grain = (h21(gl_FragCoord.xy + fract(uTime) * 19.0 * anim) - 0.5) * 0.018;
  outCol += grain;

  gl_FragColor = vec4(max(outCol, vec3(0.0)), 1.0);
}
`;

// ---------- Pass 3: 3D Volumetric Knowledge Constellation & Particles ----------

const NODE_VERT = /* glsl */ `
attribute float aSize;
attribute vec3 aColor;
attribute float aAlpha;
attribute float aRing;

uniform float uPixelRatio;

varying vec3 vColor;
varying float vAlpha;
varying float vRing;

void main() {
  vColor = aColor;
  vAlpha = aAlpha;
  vRing = aRing;
  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
  float dist = max(-mvPosition.z, 1.5);
  gl_PointSize = aSize * uPixelRatio * (14.0 / dist);
  gl_Position = projectionMatrix * mvPosition;
}
`;

const NODE_FRAG = /* glsl */ `
precision highp float;

varying vec3 vColor;
varying float vAlpha;
varying float vRing;

const float TAU = 6.2831853;

void main() {
  if (vAlpha <= 0.01) discard;
  vec2 p = gl_PointCoord - 0.5;
  float r = length(p);
  if (r > 0.5) discard;

  float glow = exp(-r * r * 18.0) * 0.40;
  float core = 1.0 - smoothstep(0.14, 0.21, r);
  float ringBand = (1.0 - smoothstep(0.0, 0.032, abs(r - 0.34))) * 0.85;
  float ang = atan(p.y, p.x);
  float normAng = fract((ang + 1.5707963) / TAU);
  float arcMask = step(normAng, vRing);

  float intensity = core + ringBand * arcMask + glow;
  vec3 col = mix(vColor, vec3(0.96, 0.92, 0.84), core * 0.22);
  gl_FragColor = vec4(col * intensity, clamp(intensity * vAlpha, 0.0, 1.0));
}
`;

const LINE_VERT = /* glsl */ `
attribute vec3 aColor;
attribute float aAlpha;

varying vec3 vColor;
varying float vAlpha;

void main() {
  vColor = aColor;
  vAlpha = aAlpha;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const LINE_FRAG = /* glsl */ `
precision highp float;

varying vec3 vColor;
varying float vAlpha;

void main() {
  if (vAlpha <= 0.005) discard;
  gl_FragColor = vec4(vColor, vAlpha);
}
`;

const SPARK_VERT = /* glsl */ `
attribute float aSize;
attribute vec3 aColor;
attribute float aAlpha;

uniform float uPixelRatio;

varying vec3 vColor;
varying float vAlpha;

void main() {
  vColor = aColor;
  vAlpha = aAlpha;
  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
  float dist = max(-mvPosition.z, 1.5);
  gl_PointSize = aSize * uPixelRatio * (13.0 / dist);
  gl_Position = projectionMatrix * mvPosition;
}
`;

const SPARK_FRAG = /* glsl */ `
precision highp float;

varying vec3 vColor;
varying float vAlpha;

void main() {
  if (vAlpha <= 0.005) discard;
  float r = length(gl_PointCoord - 0.5);
  if (r > 0.5) discard;
  float falloff = exp(-r * r * 20.0);
  float core = 1.0 - smoothstep(0.08, 0.2, r);
  float a = (falloff * 0.7 + core * 0.55) * vAlpha;
  gl_FragColor = vec4(vColor * a, a);
}
`;

const COL_AMBER = new THREE.Color("#edb966");
const COL_SAGE = new THREE.Color("#8fc79a");
const COL_SKY = new THREE.Color("#86a9c9");
const COL_ROSE = new THREE.Color("#ef8c7a");
const COL_VIOLET = new THREE.Color("#9d8ec4");
const COL_DIM = new THREE.Color("#736e63");
const COL_PAPER = new THREE.Color("#e8e4dc");

const STATE_PALETTE = [COL_SAGE, COL_SKY, COL_ROSE, COL_AMBER, COL_VIOLET];

export function createLandingStage(
  canvas: HTMLCanvasElement,
  stateRef: { current: LandingStageMutableState },
  onAnchor: (anchor: DuckScreenAnchor) => void,
): LandingStage {
  const renderer = new THREE.WebGLRenderer({
    canvas,
    alpha: false,
    antialias: true,
    powerPreference: "high-performance",
  });
  renderer.setClearColor(0x080504, 1);
  renderer.autoClear = false;

  // ---------- Pass 1: Articulated 3D Duck Rig ----------
  const rig = createDuckRig();
  const duckCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 60);
  // samples: 0 ensures generateMipmaps works reliably across all WebGL2/ANGLE drivers
  const duckTarget = new THREE.WebGLRenderTarget(2, 2, {
    samples: 0,
    generateMipmaps: true,
    minFilter: THREE.LinearMipmapLinearFilter,
    magFilter: THREE.LinearFilter,
  });

  // Preallocate pointer trail uniform array
  const trailUniforms = Array.from({ length: TRAIL_LEN }, () => new THREE.Vector4(0.5, 0.5, 0, 1));

  // ---------- Background Monumental QUACK Canvas Texture ----------
  let disposed = false;
  const brandCanvas = document.createElement("canvas");
  const brandCtx = brandCanvas.getContext("2d");
  const makeBrandTex = () => {
    const tex = new THREE.CanvasTexture(brandCanvas);
    tex.minFilter = THREE.LinearFilter;
    tex.magFilter = THREE.LinearFilter;
    tex.generateMipmaps = false;
    return tex;
  };
  let brandTex = makeBrandTex();

  // ---------- Pass 2: Full-Screen Shader Quad ----------
  const quadMaterial = new THREE.ShaderMaterial({
    vertexShader: QUAD_VERT,
    fragmentShader: QUAD_FRAG,
    uniforms: {
      uDuck: { value: duckTarget.texture },
      uBrandTex: { value: brandTex },
      uSize: { value: new THREE.Vector2(1, 1) },
      uRes: { value: new THREE.Vector2(1, 1) },
      uCell: { value: 5.2 },
      uTime: { value: 0 },
      uDisperse: { value: 1 },
      uAway: { value: 0 },
      uHeroWeight: { value: 1 },
      uDuckScale: { value: 1 },
      uTickOpacity: { value: 0.9 },
      uDuckCenter: { value: new THREE.Vector2(0.5, 0.48) },
      uPointer: { value: new THREE.Vector2(0, 0) },
      uReduced: { value: 0 },
      uTrail: { value: trailUniforms },
    },
    depthTest: false,
    depthWrite: false,
  });

  const drawBrandTexture = (w: number, h: number) => {
    if (!brandCtx || disposed) return;
    const portrait = w < h;
    const cw = Math.max(2, Math.min(1600, Math.round(w)));
    const ch = Math.max(2, Math.min(1000, Math.round(h)));
    if (brandCanvas.width !== cw || brandCanvas.height !== ch) {
      brandCanvas.width = cw;
      brandCanvas.height = ch;
      // Recreate CanvasTexture on resize so WebGL2 texStorage2D never preserves stale oversized regions
      brandTex.dispose();
      brandTex = makeBrandTex();
      quadMaterial.uniforms.uBrandTex.value = brandTex;
    }
    brandCtx.clearRect(0, 0, cw, ch);

    const rawArchivo =
      typeof document !== "undefined"
        ? getComputedStyle(document.documentElement).getPropertyValue("--font-archivo").trim()
        : "";
    const family = rawArchivo || "Archivo, system-ui, sans-serif";
    brandCtx.font = `700 100px ${family}`;
    const refW = Math.max(1, brandCtx.measureText("QUACK").width);
    const fontSize = Math.min((cw * 0.90) / ((refW * 1.06) / 100), ch * (portrait ? 0.24 : 0.44));

    brandCtx.save();
    brandCtx.font = `700 ${fontSize.toFixed(1)}px ${family}`;
    brandCtx.textAlign = "center";
    brandCtx.textBaseline = "middle";
    brandCtx.fillStyle = "#ffffff";
    brandCtx.translate(cw * 0.5, ch * (portrait ? 0.31 : 0.40));
    brandCtx.scale(1.06, 1.0);
    brandCtx.fillText("QUACK", 0, 0);
    brandCtx.restore();
    brandTex.needsUpdate = true;
  };
  const quadGeo = new THREE.PlaneGeometry(2, 2);
  const quadMesh = new THREE.Mesh(quadGeo, quadMaterial);
  quadMesh.frustumCulled = false;
  const quadScene = new THREE.Scene();
  quadScene.add(quadMesh);
  const quadCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

  // ---------- Pass 3: 3D Volumetric Knowledge Constellation & Particles ----------
  const worldScene = new THREE.Scene();
  const worldCamera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
  worldCamera.position.set(0, 0, 14);

  const rand = rng(4242);

  // 5 Chapter Hubs
  const HUB_COUNT = 5;
  const hubGeo = new THREE.TorusGeometry(0.42, 0.015, 12, 56);
  const hubInnerGeo = new THREE.TorusGeometry(0.28, 0.008, 8, 44);
  type HubData = {
    group: THREE.Group;
    outerMat: THREE.MeshBasicMaterial;
    innerMat: THREE.MeshBasicMaterial;
    px: number;
    py: number;
    pz: number;
    phase: number;
    scale: number;
  };
  const hubs: HubData[] = [];
  for (let h = 0; h < HUB_COUNT; h++) {
    const group = new THREE.Group();
    const outerMat = new THREE.MeshBasicMaterial({
      color: COL_AMBER.clone(),
      transparent: true,
      opacity: 0.3,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const innerMat = new THREE.MeshBasicMaterial({
      color: COL_PAPER.clone(),
      transparent: true,
      opacity: 0.18,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    group.add(new THREE.Mesh(hubGeo, outerMat));
    group.add(new THREE.Mesh(hubInnerGeo, innerMat));
    worldScene.add(group);
    hubs.push({
      group,
      outerMat,
      innerMat,
      px: 0,
      py: 0,
      pz: 0,
      phase: h * 1.25 + rand() * 0.5,
      scale: 1,
    });
  }

  // 44 Topic Nodes
  const NODE_COUNT = 44;
  type TopicNodeData = {
    hub: number;
    orbitAngle: number;
    orbitRadius: number;
    orbitZ: number;
    px: number;
    py: number;
    pz: number;
    vx: number;
    vy: number;
    vz: number;
    baseColor: THREE.Color;
    curColor: THREE.Color;
    alpha: number;
    size: number;
    ring: number;
    phase: number;
  };

  const topicNodes: TopicNodeData[] = [];
  const nodePositions = new Float32Array(NODE_COUNT * 3);
  const nodeColors = new Float32Array(NODE_COUNT * 3);
  const nodeAlphas = new Float32Array(NODE_COUNT);
  const nodeSizes = new Float32Array(NODE_COUNT);
  const nodeRings = new Float32Array(NODE_COUNT);

  for (let i = 0; i < NODE_COUNT; i++) {
    const hub = i % HUB_COUNT;
    const idxInHub = Math.floor(i / HUB_COUNT);
    const orbitAngle = (idxInHub / Math.ceil(NODE_COUNT / HUB_COUNT)) * Math.PI * 2 + hub * 0.7;
    const orbitRadius = 0.85 + (idxInHub % 3) * 0.45 + rand() * 0.25;
    const orbitZ = (rand() - 0.5) * 1.3;
    const stateIdx = Math.floor(rand() * STATE_PALETTE.length);
    const baseColor = STATE_PALETTE[stateIdx].clone();
    const ring = stateIdx === 0 ? 0.92 : stateIdx === 1 ? 0.65 : stateIdx === 2 ? 0.42 : stateIdx === 3 ? 0.78 : 0.28;

    topicNodes.push({
      hub,
      orbitAngle,
      orbitRadius,
      orbitZ,
      px: (rand() - 0.5) * 9,
      py: (rand() - 0.5) * 5,
      pz: (rand() - 0.5) * 3,
      vx: 0,
      vy: 0,
      vz: 0,
      baseColor,
      curColor: baseColor.clone(),
      alpha: 0.65,
      size: 23 + rand() * 13,
      ring,
      phase: rand() * Math.PI * 2,
    });
    nodeSizes[i] = topicNodes[i].size;
    nodeRings[i] = ring;
  }

  const nodeGeo = new THREE.BufferGeometry();
  const nodePosAttr = new THREE.BufferAttribute(nodePositions, 3);
  const nodeColAttr = new THREE.BufferAttribute(nodeColors, 3);
  const nodeAlphaAttr = new THREE.BufferAttribute(nodeAlphas, 1);
  const nodeSizeAttr = new THREE.BufferAttribute(nodeSizes, 1);
  const nodeRingAttr = new THREE.BufferAttribute(nodeRings, 1);
  nodeGeo.setAttribute("position", nodePosAttr);
  nodeGeo.setAttribute("aColor", nodeColAttr);
  nodeGeo.setAttribute("aAlpha", nodeAlphaAttr);
  nodeGeo.setAttribute("aSize", nodeSizeAttr);
  nodeGeo.setAttribute("aRing", nodeRingAttr);

  const nodeMat = new THREE.ShaderMaterial({
    vertexShader: NODE_VERT,
    fragmentShader: NODE_FRAG,
    uniforms: { uPixelRatio: { value: 1 } },
    transparent: true,
    depthTest: false,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const nodePoints = new THREE.Points(nodeGeo, nodeMat);
  nodePoints.frustumCulled = false;
  worldScene.add(nodePoints);

  type EdgeDef = { type: "hub" | "topic"; a: number; b: number; weight: number };
  const edges: EdgeDef[] = [];
  for (let h = 0; h < HUB_COUNT - 1; h++) {
    edges.push({ type: "hub", a: h, b: h + 1, weight: 0.85 });
  }
  for (let i = 0; i < NODE_COUNT; i++) {
    const nextInHub = i + HUB_COUNT;
    if (nextInHub < NODE_COUNT) {
      edges.push({ type: "topic", a: i, b: nextInHub, weight: 0.65 });
    }
    if (i % 4 === 0) {
      edges.push({ type: "topic", a: i, b: (i + 7) % NODE_COUNT, weight: 0.42 });
    }
  }

  const EDGE_COUNT = edges.length;
  const linePositions = new Float32Array(EDGE_COUNT * 2 * 3);
  const lineColors = new Float32Array(EDGE_COUNT * 2 * 3);
  const lineAlphas = new Float32Array(EDGE_COUNT * 2);

  const lineGeo = new THREE.BufferGeometry();
  const linePosAttr = new THREE.BufferAttribute(linePositions, 3);
  const lineColAttr = new THREE.BufferAttribute(lineColors, 3);
  const lineAlphaAttr = new THREE.BufferAttribute(lineAlphas, 1);
  lineGeo.setAttribute("position", linePosAttr);
  lineGeo.setAttribute("aColor", lineColAttr);
  lineGeo.setAttribute("aAlpha", lineAlphaAttr);

  const lineMat = new THREE.ShaderMaterial({
    vertexShader: LINE_VERT,
    fragmentShader: LINE_FRAG,
    transparent: true,
    depthTest: false,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const edgeLines = new THREE.LineSegments(lineGeo, lineMat);
  edgeLines.frustumCulled = false;
  worldScene.add(edgeLines);

  const PULSE_COUNT = 14;
  const DUST_COUNT = 200;
  const BURST_COUNT = 48;
  const TOTAL_SPARKS = PULSE_COUNT + DUST_COUNT + BURST_COUNT;

  const sparkPositions = new Float32Array(TOTAL_SPARKS * 3);
  const sparkColors = new Float32Array(TOTAL_SPARKS * 3);
  const sparkAlphas = new Float32Array(TOTAL_SPARKS);
  const sparkSizes = new Float32Array(TOTAL_SPARKS);

  const pulses = Array.from({ length: PULSE_COUNT }, (_, i) => ({
    edgeIdx: (i * 5) % EDGE_COUNT,
    t: rand(),
    speed: 0.22 + rand() * 0.26,
  }));

  const dust = Array.from({ length: DUST_COUNT }, () => ({
    bx: (rand() - 0.5) * 18,
    by: (rand() - 0.5) * 11,
    bz: -6 + rand() * 9,
    phase: rand() * Math.PI * 2,
    speed: 0.2 + rand() * 0.45,
    size: 5 + rand() * 7,
    amber: rand() < 0.65,
  }));

  type BurstParticle = {
    active: boolean;
    x: number;
    y: number;
    z: number;
    vx: number;
    vy: number;
    vz: number;
    age: number;
    life: number;
    blue: boolean;
  };
  const bursts: BurstParticle[] = Array.from({ length: BURST_COUNT }, () => ({
    active: false,
    x: 0,
    y: 0,
    z: 0,
    vx: 0,
    vy: 0,
    vz: 0,
    age: 0,
    life: 1,
    blue: false,
  }));

  const sparkGeo = new THREE.BufferGeometry();
  const sparkPosAttr = new THREE.BufferAttribute(sparkPositions, 3);
  const sparkColAttr = new THREE.BufferAttribute(sparkColors, 3);
  const sparkAlphaAttr = new THREE.BufferAttribute(sparkAlphas, 1);
  const sparkSizeAttr = new THREE.BufferAttribute(sparkSizes, 1);
  sparkGeo.setAttribute("position", sparkPosAttr);
  sparkGeo.setAttribute("aColor", sparkColAttr);
  sparkGeo.setAttribute("aAlpha", sparkAlphaAttr);
  sparkGeo.setAttribute("aSize", sparkSizeAttr);

  const sparkMat = new THREE.ShaderMaterial({
    vertexShader: SPARK_VERT,
    fragmentShader: SPARK_FRAG,
    uniforms: { uPixelRatio: { value: 1 } },
    transparent: true,
    depthTest: false,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const sparkPoints = new THREE.Points(sparkGeo, sparkMat);
  sparkPoints.frustumCulled = false;
  worldScene.add(sparkPoints);

  // ---------- Viewport & Context State ----------
  let width = canvas.clientWidth || 1280;
  let height = canvas.clientHeight || 800;
  let pixelRatio = 1;
  let lost = false;

  const onLost = (e: Event) => {
    e.preventDefault();
    lost = true;
  };
  const onRestored = () => {
    lost = false;
  };
  canvas.addEventListener("webglcontextlost", onLost);
  canvas.addEventListener("webglcontextrestored", onRestored);

  const resize = (w: number, h: number, dpr?: number) => {
    if (disposed) return;
    width = Math.max(1, w);
    height = Math.max(1, h);
    const maxDpr = width < 768 ? 1.5 : 1.75;
    pixelRatio = Math.min(dpr ?? (typeof window !== "undefined" ? window.devicePixelRatio : 1), maxDpr);
    renderer.setPixelRatio(pixelRatio);
    renderer.setSize(width, height, false);

    // High-resolution non-MSAA duck target so 4.5-6.0px stipple glyphs read razor-sharp
    const scale = width < 768 ? 0.8 : 0.85;
    const tw = Math.max(4, Math.min(1440, Math.round(width * scale)));
    const th = Math.max(4, Math.min(1080, Math.round(height * scale)));
    duckTarget.setSize(tw, th);

    const buf = renderer.getDrawingBufferSize(new THREE.Vector2());
    quadMaterial.uniforms.uSize.value.set(width, height);
    quadMaterial.uniforms.uRes.value.copy(buf);
    const cell = THREE.MathUtils.clamp(Math.round((Math.max(width, height) / 260) * 2) / 2, 4.5, 6.0);
    quadMaterial.uniforms.uCell.value = cell;
    nodeMat.uniforms.uPixelRatio.value = pixelRatio;
    sparkMat.uniforms.uPixelRatio.value = pixelRatio;

    worldCamera.aspect = width / height;
    worldCamera.updateProjectionMatrix();
    drawBrandTexture(width, height);
  };
  resize(width, height);
  if (typeof document !== "undefined" && document.fonts) {
    void document.fonts.ready.then(() => {
      if (!disposed) drawBrandTexture(width, height);
    });
  }

  // ---------- Duck Autonomous Choreography State ----------
  const pose: DuckPose = {
    x: 0,
    facing: FACING_FRONT_R,
    step: 0.25,
    walk: 0,
    stretch: 0.15,
    headYaw: 0,
    headPitch: 0,
    jaw: 0,
    flap: 0,
    time: 0,
  };
  const goal = {
    facing: FACING_FRONT_R,
    headYaw: 0,
    headPitch: 0,
    stretch: 0.15,
    headRate: 8,
  };
  let introDone = false;
  let initialQuacked = false;
  let quackT = -9;
  let flapUntil = 0;
  let turnUntil = 0;
  let nextLook = 2.8;
  let nextTurn = 9.5;
  let shockwaveT = -9;
  let shockwaveOrigin = { x: 0, y: 0, z: 0 };
  let smoothPtrX = 0;
  let smoothPtrY = 0;
  let prevPtrUvX = 0.5;
  let prevPtrUvY = 0.5;
  let trailHead = 0;
  let trailTimer = 0;

  const tmpV = new THREE.Vector3();
  const tmpBeak = new THREE.Vector3();
  const tmpCol = new THREE.Color();

  const spawnBurst = (worldX: number, worldY: number, worldZ: number) => {
    for (let i = 0; i < BURST_COUNT; i++) {
      const b = bursts[i];
      const ang = (i / BURST_COUNT) * Math.PI * 2 + (Math.random() - 0.5) * 0.35;
      const spd = 1.5 + Math.random() * 3.5;
      b.active = true;
      b.x = worldX + (Math.random() - 0.5) * 0.12;
      b.y = worldY + (Math.random() - 0.5) * 0.12;
      b.z = worldZ + (Math.random() - 0.5) * 0.3;
      b.vx = Math.cos(ang) * spd;
      b.vy = Math.sin(ang) * spd * 0.85 + 0.55;
      b.vz = (Math.random() - 0.5) * 1.5;
      b.age = 0;
      b.life = 0.65 + Math.random() * 0.75;
      b.blue = i % 3 === 0;
    }
  };

  let lastClock = 0;

  const triggerQuackInternal = (timeSec: number, emitSparks = true) => {
    quackT = timeSec;
    flapUntil = timeSec + 0.68;
    goal.stretch = 0.95;
    shockwaveT = timeSec;

    rig.apply(pose);
    rig.beakTip(tmpBeak);
    tmpBeak.project(duckCamera);
    const halfWorldH = Math.tan((worldCamera.fov * Math.PI) / 360) * worldCamera.position.z;
    const halfWorldW = halfWorldH * worldCamera.aspect;
    const wx = tmpBeak.x * halfWorldW + worldCamera.position.x;
    const wy = tmpBeak.y * halfWorldH + worldCamera.position.y;
    shockwaveOrigin = { x: wx, y: wy, z: 0 };
    if (emitSparks) spawnBurst(wx, wy, 0.2);
  };

  return {
    resize,
    triggerQuack() {
      triggerQuackInternal(lastClock, true);
    },
    render(timeSec, dt, pointerX, pointerY, pointerActive, reduced) {
      if (disposed || lost) return;
      lastClock = timeSec;

      const state = stateRef.current;
      const aspect = width / height;
      const portrait = aspect < 1.0;
      const desktopWide = aspect >= 1.15;

      smoothPtrX = approach(smoothPtrX, pointerActive && !reduced ? pointerX : 0, 7, dt);
      smoothPtrY = approach(smoothPtrY, pointerActive && !reduced ? pointerY : 0, 7, dt);

      // Update 20-point liquid obsidian pointer trail ring buffer
      const curUvX = pointerX * 0.5 + 0.5;
      const curUvY = 0.5 - pointerY * 0.5;
      if (dt > 0 && !reduced) {
        for (let i = 0; i < TRAIL_LEN; i++) {
          const item = trailUniforms[i];
          if (item.z > 0.001) {
            item.w = Math.min(1, item.w + dt * 1.15);
            item.z *= Math.exp(-2.6 * dt);
          }
        }
        if (pointerActive) {
          const moveDist = Math.hypot(curUvX - prevPtrUvX, curUvY - prevPtrUvY);
          trailTimer += dt;
          if (moveDist > 0.004 || trailTimer > 0.032) {
            const strength = Math.min(1.0, 0.25 + moveDist * 14.0);
            trailUniforms[trailHead].set(curUvX, curUvY, strength, 0);
            trailHead = (trailHead + 1) % TRAIL_LEN;
            prevPtrUvX = curUvX;
            prevPtrUvY = curUvY;
            trailTimer = 0;
          }
        }
      }

      // Scroll choreography weights
      const scrollVh = reduced ? state.scrollVh : state.smoothScrollVh;
      const scrollProg = reduced ? state.scrollProgress : state.smoothScrollProgress;
      const velocity = reduced ? 0 : state.velocity;

      const heroFadeOut = smoothstep(0.18, 0.82, scrollVh);
      const finaleReturn = smoothstep(0.80, 0.95, scrollProg);
      const duckAway = Math.min(heroFadeOut, 1.0 - finaleReturn);
      const duckScale = clamp01(1.0 - duckAway);
      const heroWeight = 1.0 - smoothstep(0.08, 0.62, scrollVh);
      const introProgress = reduced ? 1 : clamp01((timeSec - 0.1) / 1.3);
      const introDisperse = Math.pow(1.0 - introProgress, 3);

      // Tick field prominence: strong on Hero, calm ambient instrument marks mid-page
      const tickOpacity = lerp(0.32, 0.92, Math.max(heroWeight, finaleReturn * 0.6));

      // ---------- Update 3D Duck Camera & Pose ----------
      const duckPx = portrait
        ? THREE.MathUtils.clamp(width * 0.54, 180, 240)
        : THREE.MathUtils.clamp(width * 0.30, 290, Math.min(520, height * 0.58));
      const u = 3.4 / duckPx;
      const vw = width * u;
      const vh = height * u;

      // Hero: centered at orbital HUD middle, leaving bottom clean for clear display title.
      // Finale: right-aligned and lifted cleanly above footer links bar.
      const targetXFrac = lerp(0.5, desktopWide ? 0.75 : 0.5, finaleReturn);
      const targetFootFrac = lerp(portrait ? 0.44 : 0.62, desktopWide ? 0.66 : 0.68, finaleReturn);

      const cx = -(targetXFrac - 0.5) * vw;
      const cy = GROUND - (0.5 - targetFootFrac) * vh;

      duckCamera.left = -vw / 2;
      duckCamera.right = vw / 2;
      duckCamera.top = vh / 2;
      duckCamera.bottom = -vh / 2;
      duckCamera.position.set(cx, cy + 10 * Math.tan(0.16), 10);
      duckCamera.lookAt(cx, cy, 0);
      duckCamera.updateProjectionMatrix();

      if (!reduced) {
        if (!introDone) {
          if (timeSec > 0.45) {
            goal.stretch = 0.82;
            goal.headRate = 9;
            goal.headYaw = Math.max(-1.8, Math.min(1.8, 0.12));
          }
          if (!initialQuacked && timeSec > 1.05) {
            initialQuacked = true;
            triggerQuackInternal(timeSec, true);
          }
          if (timeSec > 2.3) {
            introDone = true;
            goal.stretch = 0.16;
            goal.headYaw = 0;
          }
        } else {
          if (finaleReturn > 0.45 && desktopWide) {
            goal.facing = FACING_FINALE_L;
            goal.headYaw = Math.max(-1.6, Math.min(1.6, -0.2 + smoothPtrX * 0.3));
            goal.headPitch = smoothPtrY * 0.12;
            goal.stretch = 0.22;
          } else {
            if (timeSec > nextTurn && pose.stretch < 0.55) {
              goal.facing = -Math.PI - goal.facing;
              goal.headYaw = 0;
              turnUntil = timeSec + 1.05;
              nextTurn = timeSec + 7.5 + (Math.sin(timeSec) * 0.5 + 0.5) * 4.5;
            }
            if (timeSec > nextLook) {
              nextLook = timeSec + 2.2 + (Math.cos(timeSec * 1.7) * 0.5 + 0.5) * 2.5;
              const dir = Math.sin(timeSec * 2.3) >= 0 ? RIGHT_LOOK : LEFT_LOOK;
              const targetYaw = dir + Math.sin(timeSec * 3.1) * 0.26 - goal.facing;
              goal.headYaw = Math.max(-1.9, Math.min(1.9, targetYaw));
              goal.headPitch = Math.sin(timeSec * 1.9) * 0.11;
              goal.stretch = timeSec - quackT < 1.2 ? 0.82 : 0.16;
            }
            if (pointerActive) {
              goal.headYaw = approach(goal.headYaw, smoothPtrX * 0.48, 4, dt);
              goal.headPitch = approach(goal.headPitch, smoothPtrY * 0.16, 4, dt);
            }
          }
        }

        pose.facing = approach(pose.facing, goal.facing, 3.4, dt);
        pose.walk = approach(pose.walk, timeSec < turnUntil ? 0.2 : 0, 7, dt);
        if (pose.walk > 0.02) pose.step = (pose.step + dt / 0.36) % 1;
        pose.headYaw = approach(pose.headYaw, goal.headYaw, goal.headRate, dt);
        pose.headPitch = approach(pose.headPitch, goal.headPitch, 7, dt);
        pose.stretch = approach(pose.stretch, goal.stretch, 4.5, dt);
        pose.flap = approach(pose.flap, timeSec < flapUntil ? 1 : 0, 10, dt);
        const qAge = timeSec - quackT;
        pose.jaw = qAge >= 0 && qAge < 0.45 ? Math.sin((qAge / 0.45) * Math.PI) : 0;
        pose.time = timeSec;
      } else {
        pose.facing = finaleReturn > 0.5 && desktopWide ? FACING_FINALE_L : FACING_FRONT_R;
        pose.stretch = 0.25;
        pose.headYaw = 0;
        pose.headPitch = 0;
        pose.jaw = 0;
        pose.flap = 0;
      }

      rig.apply(pose);

      rig.headCenter(tmpV).project(duckCamera);
      rig.beakTip(tmpBeak).project(duckCamera);
      const centerX = targetXFrac * width;
      const centerY = targetFootFrac * height - duckPx * 0.30;
      const beakX = (tmpBeak.x + 1) * 0.5 * width;
      const beakY = (1 - tmpBeak.y) * 0.5 * height;
      onAnchor({
        x: centerX,
        y: centerY,
        size: duckPx,
        beakX,
        beakY,
        visible: duckScale > 0.35 && heroWeight > 0.2,
      });

      if (duckScale > 0.01) {
        renderer.setRenderTarget(duckTarget);
        renderer.setClearColor(0x000000, 0);
        renderer.clear();
        renderer.render(rig.scene, duckCamera);
      }

      // ---------- Pass 2: Full-Screen Shader Quad ----------
      quadMaterial.uniforms.uTime.value = timeSec;
      quadMaterial.uniforms.uDisperse.value = introDisperse;
      quadMaterial.uniforms.uAway.value = duckAway;
      quadMaterial.uniforms.uHeroWeight.value = heroWeight;
      quadMaterial.uniforms.uDuckScale.value = duckScale;
      quadMaterial.uniforms.uTickOpacity.value = tickOpacity;
      quadMaterial.uniforms.uDuckCenter.value.set(targetXFrac, targetFootFrac - (duckPx * 0.30) / height);
      quadMaterial.uniforms.uPointer.value.set(smoothPtrX, smoothPtrY);
      quadMaterial.uniforms.uReduced.value = reduced ? 1 : 0;

      renderer.setRenderTarget(null);
      renderer.clear();
      renderer.render(quadScene, quadCamera);

      // ---------- Pass 3: 3D Knowledge Constellation & Particles ----------
      const camX = smoothPtrX * 0.68 + Math.sin(scrollProg * Math.PI * 1.4) * 0.58;
      const camY = smoothPtrY * 0.44 - Math.sin(scrollProg * Math.PI) * 0.32;
      const camZ = 14.0 - Math.sin(scrollProg * Math.PI) * 0.9;
      worldCamera.position.set(camX, camY, camZ);
      worldCamera.lookAt(smoothPtrX * 0.14, -velocity * 0.15, 0);

      const halfWorldH = Math.tan((worldCamera.fov * Math.PI) / 360) * worldCamera.position.z;
      const halfWorldW = halfWorldH * worldCamera.aspect;

      const wHero = 1.0 - smoothstep(0.06, 0.22, scrollProg);
      const wFlow = smoothstep(0.12, 0.26, scrollProg) * (1.0 - smoothstep(0.46, 0.60, scrollProg));
      const wCompare = smoothstep(0.46, 0.60, scrollProg) * (1.0 - smoothstep(0.68, 0.80, scrollProg));
      const wTools = smoothstep(0.66, 0.78, scrollProg) * (1.0 - finaleReturn);
      const wFinale = finaleReturn;

      const flowStep: FlowStep = state.flowStep;
      const compareSide: CompareSide = state.compareSide;
      const hovFeat = state.hoveredFeature;

      let edgeFactor = 1.0;
      let saturationFactor = 1.0;
      let nodeSpeed = 1.0;

      if (wFlow > 0.15) {
        if (flowStep === 0) {
          edgeFactor = lerp(edgeFactor, 0.45, wFlow);
          nodeSpeed = lerp(nodeSpeed, 0.65, wFlow);
        } else if (flowStep === 2) {
          nodeSpeed = lerp(nodeSpeed, 1.45, wFlow);
        }
      }
      if (wCompare > 0.15 && compareSide === "chat") {
        edgeFactor = lerp(edgeFactor, 0.18, wCompare);
        saturationFactor = lerp(saturationFactor, 0.25, wCompare);
      }

      const densityDim = lerp(1.0, 0.42, wTools) * lerp(1.0, 0.72, wFlow);

      // Update 5 Chapter Hubs
      for (let h = 0; h < HUB_COUNT; h++) {
        const hub = hubs[h];
        const frac = (h / (HUB_COUNT - 1)) * 2 - 1;

        // Formation A: Hero outer flanking periphery (cleanly outside orbital HUD, QUACK backdrop & duck)
        const side = h < 2 ? -1 : h > 2 ? 1 : h % 2 === 0 ? -1 : 1;
        const heroX = side * halfWorldW * (0.64 + (h === 0 || h === 4 ? 0.16 : 0.04));
        const heroY = halfWorldH * (0.28 - Math.abs(frac) * 0.26);
        const heroZ = -1.6 - (h % 2) * 0.9;

        // Formation B: Flow 3-step visual choreography
        const flowAng = (h / HUB_COUNT) * Math.PI * 2 + timeSec * (flowStep === 2 ? 0.18 : 0.09);
        const flowCenterX = desktopWide ? halfWorldW * 0.18 : 0;
        let flowX = flowCenterX + Math.cos(flowAng) * 2.6;
        let flowY = Math.sin(flowAng) * 1.75;
        let flowZ = Math.sin(flowAng * 2) * 1.1 - 1.5;
        if (flowStep === 0) {
          flowX = flowCenterX + Math.cos(flowAng + Math.sin(timeSec * 0.35 + h) * 0.2) * (2.6 + Math.sin(timeSec * 0.45 + h) * 0.7);
          flowY = Math.sin(flowAng) * 2.0;
          flowZ = -2.0 + (h % 2) * 0.6;
        } else if (flowStep === 2) {
          flowX = flowCenterX + Math.cos(flowAng) * 1.9;
          flowY = Math.sin(flowAng) * 1.35;
          flowZ = -1.2 + Math.cos(flowAng) * 0.5;
        }

        // Formation C: Compare 50/50 chat thread vs Quack constellation
        let compX: number;
        let compY: number;
        let compZ: number;
        if (compareSide === "chat") {
          compX = (desktopWide ? -halfWorldW * 0.32 : 0) + (h - 2) * 0.9;
          compY = -0.3 + h * 0.18;
          compZ = -h * 1.5;
        } else {
          const compAng = (h / HUB_COUNT) * Math.PI * 2 + timeSec * 0.12;
          compX = (desktopWide ? halfWorldW * 0.34 : 0) + Math.cos(compAng) * 2.4;
          compY = Math.sin(compAng) * 1.7;
          compZ = Math.sin(compAng * 2) * 1.1 - 1.2;
        }

        // Formation D: Wide peripheral framing for hairline Features Index
        const midSide = h % 2 === 0 ? -1 : 1;
        const toolsX = midSide * halfWorldW * (0.76 + (h % 3) * 0.08);
        const toolsY = (h - 2) * halfWorldH * 0.32;
        const toolsZ = -2.2 - (h % 2) * 1.0;

        // Formation E: Finale mastered halo around lifted returning duck
        const finAng = -0.35 + h * 0.55 + timeSec * 0.07;
        const finCenterX = desktopWide ? halfWorldW * 0.46 : 0;
        const finX = finCenterX + Math.cos(finAng) * 2.5;
        const finY = Math.sin(finAng) * 1.5 + 0.55;
        const finZ = -1.8 + Math.cos(finAng * 2) * 0.8;

        const wTotal = Math.max(0.001, wHero + wFlow + wCompare + wTools + wFinale);
        const tx = (heroX * wHero + flowX * wFlow + compX * wCompare + toolsX * wTools + finX * wFinale) / wTotal;
        const ty = (heroY * wHero + flowY * wFlow + compY * wCompare + toolsY * wTools + finY * wFinale) / wTotal;
        const tz = (heroZ * wHero + flowZ * wFlow + compZ * wCompare + toolsZ * wTools + finZ * wFinale) / wTotal;

        const effDt = reduced ? 0 : dt;
        hub.px = approach(hub.px, tx, 4.5, effDt);
        hub.py = approach(hub.py, ty, 4.5, effDt);
        hub.pz = approach(hub.pz, tz, 4.5, effDt);

        hub.group.position.set(hub.px, hub.py, hub.pz);
        const tSec = reduced ? 0 : timeSec;
        hub.group.rotation.set(
          0.35 * Math.sin(tSec * 0.35 + hub.phase),
          0.45 * Math.cos(tSec * 0.28 + hub.phase),
          tSec * 0.15 * (h % 2 === 0 ? 1 : -1),
        );

        const isFeatActive = hovFeat === h;
        const targetScale = (isFeatActive ? 1.42 : 0.92) * (portrait ? 0.68 : 1.0);
        hub.scale = approach(hub.scale, targetScale, 8, effDt);
        hub.group.scale.setScalar(hub.scale);

        const baseHubOp =
          wCompare > 0.2 && compareSide === "chat"
            ? 0.10 * (1 - h * 0.18)
            : 0.26;
        const hubOp = clamp01(baseHubOp * densityDim * (isFeatActive ? 2.1 : 1.0) * (1 - introDisperse * 0.9));
        hub.outerMat.opacity = hubOp;
        hub.innerMat.opacity = hubOp * 0.55;
        hub.outerMat.color.copy(COL_DIM).lerp(COL_AMBER, saturationFactor);
      }

      const ptrWorldX = worldCamera.position.x + smoothPtrX * halfWorldW;
      const ptrWorldY = worldCamera.position.y + smoothPtrY * halfWorldH;
      const shockAge = timeSec - shockwaveT;
      const shockActive = !reduced && shockAge >= 0 && shockAge < 1.2;
      const shockR = shockAge * 7.5;

      // Update 44 Topic Nodes
      for (let i = 0; i < NODE_COUNT; i++) {
        const n = topicNodes[i];
        const hub = hubs[n.hub];
        const tSec = reduced ? 0 : timeSec * nodeSpeed;
        const ang = n.orbitAngle + tSec * (0.14 + (n.hub % 2) * 0.05);

        const radScale = wFlow > 0.2 && flowStep === 2 ? 0.72 : 1.0;
        let lx = Math.cos(ang) * n.orbitRadius * radScale;
        let ly = Math.sin(ang) * n.orbitRadius * 0.85 * radScale;
        let lz = n.orbitZ + Math.sin(ang * 2 + n.phase) * 0.26;

        if (wCompare > 0.2 && compareSide === "chat") {
          lx = 0.32 * Math.sin(i * 0.5);
          ly = 0.2 * Math.cos(i * 0.4);
          lz = -i * 0.08;
        }

        const tx = hub.px + lx * (portrait ? 0.68 : 1.0);
        const ty = hub.py + ly * (portrait ? 0.68 : 1.0);
        const tz = hub.pz + lz;

        const K = 16.0;
        const C = 6.2;
        let ax = (tx - n.px) * K - n.vx * C;
        let ay = (ty - n.py) * K - n.vy * C;
        const az = (tz - n.pz) * K - n.vz * C;

        if (pointerActive && !reduced) {
          const dx = n.px - ptrWorldX;
          const dy = n.py - ptrWorldY;
          const d = Math.hypot(dx, dy);
          if (d < 2.2 && d > 0.01) {
            const push = Math.pow(1 - d / 2.2, 2) * 14.0;
            ax += (dx / d) * push;
            ay += (dy / d) * push;
          }
        }

        if (shockActive) {
          const dx = n.px - shockwaveOrigin.x;
          const dy = n.py - shockwaveOrigin.y;
          const d = Math.hypot(dx, dy) + 0.01;
          const ringDist = Math.abs(d - shockR);
          if (ringDist < 1.2) {
            const imp = (1 - ringDist / 1.2) * (1 - shockAge / 1.2) * 26.0;
            ax += (dx / d) * imp;
            ay += (dy / d) * imp;
          }
        }

        if (dt > 0 && !reduced) {
          const sdt = Math.min(dt, 0.045);
          n.vx += ax * sdt;
          n.vy += ay * sdt;
          n.vz += az * sdt;
          n.px += n.vx * sdt;
          n.py += n.vy * sdt;
          n.pz += n.vz * sdt;
        } else {
          n.px = tx;
          n.py = ty;
          n.pz = tz;
        }

        // Keep Hero center clear around orbital HUD & duck
        let centerClear = 1.0;
        if (wHero > 0.1) {
          const cd = Math.hypot(n.px / 3.2, (n.py - 0.25) / 2.6);
          centerClear = lerp(1.0, smoothstep(0.85, 1.45, cd), wHero);
        }

        const isFeatHub = hovFeat === n.hub;
        const targetCol = tmpCol.copy(COL_DIM).lerp(n.baseColor, saturationFactor);
        const colStep = reduced || dt <= 0 ? 1 : Math.min(1, dt * 6 + 0.05);
        n.curColor.lerp(targetCol, colStep);

        let targetAlpha = (0.44 + 0.32 * Math.sin(n.phase + timeSec * 1.1)) * densityDim * centerClear * (1 - introDisperse * 0.9);
        if (wCompare > 0.2 && compareSide === "chat") targetAlpha *= Math.max(0.12, 1.0 - (i / NODE_COUNT) * 0.85);
        if (isFeatHub) targetAlpha = Math.min(1.0, targetAlpha * 1.65);
        n.alpha = approach(n.alpha, targetAlpha, 6, reduced ? 0 : dt);

        nodePositions[i * 3] = n.px;
        nodePositions[i * 3 + 1] = n.py;
        nodePositions[i * 3 + 2] = n.pz;
        nodeColors[i * 3] = n.curColor.r;
        nodeColors[i * 3 + 1] = n.curColor.g;
        nodeColors[i * 3 + 2] = n.curColor.b;
        nodeAlphas[i] = n.alpha;
        nodeSizes[i] = n.size * (isFeatHub ? 1.28 : 1.0);
      }

      nodePosAttr.needsUpdate = true;
      nodeColAttr.needsUpdate = true;
      nodeAlphaAttr.needsUpdate = true;
      nodeSizeAttr.needsUpdate = true;

      // Update Prerequisite & Related Edges
      for (let e = 0; e < EDGE_COUNT; e++) {
        const edge = edges[e];
        let ax: number, ay: number, az: number, bx: number, by: number, bz: number;
        let ca: THREE.Color, cb: THREE.Color;
        let alphaA: number, alphaB: number;

        if (edge.type === "hub") {
          const ha = hubs[edge.a];
          const hb = hubs[edge.b];
          ax = ha.px;
          ay = ha.py;
          az = ha.pz;
          bx = hb.px;
          by = hb.py;
          bz = hb.pz;
          ca = COL_AMBER;
          cb = COL_AMBER;
          alphaA = ha.outerMat.opacity * 0.55;
          alphaB = hb.outerMat.opacity * 0.55;
        } else {
          const na = topicNodes[edge.a];
          const nb = topicNodes[edge.b];
          ax = na.px;
          ay = na.py;
          az = na.pz;
          bx = nb.px;
          by = nb.py;
          bz = nb.pz;
          ca = na.curColor;
          cb = nb.curColor;
          alphaA = na.alpha * 0.34;
          alphaB = nb.alpha * 0.34;
        }

        const base = e * 6;
        linePositions[base] = ax;
        linePositions[base + 1] = ay;
        linePositions[base + 2] = az;
        linePositions[base + 3] = bx;
        linePositions[base + 4] = by;
        linePositions[base + 5] = bz;

        lineColors[base] = ca.r;
        lineColors[base + 1] = ca.g;
        lineColors[base + 2] = ca.b;
        lineColors[base + 3] = cb.r;
        lineColors[base + 4] = cb.g;
        lineColors[base + 5] = cb.b;

        lineAlphas[e * 2] = alphaA * edge.weight * edgeFactor;
        lineAlphas[e * 2 + 1] = alphaB * edge.weight * edgeFactor;
      }

      linePosAttr.needsUpdate = true;
      lineColAttr.needsUpdate = true;
      lineAlphaAttr.needsUpdate = true;

      let sIdx = 0;

      for (let p = 0; p < PULSE_COUNT; p++) {
        const pulse = pulses[p];
        if (!reduced && dt > 0) {
          pulse.t += dt * pulse.speed;
          if (pulse.t >= 1) {
            pulse.t -= 1;
            pulse.edgeIdx = (pulse.edgeIdx + 7) % EDGE_COUNT;
          }
        }
        const eBase = pulse.edgeIdx * 6;
        const t = pulse.t;
        const px = lerp(linePositions[eBase], linePositions[eBase + 3], t);
        const py = lerp(linePositions[eBase + 1], linePositions[eBase + 4], t);
        const pz = lerp(linePositions[eBase + 2], linePositions[eBase + 5], t);
        const eAlpha = Math.min(lineAlphas[pulse.edgeIdx * 2], lineAlphas[pulse.edgeIdx * 2 + 1]);

        sparkPositions[sIdx * 3] = px;
        sparkPositions[sIdx * 3 + 1] = py;
        sparkPositions[sIdx * 3 + 2] = pz;
        sparkColors[sIdx * 3] = COL_AMBER.r;
        sparkColors[sIdx * 3 + 1] = COL_AMBER.g;
        sparkColors[sIdx * 3 + 2] = COL_AMBER.b;
        sparkAlphas[sIdx] = clamp01(eAlpha * 2.8 * Math.sin(t * Math.PI));
        sparkSizes[sIdx] = 15;
        sIdx++;
      }

      for (let d = 0; d < DUST_COUNT; d++) {
        const m = dust[d];
        const tSec = reduced ? 0 : timeSec * m.speed;
        const dx = m.bx + Math.sin(tSec + m.phase) * 0.45;
        const dy = ((m.by + scrollVh * 1.2 + Math.cos(tSec * 0.8 + m.phase) * 0.35 + 6.5) % 13) - 6.5;
        const dz = m.bz;
        const c = m.amber ? COL_AMBER : COL_PAPER;

        sparkPositions[sIdx * 3] = dx;
        sparkPositions[sIdx * 3 + 1] = dy;
        sparkPositions[sIdx * 3 + 2] = dz;
        sparkColors[sIdx * 3] = c.r;
        sparkColors[sIdx * 3 + 1] = c.g;
        sparkColors[sIdx * 3 + 2] = c.b;
        sparkAlphas[sIdx] = (0.12 + 0.10 * Math.sin(timeSec * 1.4 + m.phase)) * (1 - introDisperse * 0.8);
        sparkSizes[sIdx] = m.size + Math.min(4, Math.abs(velocity) * 0.6);
        sIdx++;
      }

      for (let b = 0; b < BURST_COUNT; b++) {
        const pt = bursts[b];
        if (pt.active && dt > 0 && !reduced) {
          pt.age += dt;
          if (pt.age >= pt.life) {
            pt.active = false;
          } else {
            const drag = Math.exp(-2.1 * dt);
            pt.vx *= drag;
            pt.vy *= drag;
            pt.vz *= drag;
            pt.x += pt.vx * dt;
            pt.y += pt.vy * dt;
            pt.z += pt.vz * dt;
          }
        }
        const c = pt.blue ? COL_SKY : COL_AMBER;
        const lifeT = pt.active ? pt.age / pt.life : 1;
        sparkPositions[sIdx * 3] = pt.x;
        sparkPositions[sIdx * 3 + 1] = pt.y;
        sparkPositions[sIdx * 3 + 2] = pt.z;
        sparkColors[sIdx * 3] = c.r;
        sparkColors[sIdx * 3 + 1] = c.g;
        sparkColors[sIdx * 3 + 2] = c.b;
        sparkAlphas[sIdx] = pt.active ? Math.min(1, lifeT * 6) * (1 - lifeT) * 0.92 : 0;
        sparkSizes[sIdx] = 18 * (1 - lifeT * 0.4);
        sIdx++;
      }

      sparkPosAttr.needsUpdate = true;
      sparkColAttr.needsUpdate = true;
      sparkAlphaAttr.needsUpdate = true;
      sparkSizeAttr.needsUpdate = true;

      renderer.clearDepth();
      renderer.render(worldScene, worldCamera);
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      canvas.removeEventListener("webglcontextlost", onLost);
      canvas.removeEventListener("webglcontextrestored", onRestored);
      rig.dispose();
      duckTarget.dispose();
      brandTex.dispose();
      quadGeo.dispose();
      quadMaterial.dispose();
      hubGeo.dispose();
      hubInnerGeo.dispose();
      for (const h of hubs) {
        h.outerMat.dispose();
        h.innerMat.dispose();
      }
      nodeGeo.dispose();
      nodeMat.dispose();
      lineGeo.dispose();
      lineMat.dispose();
      sparkGeo.dispose();
      sparkMat.dispose();
      worldScene.clear();
      quadScene.clear();
      renderer.dispose();
    },
  };
}
