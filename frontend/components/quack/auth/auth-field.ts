import * as THREE from "three";
import { createAuthDuck } from "./auth-duck";

/**
 * Экран входа. Копия components/quack/landing/glyph-field.ts: сцена, шейдер, атлас символов и физика нодов те же,
 * но утка своя (auth-duck.ts: сидит, на пароле надевает повязку) и композиция под форму сбоку.
 * Блоки «шейдер», «атлас» и «свободные ноды» переносятся с лендинга как есть, когда он меняется.
 *
 * Лендинг: 3D-сцена → поле из мелких математических символов на гексагональной решётке (· + × = ∫ ∂ √ ∑ π Δ λ …).
 * Сцены две: утка (яркая, оранжево-жёлтая) и фон: графы по бокам (хабы-кольца, точки-темы, связи) и россыпь свободных нодов,
 * которые разлетаются от курсора. Фон тусклый тёплый серый.
 * Каждая сцена рендерится в маленькую текстуру, шейдер-решётка по яркости ячейки выбирает глиф, размер и цвет.
 * `disperse` 1 → 0 собирает глифы из пыли; тот же параметр потом привяжем к скроллу, чтобы утка рассыпалась.
 */

export type AuthFieldInput = {
  /** Положение курсора в кадре, -1..1 (x вправо, y вверх) */
  pointerX: number;
  pointerY: number;
  /** Курсор над страницей: без него ноды не отталкиваются (иначе в начале их выталкивает из центра кадра) */
  pointerActive: boolean;
  /** 0 — всё собрано, 1 — всё рассыпано */
  disperse: number;
  /** Ученик вводит пароль: утке надевают повязку */
  blind: boolean;
  /** Без анимации перехода: повязка сразу надета или снята (prefers-reduced-motion) */
  instant?: boolean;
};

export type AuthField = {
  resize(widthCss: number, heightCss: number, dpr?: number): void;
  render(timeSec: number, input: AuthFieldInput): void;
  dispose(): void;
};

const TARGET_SCALE = 0.5; // сцены считаем вдвое реже CSS-пикселей: ячейка всё равно ≥ 6px

const VERT = /* glsl */ `
void main() {
  gl_Position = vec4(position.xy, 0.0, 1.0);
}
`;

