import * as THREE from "three";
import { mergeVertices } from "three/examples/jsm/utils/BufferGeometryUtils.js";

/**
 * 3D-утка из примитивов three.js (без моделей). Серые материалы и контрастный свет:
 * движок штриховки превращает яркость в толщину штриха, поэтому форма читается только через тон —
 * освещённая сторона плотная и яркая, теневая рвётся на редкие тонкие штрихи и местами чёрная.
 * Утка в профиль, смотрит вправо (+X), слегка довёрнута к камере. Ходьба на месте — процедурная, петля 1.2s.
 */

export type Duck = {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  /** Корень модели: снаружи им можно довернуть утку (поворот к курсору) */
  root: THREE.Group;
  update(timeSec: number, aspect: number): void;
  dispose(): void;
};

const TAU = Math.PI * 2;
const PERIOD = 1.2; // период шага, с
const RUFFLE_CYCLE = PERIOD * 4; // раз в 4 шага — встряска крыльев и «кряк»
const STRIDE = 0.4; // sin угла выноса ноги
const LEG = 0.6; // длина голени
const HIP_Y = -0.45;
const CENTER_Y = 0.3; // куда смотрит камера
const FOV = 24;
const KEY_INTENSITY = 4.2;
const TONE_GAMMA = 2.0; // показатель кривой яркости (>1 — контрастнее)
const TONE_GAIN = 4.0;
const RIM_INTENSITY = 2.6;

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
      // крупные «перья» + мелкая рябь, считаем по направлению на единичной сфере
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

/** Перепончатая лапа: плоская, лежит в XZ, носки вперёд (+X) */
function webbedFoot(): THREE.BufferGeometry {
  const s = new THREE.Shape();
  s.moveTo(-0.08, 0);
  s.quadraticCurveTo(0.12, 0.24, 0.5, 0.27); // внешний край до левого пальца
  s.quadraticCurveTo(0.58, 0.2, 0.46, 0.15);
  s.quadraticCurveTo(0.4, 0.07, 0.62, 0.02); // перепонка до среднего пальца
  s.quadraticCurveTo(0.68, 0, 0.62, -0.02);
  s.quadraticCurveTo(0.4, -0.07, 0.46, -0.15); // перепонка до правого пальца
  s.quadraticCurveTo(0.58, -0.2, 0.5, -0.27);
  s.quadraticCurveTo(0.12, -0.24, -0.08, 0);
  const depth = 0.03;
  const g = new THREE.ExtrudeGeometry(s, {
    depth,
    curveSegments: 14,
    bevelEnabled: true,
    bevelSize: 0.02,
    bevelThickness: 0.015,
    bevelSegments: 3,
  });
  g.rotateX(-Math.PI / 2); // форма ложится на пол, толщина вверх
  g.translate(0, -depth / 2, 0);
  g.scale(1.05, 1, 1.05);
  return g;
}

