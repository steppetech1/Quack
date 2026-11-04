import * as THREE from "three";
import { mergeVertices } from "three/examples/jsm/utils/BufferGeometryUtils.js";

/**
 * Утка для экрана входа: та же модель, что в components/quack/scanline/duck-3d.ts (из примитивов three.js, серые материалы
 * и контрастный свет для шейдера-решётки), но она сидит и ждёт: дышит, моргает, иногда косится и вертит хвостом.
 * Когда ученик вводит пароль, на глаза опускается повязка (`blind` 0 → 1), утка замирает.
 * Копия, а не импорт: лендинг ещё меняется, а здесь нужен доступ к голове и свой набор поз.
 */

export type AuthDuck = {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  /** Корень модели: снаружи им можно довернуть утку (поворот к курсору) */
  root: THREE.Group;
  /** `blind`: 0 — глаза открыты, 1 — повязка надета. Плавность задаёт вызывающий, перелёт «с пружинкой» — здесь */
  update(timeSec: number, aspect: number, blind: number): void;
  dispose(): void;
};

const CENTER_Y = 0.2; // куда смотрит камера
const FOV = 24;
const KEY_INTENSITY = 4.2;
const TONE_GAMMA = 2.0; // показатель кривой яркости (>1 — контрастнее)
const TONE_GAIN = 4.0;
const RIM_INTENSITY = 2.6;
/** Тело опущено на землю: утка сидит, ног не видно */
const SIT_Y = -0.34;

const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const smooth = (a: number, b: number, x: number) => {
  const t = clamp01((x - a) / (b - a));
  return t * t * (3 - 2 * t);
};
/** Окно [a, b] с плавными краями: 0 → 1 → 0 */
const bump = (x: number, a: number, b: number) => {
  const t = clamp01((x - a) / (b - a));
  return Math.sin(Math.PI * t) ** 2;
};
/** 0 → 1 с небольшим перелётом и возвратом: повязка садится «с пружинкой» */
const easeOutBack = (x: number) => {
  const c1 = 1.4;
  const c3 = c1 + 1;
  const t = clamp01(x) - 1;
  return 1 + c3 * t * t * t + c1 * t * t;
};

/** Детерминированный value-noise по 3D-точке, -1..1 (без Math.random: утка не должна меняться между запусками) */
const hash3 = (x: number, y: number, z: number) => {
  const h = Math.sin(x * 127.1 + y * 311.7 + z * 74.7) * 43758.5453;
  return (h - Math.floor(h)) * 2 - 1;
};
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
function noise3(x: number, y: number, z: number): number {
  const xi = Math.floor(x);
  const yi = Math.floor(y);
  const zi = Math.floor(z);
  const u = smooth(0, 1, x - xi);
  const w = smooth(0, 1, y - yi);
  const q = smooth(0, 1, z - zi);
  const plane = (dz: number) =>
    lerp(
      lerp(hash3(xi, yi, zi + dz), hash3(xi + 1, yi, zi + dz), u),
      lerp(hash3(xi, yi + 1, zi + dz), hash3(xi + 1, yi + 1, zi + dz), u),
      w,
    );
  return lerp(plane(0), plane(1), q);
}

/**
 * Гладкая «капля»: сфера, растянутая по осям и деформированная функцией; вершины склеены, нормали считаются заново.
 * bumps — амплитуда «перьевой» ряби (радиальное смещение по шуму): ломает штрихи на гладких местах.
 */
function blob(
  rx: number,
  ry: number,
  rz: number,
  deform?: (v: THREE.Vector3) => void,
  wSeg = 56,
  hSeg = 36,
  bumps = 0,
  bumpFreq = 4,
  seed = 0,
): THREE.BufferGeometry {
  let g: THREE.BufferGeometry = new THREE.SphereGeometry(1, wSeg, hSeg);
  g.deleteAttribute("uv");
  g.deleteAttribute("normal");
  g = mergeVertices(g); // без шва: иначе на стыке видна полоса
  const pos = g.getAttribute("position");
  const v = new THREE.Vector3();
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i);
    if (bumps > 0) {
      const f = bumpFreq;
      const n = noise3(v.x * f + seed, v.y * f, v.z * f) + 0.5 * noise3(v.x * f * 2.3 + seed, v.y * f * 2.3 + 7, v.z * f * 2.3);
      v.multiplyScalar(1 + bumps * n);
    }
    v.set(v.x * rx, v.y * ry, v.z * rz);
    deform?.(v);
    pos.setXYZ(i, v.x, v.y, v.z);
  }
  g.computeVertexNormals();
  return g;
}

