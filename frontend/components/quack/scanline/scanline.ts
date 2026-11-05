import * as THREE from "three";

/** Движок «штриховки»: любой источник (3D-сцена, canvas, video, img) → вертикальные штрихи на прозрачном фоне */

export type ScanlineOptions = {
  color: string; // цвет штриха
  step: number; // шаг столбцов, CSS px
  lineWidth: number; // доля столбца под штрих 0..1
  threshold: number; // ниже этой яркости штриха нет
  rowSize: number; // высота «ступеньки», CSS px
  glitch: number; // 0..1 сила глитча
};

export const DEFAULT_SCANLINE_OPTIONS: ScanlineOptions = {
  color: "#edb966",
  step: 3,
  lineWidth: 0.45,
  threshold: 0.08,
  rowSize: 2,
  glitch: 0.35,
};

export type Scanline = {
  renderer: THREE.WebGLRenderer; // общий рендерер (canvas прозрачный)
  /** 3D-сцена → штриховка на canvas. Камеру (aspect) настраивает вызывающий, размер — getSize() */
  renderScene(scene: THREE.Scene, camera: THREE.Camera, timeSec: number): void;
  /** 2D-источник (canvas / video / image) → штриховка на canvas. Источник растягивается на весь canvas */
  renderSource(source: HTMLCanvasElement | HTMLVideoElement | HTMLImageElement, timeSec: number): void;
  resize(widthCss: number, heightCss: number, dpr?: number): void;
  setOptions(patch: Partial<ScanlineOptions>): void;
  dispose(): void;
  /** Текущий размер в CSS px (для aspect камеры) */
  getSize(): { width: number; height: number };
};

const VERT = /* glsl */ `
void main() {
  gl_Position = vec4(position.xy, 0.0, 1.0);
}
`;

const FRAG = /* glsl */ `
precision highp float;

uniform sampler2D uTex;
uniform vec2 uSize;     // CSS px
uniform vec2 uRes;      // device px
uniform vec3 uColor;    // sRGB
uniform float uStep;
uniform float uLineWidth;
uniform float uThreshold;
uniform float uRow;
uniform float uGlitch;
uniform float uTime;
uniform float uLinear;  // 1 = текстура в линейном пространстве (render target), нужна гамма

float hash11(float p) {
  p = fract(p * 0.1031);
  p *= p + 33.33;
  p *= p + p;
  return fract(p);
}
float hash21(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}

void main() {
  // координаты в CSS px, начало слева сверху
  vec2 k = uSize / uRes;
  vec2 pos = vec2(gl_FragCoord.x, uRes.y - gl_FragCoord.y) * k;

  float colIdx = floor(pos.x / uStep);
  float rowIdx = floor(pos.y / uRow);
  float cx = (colIdx + 0.5) * uStep;
  float ry = (rowIdx + 0.5) * uRow;

  // глитч: всё считается от целых «срезов» времени, а не от шума по пикселям
  float g = uGlitch;
  float shiftX = 0.0;
  for (int i = 0; i < 2; i++) {
    float fi = float(i);
    float ev = floor(uTime * 3.0 + fi * 17.0);          // событие ~ каждые 0.33с
    float phase = fract(uTime * 3.0 + fi * 17.0);
    float on = step(hash11(ev * 1.37 + fi * 5.1 + 0.5), g * 0.45) * step(phase, 0.55);
    float bandY = hash11(ev * 7.1 + fi * 3.7 + 3.0) * uSize.y;
    float bandH = mix(6.0, 56.0, hash11(ev * 3.3 + fi + 9.0));
    float inBand = on * step(abs(pos.y - bandY), bandH * 0.5);
    float frame = floor(uTime * 20.0);                   // сдвиг меняется «кадрами»
    float amt = (hash11(frame * 1.7 + ev * 0.31 + fi) - 0.5) * 2.0;
    shiftX += inBand * amt * g * 36.0;
  }

  // столбцы: иногда пропадают или съезжают по вертикали
  float colSlice = floor(uTime * 8.0);
  float hDrop = hash21(vec2(colIdx, colSlice));
  float hMove = hash21(vec2(colIdx + 311.0, colSlice));
  float drop = step(hDrop, g * 0.03);
  float moveY = step(hMove, g * 0.06) * (hash21(vec2(colIdx, colSlice + 91.0)) - 0.5) * 70.0 * g;
  ry = (floor((ry + moveY) / uRow) + 0.5) * uRow;

  // яркость источника в центре столбца
  vec2 sp = vec2(cx - shiftX, ry);
  vec2 uv = vec2(sp.x / uSize.x, 1.0 - sp.y / uSize.y);
  vec4 s = texture2D(uTex, uv);
  float lum = dot(s.rgb, vec3(0.2126, 0.7152, 0.0722)) * s.a;
  lum = mix(lum, pow(max(lum, 0.0), 1.0 / 2.2), uLinear);
  // за пределами кадра штриха нет
  lum *= step(0.0, uv.x) * step(uv.x, 1.0) * step(0.0, uv.y) * step(uv.y, 1.0);

  // рваные края: статичный по пространству разброс (не мерцает)
  float jx = (hash21(vec2(colIdx, floor(rowIdx / 3.0))) - 0.5) * uStep * 0.4;
  float jw = 0.8 + 0.4 * hash21(vec2(colIdx + 57.0, floor(rowIdx / 2.0)));
  float jt = (hash21(vec2(colIdx + 13.0, rowIdx)) - 0.5) * 0.1;

  float thr = uThreshold + jt;
  float on = step(thr, lum);
  float t01 = clamp((lum - thr) / max(1.0 - thr, 0.001), 0.0, 1.0);

  float w = uStep * uLineWidth * mix(0.55, 1.35, t01) * jw;
  float aa = 0.6 * k.x;
  float d = abs(pos.x - (cx + jx));
  float cov = 1.0 - smoothstep(w * 0.5 - aa, w * 0.5 + aa, d);

  float alpha = cov * on * mix(0.5, 1.0, t01) * (1.0 - drop);
  gl_FragColor = vec4(uColor * alpha, alpha); // premultiplied
}
`;