const FRAG = /* glsl */ `
precision highp float;

uniform sampler2D uDuck;
uniform sampler2D uBg;
uniform sampler2D uAtlas; // сетка символов: строка — ярус яркости, столбец — символ
uniform vec2 uSize;   // CSS px
uniform vec2 uRes;    // device px
uniform float uCell;  // шаг решётки, CSS px
uniform float uTime;
uniform float uDisperse;

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

float h21(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
vec2 h22(vec2 p) {
  return vec2(h21(p), h21(p + vec2(41.7, 19.3)));
}

// (яркость, покрытие) сцены в точке, CSS px от левого верхнего угла
vec2 sampleScene(sampler2D tex, vec2 sp, float gam, float bias) {
  vec2 uv = vec2(sp.x / uSize.x, 1.0 - sp.y / uSize.y);
  float inside = step(0.0, uv.x) * step(uv.x, 1.0) * step(0.0, uv.y) * step(uv.y, 1.0);
  vec4 s = texture2D(tex, uv, bias);
  float a = s.a * inside;
  float l = a > 0.001 ? dot(s.rgb, LUMA) / s.a : 0.0;
  return vec2(pow(clamp(l, 0.0, 1.0), gam), a);
}

const float ATLAS_COLS = 8.0;
const float ATLAS_ROWS = 4.0;

// покрытие символа из атласа: p — локальная точка в шагах решётки (y вниз), q — размер квадрата символа
float glyphAlpha(vec2 p, float row, float col, float q) {
  vec2 t = p / q + 0.5;
  if (t.x < 0.0 || t.x > 1.0 || t.y < 0.0 || t.y > 1.0) return 0.0;
  vec2 uv = vec2((col + t.x) / ATLAS_COLS, 1.0 - (row + t.y) / ATLAS_ROWS);
  return smoothstep(0.12, 0.85, texture2D(uAtlas, uv, -0.5).a);
}

void main() {
  vec2 k = uSize / uRes;
  vec2 pos = vec2(gl_FragCoord.x, uRes.y - gl_FragCoord.y) * k;
  vec2 q = pos / uCell;

  // гексагональная решётка: два сдвинутых прямоугольных набора, берём ближайший центр
  vec2 s = vec2(1.0, 1.7320508);
  vec2 a = mod(q, s) - s * 0.5;
  vec2 b = mod(q - s * 0.5, s) - s * 0.5;
  vec2 gv = dot(a, a) < dot(b, b) ? a : b;
  vec2 id = q - gv;

  float r1 = h21(id * 1.31 + 3.7);
  float r2 = h21(id * 0.77 + 91.1);
  float t = uTime;

  // «живость»: каждый глиф чуть дышит и покачивается на месте
  vec2 wob = 0.06 * vec2(sin(t * 0.9 + r1 * 40.0), cos(t * 0.7 + r2 * 40.0));
  vec2 local = gv + wob;

  // сборка из пыли / рассыпание: ячейки читают сцену из случайных мест и появляются по очереди
  float dd = uDisperse;
  vec2 off = (h22(id + 11.0) - 0.5) * 2.0 * dd * dd * 320.0;
  float appear = clamp(((1.0 - dd) - r2 * 0.8) / 0.2, 0.0, 1.0);
  vec2 center = id * uCell;
  vec2 sp = center + off;

  vec2 dk = sampleScene(uDuck, sp, 0.55, 0.0);
  vec2 bg = sampleScene(uBg, sp, 0.45, 0.0);
  bool isDuck = dk.y > 0.5;
  // закрашенные ячейки утки не пропадают целиком: тень остаётся редкими тусклыми глифами, силуэт цел
  float L = isDuck ? max(dk.x, 0.2) : bg.x * step(0.5, bg.y);
  float flick = 0.06 * sin(t * 1.1 + r1 * 40.0) + (r2 - 0.5) * 0.1;
  float lv = L + flick;

  // символ по яркости: от мелких знаков в тени до плотных греческих букв на свету
  float cov = 0.0;
  if (L > 0.04 && lv > 0.08) {
    float row = lv < 0.24 ? 0.0 : (lv < 0.46 ? 1.0 : (lv < 0.74 ? 2.0 : 3.0));
    float col = floor(r1 * ATLAS_COLS);
    float scale = 1.0;
    vec2 p = local / (0.4 + 0.6 * appear);
    cov = glyphAlpha(p, row, col, 1.5 * scale) * appear;
  }

  vec3 col;
  float strength;
  if (isDuck) {
    vec3 dark = vec3(0.7, 0.56, 0.44); // тень: приглушённый тёплый серый, как в референсе
    vec3 mid = vec3(1.0, 0.5, 0.1);
    vec3 hi = vec3(1.0, 0.86, 0.38);
    col = mix(dark, mid, smoothstep(0.12, 0.55, lv));
    col = mix(col, hi, smoothstep(0.5, 0.9, lv));
    col *= 0.9 + 0.5 * lv;
    strength = 1.0;
  } else {
    col = vec3(0.74, 0.64, 0.54) * (0.35 + 0.65 * lv);
    strength = 0.75;
  }

  // свечение: размытая яркость утки (верхние mip-уровни текстуры)
  vec2 h1 = sampleScene(uDuck, pos, 0.55, 4.0);
  vec2 h2 = sampleScene(uDuck, pos, 0.55, 5.5);
  float haze = (h1.x * h1.y * 0.6 + h2.x * h2.y * 0.4) * (1.0 - dd) * 1.3;
  vec3 hazeCol = vec3(1.0, 0.46, 0.08);
  float hazeA = clamp(haze * 0.5, 0.0, 0.55);

  float ga = cov * strength;
  vec3 rgb = col * ga + hazeCol * hazeA * (1.0 - ga);
  float alpha = ga + hazeA * (1.0 - ga);
  gl_FragColor = vec4(rgb, alpha); // premultiplied
}
`;