export function createAuthDuck(): AuthDuck {
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 80);

  const geometries: THREE.BufferGeometry[] = [];
  const materials: THREE.Material[] = [];
  const geo = (g: THREE.BufferGeometry) => {
    geometries.push(g);
    return g;
  };
  const mat = (color: number, roughness: number, side: THREE.Side = THREE.FrontSide) => {
    const m = new THREE.MeshStandardMaterial({ color, roughness, metalness: 0, side });
    // крутая кривая на выходе освещения: тень уходит в ноль быстрее, чем обычный sRGB-подъём тёмных тонов
    m.onBeforeCompile = (sh) => {
      sh.fragmentShader = sh.fragmentShader.replace(
        "#include <opaque_fragment>",
        `outgoingLight = pow(max(outgoingLight, vec3(0.0)), vec3(${TONE_GAMMA.toFixed(2)})) * ${TONE_GAIN.toFixed(2)};
#include <opaque_fragment>`,
      );
    };
    materials.push(m);
    return m;
  };

  const featherMat = mat(0x9a9a9a, 0.9);
  const wingMat = mat(0x707070, 0.85);
  const beakMat = mat(0xd2d2d2, 0.4);
  // повязка тёмная: в решётке это редкие тусклые глифы, на светлой голове читается как полоса.
  // Узел и хвостики светлые, чтобы их было видно на тёмном фоне
  const clothMat = mat(0x2a2a2a, 0.8, THREE.DoubleSide);
  const knotMat = mat(0xe4e4e4, 0.75, THREE.DoubleSide);
  const eyeMat = new THREE.MeshBasicMaterial({ color: 0x000000 });
  materials.push(eyeMat);

  const mesh = (g: THREE.BufferGeometry, m: THREE.Material) => new THREE.Mesh(geo(g), m);

  // --- корень: поворот 3/4 к камере ---
  const root = new THREE.Group();
  root.rotation.y = -0.42;
  scene.add(root);

  // --- тело: капля с приподнятым хвостом ---
  const body = new THREE.Group();
  root.add(body);
  const BODY_RX = 1.25;
  body.add(
    mesh(
      blob(BODY_RX, 0.8, 0.78, (v) => {
        const u = v.x / BODY_RX; // -1 хвост … +1 грудь
        if (u < 0) {
          const k = -u;
          const s = 1 - 0.5 * Math.pow(k, 1.6);
          v.y *= s;
          v.z *= s;
          v.y += 0.62 * k * k;
        } else {
          v.y -= 0.1 * u * u;
        }
        if (v.y < 0) v.y *= 0.92;
      }, 56, 36, 0.05, 3.4, 1.3),
      featherMat,
    ),
  );

  // хвостовое перо
  const tail = new THREE.Group();
  tail.position.set(-1.1, 0.5, 0);
  body.add(tail);
  const tailMesh = mesh(
    blob(0.46, 0.08, 0.3, (v) => {
      const k = clamp01((-v.x / 0.46 + 1) / 2);
      v.z *= 0.55 + 0.7 * Math.sin(Math.PI * Math.min(1, k * 0.9 + 0.1));
      v.y += 0.035 * Math.sin(v.z * 22);
    }, 56, 36, 0.05, 3, 5.1),
    featherMat,
  );
  tailMesh.position.set(-0.32, 0, 0);
  tail.add(tailMesh);

  // --- крылья: сложены ---
  const makeWing = (side: 1 | -1) => {
    const g = geo(
      blob(0.8, 0.46, 0.1, (v) => {
        const k = clamp01((-v.x / 0.8 + 1) / 2);
        v.y *= 1 - 0.65 * k;
        v.z *= 1 - 0.4 * k;
        v.y += 0.14 * k * k;
        v.z -= side * 0.08 * (v.y / 0.46) ** 2;
        const row = Math.sin(v.y * 19 - v.x * 3.5);
        v.z += side * (0.03 * row + 0.015 * Math.sin(v.y * 41 + v.x * 6)) * (0.4 + 0.6 * k);
      }, 72, 44, 0.03, 3.5, 2.7),
    );
    const pivot = new THREE.Group();
    pivot.position.set(0.4, 0.3, 0.8 * side);
    pivot.rotation.y = -0.3 * side;
    const m = new THREE.Mesh(g, wingMat);
    m.position.set(-0.6, -0.1, 0);
    m.rotation.z = -0.16;
    pivot.add(m);
    body.add(pivot);
    return pivot;
  };
  const wingNear = makeWing(1);
  const wingFar = makeWing(-1);

  // --- шея, голова, клюв ---
  const NECK_X = 0.8;
  const NECK_Y = 0.42;
  const neck = new THREE.Group();
  neck.position.set(NECK_X, NECK_Y, 0);
  body.add(neck);
  const neckMesh = mesh(
    blob(0.3, 0.55, 0.3, (v) => {
      const t = clamp01((v.y / 0.55 + 1) / 2);
      const flare = 1 + 0.9 * Math.pow(1 - t, 2.5);
      v.x = v.x * flare + 0.2 * t;
      v.z *= flare;
    }, 56, 36, 0.045, 3.5, 4.4),
    featherMat,
  );
  neckMesh.position.set(0, 0.2, 0);
  neck.add(neckMesh);

  const head = new THREE.Group(); // шарнир головы на конце шеи
  head.position.set(0.2, 0.62, 0);
  neck.add(head);
  const HEAD_C = new THREE.Vector3(0.05, 0.08, 0); // центр черепа в системе головы
  const HEAD_R = new THREE.Vector3(0.5, 0.45, 0.43);
  const headMesh = mesh(
    blob(HEAD_R.x, HEAD_R.y, HEAD_R.z, (v) => {
      if (v.y < 0) v.y *= 0.9;
    }, 56, 36, 0.032, 4, 8.2),
    featherMat,
  );
  headMesh.position.copy(HEAD_C);
  head.add(headMesh);

  const beakDeform = (rx: number, drop: number) => (v: THREE.Vector3) => {
    const k = clamp01((v.x / rx + 1) / 2);
    v.z *= 0.72 + 0.4 * smooth(0, 1, k);
    v.y -= drop * k * k;
  };
  const beakUp = mesh(blob(0.5, 0.1, 0.29, beakDeform(0.5, 0.035), 40, 24), beakMat);
  beakUp.position.set(0.6, -0.02, 0);
  head.add(beakUp);
  const jaw = new THREE.Group();
  jaw.position.set(0.2, -0.13, 0);
  head.add(jaw);
  const beakLow = mesh(blob(0.42, 0.06, 0.23, beakDeform(0.42, 0.02), 40, 24), beakMat);
  beakLow.position.set(0.34, 0, 0);
  jaw.add(beakLow);

  // глаза: чёрные точки; моргание сплющивает их по вертикали
  const eyeGeo = geo(new THREE.SphereGeometry(0.065, 16, 12));
  const eyes: THREE.Mesh[] = [];
  for (const side of [1, -1]) {
    const eye = new THREE.Mesh(eyeGeo, eyeMat);
    eye.position.set(0.22, 0.13, 0.37 * side);
    head.add(eye);
    eyes.push(eye);
  }

  // --- повязка: лента вокруг головы на уровне глаз, узел сзади и два хвостика ---
  const EYE_DY = 0.13 - HEAD_C.y; // глаза относительно центра черепа
  const blindfold = new THREE.Group();
  blindfold.position.set(HEAD_C.x, HEAD_C.y, HEAD_C.z);
  blindfold.visible = false;
  head.add(blindfold);
  const band = new THREE.Mesh(geo(new THREE.CylinderGeometry(1, 1, 0.24, 56, 1, true)), clothMat);
  band.scale.set(HEAD_R.x * 1.13, 1, HEAD_R.z * 1.13);
  band.position.y = EYE_DY;
  blindfold.add(band);
  const knot = new THREE.Mesh(geo(new THREE.SphereGeometry(0.085, 16, 12)), knotMat);
  knot.position.set(-HEAD_R.x * 1.13 - 0.02, EYE_DY, 0);
  blindfold.add(knot);
  const tails: THREE.Group[] = [];
  for (const side of [1, -1]) {
    const pivot = new THREE.Group();
    pivot.position.set(knot.position.x - 0.03, EYE_DY, 0.02 * side);
    const strip = new THREE.Mesh(
      geo(
        blob(0.2, 0.035, 0.07, (v) => {
          v.y += 0.02 * Math.sin(v.x * 12);
        }, 24, 16),
      ),
      knotMat,
    );
    strip.position.set(-0.17, 0, 0);
    pivot.add(strip);
    pivot.rotation.y = 0.4 * side;
    blindfold.add(pivot);
    tails.push(pivot);
  }

  // --- свет: один доминирующий ключ, контровой сзади-сверху, почти нулевое заполнение ---
  scene.add(new THREE.HemisphereLight(0xffffff, 0x000000, 0.04));
  const key = new THREE.DirectionalLight(0xffffff, KEY_INTENSITY);
  key.position.set(-4.5, 5.5, 4);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.12);
  fill.position.set(5, -1, 3);
  scene.add(fill);
  const rim = new THREE.DirectionalLight(0xffffff, RIM_INTENSITY);
  rim.position.set(2, 5, -5);
  scene.add(rim);

  // габариты утки (с запасом) для подбора камеры
  const BOX_W = 3.6;
  const BOX_H = 2.5;
  const FILL = 0.7;
  const tanHalf = Math.tan((FOV * Math.PI) / 360);

  function update(timeSec: number, aspect: number, blind: number) {
    const t = timeSec;
    const sealed = smooth(0.55, 1, blind); // голова «прислушивается», когда повязка села

    // дыхание: грудь чуть поднимается
    const breath = Math.sin(t * 1.7);
    body.position.y = SIT_Y + 0.012 * breath;
    body.scale.set(1, 1 + 0.014 * breath, 1 + 0.008 * breath);
    body.rotation.set(0, 0, 0);

    // голова: медленное покачивание; раз в 7с косится в сторону, раз в 11с зевает носом вниз
    const glanceT = ((t % 7) + 7) % 7;
    const glance = bump(glanceT, 3.2, 4.6) * (Math.floor(t / 7) % 2 === 0 ? 1 : -1) * (1 - sealed);
    const dipT = ((t % 11) + 11) % 11;
    const dip = bump(dipT, 6.2, 7.6) * (1 - sealed);
    neck.position.x = NECK_X;
    neck.rotation.z = -0.1 + 0.025 * Math.sin(t * 0.6) + 0.1 * dip - 0.06 * sealed;
    head.rotation.z = 0.05 + 0.03 * Math.sin(t * 0.8 + 1) - 0.1 * dip + 0.1 * sealed;
    head.rotation.y = 0.45 * glance;

    // моргание: раз в 3.7с, быстро
    const blinkT = ((t % 3.7) + 3.7) % 3.7;
    const lid = bump(blinkT, 3.45, 3.65);
    for (const eye of eyes) eye.scale.y = 1 - 0.92 * lid;

    // хвост: редкая встряска; со сложенными крыльями лёгкое дыхание
    const wig = bump(((t % 9) + 9) % 9, 6.4, 7.6);
    tail.rotation.y = 0.32 * Math.sin(t * 15) * wig;
    tail.rotation.z = -0.5 + 0.04 * breath;
    wingNear.rotation.x = -0.012 * breath;
    wingFar.rotation.x = 0.012 * breath;
    jaw.rotation.z = 0;

    // повязка: опускается сверху, чуть перелетает и садится на глаза; хвостики качаются
    const b = easeOutBack(blind);
    blindfold.visible = blind > 0.002;
    blindfold.position.y = HEAD_C.y + (1 - b) * 0.75;
    blindfold.rotation.z = (1 - b) * 0.6;
    blindfold.scale.set(1, 0.85 + 0.15 * clamp01(blind * 1.4), 1);
    tails.forEach((p, i) => {
      const side = i === 0 ? 1 : -1;
      p.rotation.z = -0.35 + 0.18 * Math.sin(t * 2.4 + i * 1.7) - 0.5 * (1 - clamp01(blind));
      p.rotation.y = 0.4 * side + 0.08 * Math.sin(t * 1.9 + i);
    });

    // камера: утка занимает ~70% кадра при любом соотношении сторон
    const asp = Math.max(0.2, aspect);
    camera.aspect = asp;
    const visH = Math.max(BOX_H / FILL, BOX_W / FILL / asp);
    const dist = visH / (2 * tanHalf);
    camera.position.set(0.2, CENTER_Y + dist * 0.09, dist);
    camera.lookAt(0.2, CENTER_Y, 0);
    camera.updateProjectionMatrix();
  }

  update(0, 1, 0);

  return {
    scene,
    camera,
    root,
    update,
    dispose() {
      for (const g of geometries) g.dispose();
      for (const m of materials) m.dispose();
      scene.clear();
    },
  };
}