export function createDuck(): Duck {
  const scene = new THREE.Scene(); // фон не задаём: движок чистит в чёрный
  const camera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 80);

  const geometries: THREE.BufferGeometry[] = [];
  const materials: THREE.Material[] = [];
  const geo = (g: THREE.BufferGeometry) => {
    geometries.push(g);
    return g;
  };
  const mat = (color: number, roughness: number) => {
    const m = new THREE.MeshStandardMaterial({ color, roughness, metalness: 0 });
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

  // тёмный базовый альбедо + высокая шероховатость: тень падает почти в ноль, свет остаётся плотным.
  // Части разведены по тону: тело среднее, крыло темнее (яркая кромка — от контрового), клюв ярче и глянцевее, лапы темнее
  const featherMat = mat(0x9a9a9a, 0.9);
  const wingMat = mat(0x707070, 0.85);
  const beakMat = mat(0xd2d2d2, 0.4);
  const legMat = mat(0x6a6a6a, 0.6);
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
          const s = 1 - 0.5 * Math.pow(k, 1.6); // сужение к хвосту
          v.y *= s;
          v.z *= s;
          v.y += 0.62 * k * k; // хвост приподнят
        } else {
          v.y -= 0.1 * u * u; // грудь чуть ниже
        }
        if (v.y < 0) v.y *= 0.92; // низ плоскее
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
      const k = clamp01((-v.x / 0.46 + 1) / 2); // 0 у основания … 1 на кончике
      v.z *= 0.55 + 0.7 * Math.sin(Math.PI * Math.min(1, k * 0.9 + 0.1)); // веер: шире в середине, острый кончик
      v.y += 0.035 * Math.sin(v.z * 22); // бороздки вдоль пера
    }, 56, 36, 0.05, 3, 5.1),
    featherMat,
  );
  tailMesh.position.set(-0.32, 0, 0);
  tail.add(tailMesh);

  // --- крылья ---
  const makeWing = (side: 1 | -1) => {
    const g = geo(
      blob(0.8, 0.46, 0.1, (v) => {
        const k = clamp01((-v.x / 0.8 + 1) / 2); // к кончику (назад) уже
        v.y *= 1 - 0.65 * k;
        v.z *= 1 - 0.4 * k;
        v.y += 0.14 * k * k; // кончик загнут вверх
        v.z -= side * 0.08 * (v.y / 0.46) ** 2; // края прижаты к телу
        // неглубокие гребни маховых перьев: идут назад и чуть вниз, кончики рядов чуть торчат
        const row = Math.sin(v.y * 19 - v.x * 3.5);
        v.z += side * (0.03 * row + 0.015 * Math.sin(v.y * 41 + v.x * 6)) * (0.4 + 0.6 * k);
      }, 72, 44, 0.03, 3.5, 2.7),
    );
    const pivot = new THREE.Group();
    pivot.position.set(0.4, 0.3, 0.8 * side);
    pivot.rotation.y = -0.3 * side; // кончик прижат к телу
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
  // шея: наклонённая «колба», внизу шире (плавно переходит в грудь)
  const neckMesh = mesh(
    blob(0.3, 0.55, 0.3, (v) => {
      const t = clamp01((v.y / 0.55 + 1) / 2); // 0 низ … 1 верх
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
  const headMesh = mesh(
    blob(0.5, 0.45, 0.43, (v) => {
      if (v.y < 0) v.y *= 0.9; // щёки чуть плоскее
    }, 56, 36, 0.032, 4, 8.2),
    featherMat,
  );
  headMesh.position.set(0.05, 0.08, 0);
  head.add(headMesh);

  // клюв: широкий и плоский, к концу шире
  const beakDeform = (rx: number, drop: number) => (v: THREE.Vector3) => {
    const k = clamp01((v.x / rx + 1) / 2); // 0 у головы … 1 кончик
    v.z *= 0.72 + 0.4 * smooth(0, 1, k);
    v.y -= drop * k * k;
  };
  const beakUp = mesh(blob(0.5, 0.1, 0.29, beakDeform(0.5, 0.035), 40, 24), beakMat);
  beakUp.position.set(0.6, -0.02, 0);
  head.add(beakUp);
  const jaw = new THREE.Group(); // нижняя половина на шарнире: «кряк»
  jaw.position.set(0.2, -0.13, 0);
  head.add(jaw);
  const beakLow = mesh(blob(0.42, 0.06, 0.23, beakDeform(0.42, 0.02), 40, 24), beakMat);
  beakLow.position.set(0.34, 0, 0);
  jaw.add(beakLow);

  // глаза — чёрные точки, в штриховке дают «дырки»
  const eyeGeo = geo(new THREE.SphereGeometry(0.065, 16, 12));
  for (const side of [1, -1]) {
    const eye = new THREE.Mesh(eyeGeo, eyeMat);
    eye.position.set(0.22, 0.13, 0.37 * side);
    head.add(eye);
  }

  // --- ноги: бедро (перья) + голень + лапа ---
  const thighGeo = geo(blob(0.22, 0.3, 0.2, undefined, 40, 28, 0.05, 3.5, 6.6));
  const shinGeo = geo(new THREE.CylinderGeometry(0.085, 0.07, LEG, 14));
  shinGeo.translate(0, -LEG / 2, 0);
  const footGeo = geo(webbedFoot());

  const makeLeg = (side: 1 | -1) => {
    const hip = new THREE.Group(); // шарнир бедра
    hip.position.set(-0.15, HIP_Y, 0.4 * side);
    const thigh = new THREE.Mesh(thighGeo, featherMat);
    thigh.position.y = 0.02;
    hip.add(thigh);
    hip.add(new THREE.Mesh(shinGeo, legMat));
    const ankle = new THREE.Group(); // голеностоп: держит стопу плоско
    ankle.position.y = -LEG;
    hip.add(ankle);
    const foot = new THREE.Mesh(footGeo, legMat);
    foot.rotation.y = -0.2 * side; // носки чуть врозь
    ankle.add(foot);
    root.add(hip);
    return { hip, ankle };
  };
  const legNear = makeLeg(1);
  const legFar = makeLeg(-1);

  // шаг одной ноги: phase 0..1; 0..0.5 опора (нога едет назад), 0.5..1 вынос с подъёмом
  const stepLeg = (leg: { hip: THREE.Group; ankle: THREE.Group }, phase: number, side: 1 | -1) => {
    let s: number; // sin угла
    let lift = 0;
    let toe = 0;
    if (phase < 0.5) {
      s = STRIDE * (1 - 2 * (phase / 0.5)); // линейно, стопа стоит на месте
    } else {
      const v = (phase - 0.5) / 0.5;
      s = STRIDE * (-1 + 2 * smooth(0, 1, v));
      lift = Math.sin(Math.PI * v) * 0.2;
      toe = Math.sin(Math.PI * v);
    }
    const a = Math.asin(s);
    leg.hip.rotation.z = a;
    leg.hip.position.y = HIP_Y + LEG * (Math.cos(a) - 1) + lift; // стопа на уровне земли
    leg.ankle.rotation.z = -a - 0.45 * toe; // стопа плоско, в выносе носок вниз
    leg.hip.position.z = 0.4 * side;
  };

  // --- свет: один доминирующий ключ спереди-сверху-слева (от камеры), почти нулевое заполнение,
  // контровой сзади-сверху обводит спину и верхний край. Мир = система камеры (она стоит на +Z). ---
  const ambient = new THREE.HemisphereLight(0xffffff, 0x000000, 0.04);
  scene.add(ambient);
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
  const BOX_W = 3.9;
  const BOX_H = 3.0;
  const FILL = 0.7; // доля кадра
  const tanHalf = Math.tan((FOV * Math.PI) / 360);

  function update(timeSec: number, aspect: number) {
    const ph = (((timeSec / PERIOD) % 1) + 1) % 1;
    const a = ph * TAU;
    const cyc = (((timeSec % RUFFLE_CYCLE) + RUFFLE_CYCLE) % RUFFLE_CYCLE) / RUFFLE_CYCLE; // 0..1 за 4 шага

    // ноги: ближняя и дальняя в противофазе
    stepLeg(legNear, ph, 1);
    stepLeg(legFar, (ph + 0.5) % 1, -1);

    // переваливание: крен на опорную ногу, покачивание, подпрыгивание на каждом шаге
    body.rotation.x = 0.1 * Math.sin(a);
    body.rotation.y = 0.07 * Math.sin(a + Math.PI / 2);
    body.position.z = 0.05 * Math.sin(a);
    body.position.y = -0.045 * Math.cos(2 * a);

    // голова кивает вперёд-назад дважды за период, в такт шагам
    const nod = Math.sin(2 * a - 0.6);
    neck.position.x = NECK_X + 0.06 * nod;
    neck.rotation.z = -0.1 - 0.1 * nod;
    head.rotation.z = 0.08 * nod + 0.05; // клюв остаётся почти горизонтальным

    // встряска: окно в цикле из 4 шагов
    const env = bump(cyc, 0.62, 0.8);
    const shake = Math.sin(timeSec * TAU * 9) * env;
    const flap = 0.05 * Math.sin(2 * a + 1.5); // лёгкое дыхание крыльев в ходьбе
    wingNear.rotation.x = -(0.5 * env + 0.35 * shake * env) + flap;
    wingFar.rotation.x = 0.5 * env + 0.35 * shake * env - flap;

    // «кряк»: клюв открывается дважды
    const quack = bump(cyc, 0.62, 0.8) * Math.abs(Math.sin(timeSec * TAU * 4.5));
    jaw.rotation.z = -0.28 * quack;

    // хвост виляет в такт шагам, на встряске чаще
    tail.rotation.y = 0.28 * Math.sin(2 * a + 1.1) + 0.22 * shake;
    tail.rotation.z = -0.5 + 0.06 * Math.sin(2 * a);

    // камера: утка занимает ~70% кадра при любом соотношении сторон
    const asp = Math.max(0.2, aspect);
    camera.aspect = asp;
    const visH = Math.max(BOX_H / FILL, BOX_W / FILL / asp);
    const dist = visH / (2 * tanHalf);
    camera.position.set(0.2, CENTER_Y + dist * 0.09, dist);
    camera.lookAt(0.2, CENTER_Y, 0);
    camera.updateProjectionMatrix();
  }

  update(0, 1);

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