type BgGraph = {
  obj: THREE.Group;
  land: [number, number]; // положение в кадре (-1..1) для широкого экрана
  port: [number, number]; // и для вертикального
  z: number; // глубина: влияет на параллакс
  size: number; // доля половины высоты кадра
  phase: number;
};

/** Детерминированный генератор: фон не должен меняться между запусками */
function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function createAuthField(canvas: HTMLCanvasElement): AuthField {
  const renderer = new THREE.WebGLRenderer({
    canvas,
    alpha: true,
    premultipliedAlpha: true,
    antialias: false,
    powerPreference: "high-performance",
  });
  renderer.setClearColor(0x000000, 0);

  const duck = createAuthDuck();

  // --- фон: графы как в приложении (хабы-кольца, цепочки тем-точек, тонкие связи); шейдер сделает из них глифы ---
  const bgScene = new THREE.Scene();
  const bgCamera = new THREE.PerspectiveCamera(30, 1, 0.1, 60);
  bgCamera.position.set(0, 0, 12);
  const BG_HALF_H = Math.tan((30 * Math.PI) / 360) * 12; // половина высоты кадра на плоскости z=0

  const geometries: THREE.BufferGeometry[] = [];
  const geo = <T extends THREE.BufferGeometry>(g: T) => {
    geometries.push(g);
    return g;
  };
  // без освещения: яркость задаёт серый цвет, поэтому связи, темы и хабы держат свой тон
  const edgeMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(0.22, 0.22, 0.22) });
  const nodeMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(0.45, 0.45, 0.45) });
  const hubMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(0.75, 0.75, 0.75) });

  const nodeGeo = geo(new THREE.SphereGeometry(1, 20, 14));
  const hubGeo = geo(new THREE.TorusGeometry(1, 0.2, 14, 56));
  const edgeGeo = geo(new THREE.CylinderGeometry(1, 1, 1, 6, 1, true));
  const Y = new THREE.Vector3(0, 1, 0);

  /** Граф в локальных координатах (~ -1..1): три хаба цепочкой, от каждого веером наружу цепочки тем, пара связей между хабами */
  const buildGraph = (seed: number, dir: 1 | -1): THREE.Group => {
    const rand = rng(seed);
    const g = new THREE.Group();
    const edge = (a: THREE.Vector3, b: THREE.Vector3, r = 0.02) => {
      const m = new THREE.Mesh(edgeGeo, edgeMat);
      const d = b.clone().sub(a);
      m.position.copy(a).addScaledVector(d, 0.5);
      m.scale.set(r, d.length(), r);
      m.quaternion.setFromUnitVectors(Y, d.normalize());
      g.add(m);
    };
    const node = (p: THREE.Vector3, r: number) => {
      const m = new THREE.Mesh(nodeGeo, nodeMat);
      m.position.copy(p);
      m.scale.setScalar(r);
      g.add(m);
    };

    const hubs = [new THREE.Vector3(-0.2 * dir, 0.7, 0), new THREE.Vector3(0.02 * dir, 0, 0), new THREE.Vector3(-0.2 * dir, -0.7, 0)];
    const chains: THREE.Vector3[][] = [];
    hubs.forEach((h, i) => {
      const ring = new THREE.Mesh(hubGeo, hubMat);
      ring.position.copy(h);
      ring.scale.setScalar(0.13);
      g.add(ring);
      if (i > 0) edge(hubs[i - 1], h, 0.018);

      for (const branch of [-1, 1]) {
        const base = (i - 1) * -0.5 + branch * (0.35 + rand() * 0.25); // веер наружу, ближе к горизонтали
        const chain: THREE.Vector3[] = [h];
        const len = 2 + Math.floor(rand() * 2);
        for (let j = 1; j <= len; j++) {
          const ang = base + (rand() - 0.5) * 0.45;
          const prev = chain[j - 1];
          const step = 0.26 + rand() * 0.1;
          const p = new THREE.Vector3(prev.x + Math.cos(ang) * step * dir, prev.y + Math.sin(ang) * step, (rand() - 0.5) * 0.3);
          edge(prev, p);
          node(p, 0.045 + rand() * 0.02);
          chain.push(p);
        }
        chains.push(chain);
      }
    });
    // связи между соседними кластерами
    for (let i = 0; i + 2 < chains.length; i += 2) {
      const a = chains[i][chains[i].length - 1];
      const b = chains[i + 3]?.[1] ?? chains[i + 2][1];
      edge(a, b, 0.012);
    }
    return g;
  };

  // свободные ноды: ничем не соединены, плывут на месте и разлетаются от курсора
  type FreeNode = { mesh: THREE.Mesh; nx: number; ny: number; z: number; r: number; phase: number; ox: number; oy: number; vx: number; vy: number };
  const freeNodes: FreeNode[] = [];
  {
    const rand = rng(99);
    while (freeNodes.length < 46) {
      const nx = rand() * 2 - 1;
      const ny = rand() * 2 - 1;
      if (nx > 0.3 && Math.abs(ny) < 0.85) continue; // за формой
      const mesh = new THREE.Mesh(nodeGeo, nodeMat);
      bgScene.add(mesh);
      freeNodes.push({ mesh, nx, ny, z: -0.5 - rand() * 2, r: 0.1 + rand() * 0.05, phase: rand() * 6.28, ox: 0, oy: 0, vx: 0, vy: 0 });
    }
  }

  const graphs: BgGraph[] = [];
  const addGraph = (seed: number, dir: 1 | -1, it: Omit<BgGraph, "obj">) => {
    const obj = buildGraph(seed, dir);
    bgScene.add(obj);
    graphs.push({ obj, ...it });
  };
  addGraph(7, -1, { land: [-0.74, -0.02], port: [-0.5, 0.02], z: -1, size: 0.62, phase: 0.4 });
  addGraph(21, 1, { land: [0.76, -0.08], port: [0.52, -0.05], z: -1, size: 0.6, phase: 2.3 });
  addGraph(3, -1, { land: [-0.96, 0.66], port: [-0.8, 0.4], z: -3, size: 0.34, phase: 4.1 });
  addGraph(12, 1, { land: [0.96, 0.7], port: [0.85, -0.5], z: -3, size: 0.32, phase: 5.4 });

  // --- рендер в текстуры и решётка ---
  // у утки есть mip-цепочка: из неё шейдер берёт размытие для свечения
  const duckTarget = new THREE.WebGLRenderTarget(1, 1, {
    samples: 4,
    generateMipmaps: true,
    minFilter: THREE.LinearMipmapLinearFilter,
  });
  const bgTarget = new THREE.WebGLRenderTarget(1, 1, { samples: 4 });

  // атлас символов: в строке ярус яркости (знаки → операторы → анализ → греческие буквы), столбец выбирается случайно по ячейке
  const ATLAS_CELL = 128;
  const atlasRows = ["+−=<>÷±%", "×≈≠≤≥∞°π", "∫√∂∞θλπσ", "∑ΔΩπλθΣΦ"];
  const atlasCanvas = document.createElement("canvas");
  atlasCanvas.width = ATLAS_CELL * 8;
  atlasCanvas.height = ATLAS_CELL * atlasRows.length;
  const actx = atlasCanvas.getContext("2d");
  if (actx) {
    actx.fillStyle = "#fff";
    actx.textAlign = "center";
    actx.textBaseline = "middle";
    // обычное начертание без обводки: на мелком кегле тонкие линии serif-шрифтов пропадают, поэтому берём гротески
    actx.font = `${Math.round(ATLAS_CELL * 0.66)}px "Segoe UI Symbol","Noto Sans Math","DejaVu Sans","Apple Symbols","Arial Unicode MS",sans-serif`;
    atlasRows.forEach((row, r) =>
      [...row].forEach((ch, c) => actx.fillText(ch, (c + 0.5) * ATLAS_CELL, (r + 0.5) * ATLAS_CELL + ATLAS_CELL * 0.03)),
    );
  }
  const atlas = new THREE.CanvasTexture(atlasCanvas);
  atlas.anisotropy = 4;

  const material = new THREE.ShaderMaterial({
    vertexShader: VERT,
    fragmentShader: FRAG,
    uniforms: {
      uDuck: { value: duckTarget.texture },
      uBg: { value: bgTarget.texture },
      uAtlas: { value: atlas },
      uSize: { value: new THREE.Vector2(1, 1) },
      uRes: { value: new THREE.Vector2(1, 1) },
      uCell: { value: 11 },
      uTime: { value: 0 },
      uDisperse: { value: 0 },
    },
    blending: THREE.NoBlending,
    depthTest: false,
    depthWrite: false,
    transparent: false,
  });
  const quadGeo = new THREE.PlaneGeometry(2, 2);
  const quad = new THREE.Mesh(quadGeo, material);
  quad.frustumCulled = false;
  const quadScene = new THREE.Scene();
  quadScene.add(quad);
  const quadCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

  let width = canvas.clientWidth || 300;
  let height = canvas.clientHeight || 150;
  let disposed = false;
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
    const ratio = Math.min(dpr ?? (typeof window !== "undefined" ? window.devicePixelRatio : 1), 1.5);
    renderer.setPixelRatio(ratio);
    renderer.setSize(width, height, false);
    const tw = Math.max(2, Math.ceil(width * TARGET_SCALE));
    const th = Math.max(2, Math.ceil(height * TARGET_SCALE));
    duckTarget.setSize(tw, th);
    bgTarget.setSize(tw, th);
    const buf = renderer.getDrawingBufferSize(new THREE.Vector2());
    material.uniforms.uSize.value.set(width, height);
    material.uniforms.uRes.value.copy(buf);
    material.uniforms.uCell.value = THREE.MathUtils.clamp(Math.round((Math.max(width, height) / 105) * 2) / 2, 7, 14);
    bgCamera.aspect = width / height;
    bgCamera.updateProjectionMatrix();
  };
  resize(width, height);

  // сглаженный поворот к курсору
  let yaw = 0;
  let pitch = 0;
  let lastT = 0;
  let blind = 0; // 0..1: насколько надета повязка

  const renderTo = (target: THREE.WebGLRenderTarget, scene: THREE.Scene, camera: THREE.Camera) => {
    renderer.setRenderTarget(target);
    renderer.setClearColor(0x000000, 0);
    renderer.clear();
    renderer.render(scene, camera);
  };

  return {
    resize,
    render(timeSec, input) {
      if (disposed || lost) return;
      const aspect = width / height;
      const portrait = aspect < 1;
      const dt = Math.min(0.1, Math.max(0, timeSec - lastT));
      lastT = timeSec;
      const ease = 1 - Math.pow(0.001, dt); // ~ за 0.5с к цели
      yaw += (input.pointerX * 0.6 - yaw) * ease;
      pitch += (input.pointerY * 0.5 - pitch) * ease;

      // повязка надевается за 0.8с и снимается за 0.5с
      const target = input.blind ? 1 : 0;
      if (input.instant) blind = target;
      else blind = THREE.MathUtils.clamp(blind + (target ? dt / 0.8 : -dt / 0.5), 0, 1);

      // утка сидит слева от формы (на узком экране — над формой) и следит за курсором, пока глаза открыты
      const look = 1 - 0.8 * blind;
      duck.update(timeSec, aspect, blind);
      duck.root.rotation.y = -0.42 + yaw * 0.7 * look;
      duck.root.rotation.x = pitch * 0.1 * look;
      duck.camera.zoom = portrait ? 1.05 : 0.7;
      duck.camera.setViewOffset(width, height, portrait ? 0 : 0.28 * width, portrait ? 0.2 * height : -0.04 * height, width, height);

      const halfW = BG_HALF_H * aspect;
      // свободные ноды: пружина к своему месту, курсор отталкивает
      const pw = { x: input.pointerX * halfW, y: input.pointerY * BG_HALF_H };
      const reach = 0.4 * BG_HALF_H;
      const sdt = Math.min(dt, 0.05);
      for (const n of freeNodes) {
        const hx = n.nx * halfW + Math.sin(timeSec * 0.21 + n.phase) * 0.12;
        const hy = n.ny * BG_HALF_H + Math.cos(timeSec * 0.17 + n.phase * 1.7) * 0.12;
        if (input.pointerActive) {
          const dx = hx + n.ox - pw.x;
          const dy = hy + n.oy - pw.y;
          const dist = Math.hypot(dx, dy);
          if (dist < reach && dist > 1e-4) {
            const f = (1 - dist / reach) ** 2 * 70;
            n.vx += (dx / dist) * f * sdt;
            n.vy += (dy / dist) * f * sdt;
          }
        }
        n.vx += -n.ox * 5 * sdt;
        n.vy += -n.oy * 5 * sdt;
        const damp = Math.exp(-3 * sdt);
        n.vx *= damp;
        n.vy *= damp;
        n.ox += n.vx * sdt;
        n.oy += n.vy * sdt;
        const persp = (12 - n.z) / 12; // положение задано в плоскости экрана, глубина лишь прячет за графы
        n.mesh.position.set((hx + n.ox) * persp, (hy + n.oy) * persp, n.z);
        n.mesh.scale.setScalar(n.r * persp);
      }

      // графы по бокам: медленно покачиваются в 3D, лёгкий параллакс от курсора
      for (const it of graphs) {
        const [nx, ny] = portrait ? it.port : it.land;
        const par = 0.22 * (1 + it.z * 0.25);
        it.obj.position.set(
          nx * halfW - yaw * par * BG_HALF_H + Math.sin(timeSec * 0.17 + it.phase) * 0.03 * halfW,
          ny * BG_HALF_H + pitch * par * BG_HALF_H + Math.cos(timeSec * 0.13 + it.phase) * 0.03 * BG_HALF_H,
          it.z,
        );
        it.obj.scale.setScalar(it.size * BG_HALF_H * (portrait ? 0.75 : 1));
        it.obj.rotation.set(0.12 * Math.sin(timeSec * 0.13 + it.phase * 2), 0.5 * Math.sin(timeSec * 0.18 + it.phase), 0);
      }

      renderTo(duckTarget, duck.scene, duck.camera);
      renderTo(bgTarget, bgScene, bgCamera);

      material.uniforms.uTime.value = timeSec;
      material.uniforms.uDisperse.value = input.disperse;
      renderer.setRenderTarget(null);
      renderer.clear();
      renderer.render(quadScene, quadCamera);
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      canvas.removeEventListener("webglcontextlost", onLost);
      canvas.removeEventListener("webglcontextrestored", onRestored);
      duck.dispose();
      for (const g of geometries) g.dispose();
      edgeMat.dispose();
      nodeMat.dispose();
      hubMat.dispose();
      atlas.dispose();
      duckTarget.dispose();
      bgTarget.dispose();
      quadGeo.dispose();
      material.dispose();
      renderer.dispose();
    },
  };
}