export function createScanline(canvas: HTMLCanvasElement, options: Partial<ScanlineOptions> = {}): Scanline {
  const opts: ScanlineOptions = { ...DEFAULT_SCANLINE_OPTIONS, ...options };

  const renderer = new THREE.WebGLRenderer({
    canvas,
    alpha: true,
    premultipliedAlpha: true,
    antialias: false,
    powerPreference: "high-performance",
  });
  renderer.setClearColor(0x000000, 0);

  // сцена рендерится в текстуру размером в CSS px: столбцы всё равно реже пикселей
  const target = new THREE.WebGLRenderTarget(1, 1, { samples: 4 });

  const material = new THREE.ShaderMaterial({
    vertexShader: VERT,
    fragmentShader: FRAG,
    uniforms: {
      uTex: { value: null as THREE.Texture | null },
      uSize: { value: new THREE.Vector2(1, 1) },
      uRes: { value: new THREE.Vector2(1, 1) },
      uColor: { value: new THREE.Vector3() },
      uStep: { value: opts.step },
      uLineWidth: { value: opts.lineWidth },
      uThreshold: { value: opts.threshold },
      uRow: { value: opts.rowSize },
      uGlitch: { value: opts.glitch },
      uTime: { value: 0 },
      uLinear: { value: 0 },
    },
    blending: THREE.NoBlending,
    depthTest: false,
    depthWrite: false,
    transparent: false,
  });
  const geometry = new THREE.PlaneGeometry(2, 2);
  const quad = new THREE.Mesh(geometry, material);
  quad.frustumCulled = false;
  const quadScene = new THREE.Scene();
  quadScene.add(quad);
  const quadCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

  const setColor = (css: string) => {
    const rgb = { r: 0, g: 0, b: 0 };
    new THREE.Color(css).getRGB(rgb, THREE.SRGBColorSpace); // на canvas пишем sRGB как есть
    material.uniforms.uColor.value.set(rgb.r, rgb.g, rgb.b);
  };
  setColor(opts.color);

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
    const ratio = Math.min(dpr ?? (typeof window !== "undefined" ? window.devicePixelRatio : 1), 2);
    renderer.setPixelRatio(ratio);
    renderer.setSize(width, height, false); // CSS-размер задаёт вёрстка
    target.setSize(Math.ceil(width), Math.ceil(height));
    const buf = renderer.getDrawingBufferSize(new THREE.Vector2());
    material.uniforms.uSize.value.set(width, height);
    material.uniforms.uRes.value.copy(buf);
  };
  resize(width, height);

  const present = (tex: THREE.Texture, linear: boolean, timeSec: number) => {
    material.uniforms.uTex.value = tex;
    material.uniforms.uLinear.value = linear ? 1 : 0;
    material.uniforms.uTime.value = timeSec;
    renderer.setRenderTarget(null);
    renderer.clear();
    renderer.render(quadScene, quadCamera);
  };

  // текстура 2D-источника кэшируется, пока источник тот же
  let srcTex: THREE.Texture | null = null;
  let srcRef: HTMLCanvasElement | HTMLVideoElement | HTMLImageElement | null = null;

  const sourceTexture = (source: HTMLCanvasElement | HTMLVideoElement | HTMLImageElement) => {
    if (srcRef !== source || !srcTex) {
      srcTex?.dispose();
      if (source instanceof HTMLVideoElement) srcTex = new THREE.VideoTexture(source);
      else if (source instanceof HTMLCanvasElement) srcTex = new THREE.CanvasTexture(source);
      else srcTex = new THREE.Texture(source);
      srcTex.minFilter = THREE.LinearFilter;
      srcTex.magFilter = THREE.LinearFilter;
      srcTex.generateMipmaps = false;
      srcRef = source;
    }
    // canvas меняется каждый кадр; video обновляет сам VideoTexture, но лишний флаг не мешает;
    // картинку грузим один раз
    if (!(source instanceof HTMLImageElement) || !srcTex.userData.uploaded) {
      srcTex.needsUpdate = true;
      if (source instanceof HTMLImageElement) srcTex.userData.uploaded = true;
    }
    return srcTex;
  };

  return {
    renderer,
    renderScene(scene, camera, timeSec) {
      if (disposed || lost) return;
      renderer.setRenderTarget(target);
      renderer.setClearColor(0x000000, 1);
      renderer.clear();
      renderer.render(scene, camera);
      renderer.setClearColor(0x000000, 0);
      present(target.texture, true, timeSec);
    },
    renderSource(source, timeSec) {
      if (disposed || lost) return;
      if (source instanceof HTMLVideoElement && source.readyState < 2) return;
      if (source instanceof HTMLImageElement && !(source.complete && source.naturalWidth > 0)) return;
      present(sourceTexture(source), false, timeSec);
    },
    resize,
    setOptions(patch) {
      Object.assign(opts, patch);
      const u = material.uniforms;
      if (patch.color !== undefined) setColor(opts.color);
      if (patch.step !== undefined) u.uStep.value = Math.max(1, opts.step);
      if (patch.lineWidth !== undefined) u.uLineWidth.value = opts.lineWidth;
      if (patch.threshold !== undefined) u.uThreshold.value = opts.threshold;
      if (patch.rowSize !== undefined) u.uRow.value = Math.max(1, opts.rowSize);
      if (patch.glitch !== undefined) u.uGlitch.value = opts.glitch;
    },
    getSize: () => ({ width, height }),
    dispose() {
      if (disposed) return;
      disposed = true;
      canvas.removeEventListener("webglcontextlost", onLost);
      canvas.removeEventListener("webglcontextrestored", onRestored);
      srcTex?.dispose();
      target.dispose();
      geometry.dispose();
      material.dispose();
      renderer.dispose(); // контекст не теряем: canvas может быть переиспользован (StrictMode)
    },
  };
}
