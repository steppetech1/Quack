import * as THREE from "three";
import { mergeVertices } from "three/examples/jsm/utils/BufferGeometryUtils.js";

/**
 * 3D-утка первого экрана: стоит на лапах, развёрнута к зрителю на 3/4, ходит, разворачивается другим боком,
 * вертит головой, вытягивается на лапах. Модель из примитивов three.js (подход как в scanline/duck-3d.ts),
 * но поза управляется снаружи (DuckPose), а не зациклена. Материалы серые: решётка глифов читает только яркость,
 * поэтому важен свет — ключевой справа сверху, контровой сзади, как в референсе с лестницей.
 * Локальная ось +X — куда смотрит клюв. Земля — y = GROUND.
 */

export type DuckPose = {
  /** Мировая координата X утки */
  x: number;
  /** Поворот корпуса вокруг Y, рад: -0.6 — 3/4 вправо к зрителю, -π+0.6 — 3/4 влево к зрителю */
  facing: number;
  /** Фаза шага, 0..1 (крутится, пока утка идёт) */
  step: number;
  /** 0..1: насколько утка идёт (амплитуда шага) */
  walk: number;
  /** 0..1: вытянулась на лапах, грудь вверх, шея вверх */
  stretch: number;
  /** Поворот головы вокруг Y относительно корпуса, рад (минус — к зрителю и дальше) */
  headYaw: number;
  /** Наклон головы вверх/вниз, рад (плюс — клюв вверх) */
  headPitch: number;
  /** 0..1: клюв открыт */
  jaw: number;
  /** 0..1: взмах крыльями */
  flap: number;
  /** Время, с: дыхание и мелкие движения */
  time: number;
  /** 0..1: полёт — крылья раскрыты и машут, шея вытянута вперёд, лапы поджаты. Без неё утка стоит */
  flight?: number;
  /** Подъём над землёй, мировые единицы (в полёте — покачивание) */
  y?: number;
};

export const GROUND = -0.82; // короткие лапы: у настоящей утки видна только цевка
const HIP_Y = -0.45;
const STRIDE = 0.38;
const TONE_GAMMA = 1.7;
const TONE_GAIN = 3.2;

const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const smooth = (a: number, b: number, x: number) => {
  const t = clamp01((x - a) / (b - a));
  return t * t * (3 - 2 * t);
};
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
    lerp(lerp(hash3(xi, yi, zi + dz), hash3(xi + 1, yi, zi + dz), u), lerp(hash3(xi, yi + 1, zi + dz), hash3(xi + 1, yi + 1, zi + dz), u), w);
  return lerp(plane(0), plane(1), q);
}

/** Гладкая «капля» с перьевой рябью: сфера, растянутая и деформированная, нормали пересчитаны */
function blob(rx: number, ry: number, rz: number, deform?: (v: THREE.Vector3) => void, seg = 56, bumps = 0, freq = 4, seed = 0) {
  let g: THREE.BufferGeometry = new THREE.SphereGeometry(1, seg, Math.round(seg * 0.65));
  g.deleteAttribute("uv");
  g.deleteAttribute("normal");
  g = mergeVertices(g);
  const pos = g.getAttribute("position");
  const v = new THREE.Vector3();
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i);
    if (bumps > 0) {
      const n = noise3(v.x * freq + seed, v.y * freq, v.z * freq) + 0.5 * noise3(v.x * freq * 2.3 + seed, v.y * freq * 2.3 + 7, v.z * freq * 2.3);
      v.multiplyScalar(1 + bumps * n);
    }
    v.set(v.x * rx, v.y * ry, v.z * rz);
    deform?.(v);
    pos.setXYZ(i, v.x, v.y, v.z);
  }
  g.computeVertexNormals();
  return g;
}

function webbedFoot(): THREE.BufferGeometry {
  const s = new THREE.Shape();
  s.moveTo(-0.08, 0);
  s.quadraticCurveTo(0.12, 0.24, 0.5, 0.27);
  s.quadraticCurveTo(0.58, 0.2, 0.46, 0.15);
  s.quadraticCurveTo(0.4, 0.07, 0.62, 0.02);
  s.quadraticCurveTo(0.68, 0, 0.62, -0.02);
  s.quadraticCurveTo(0.4, -0.07, 0.46, -0.15);
  s.quadraticCurveTo(0.58, -0.2, 0.5, -0.27);
  s.quadraticCurveTo(0.12, -0.24, -0.08, 0);
  const g = new THREE.ExtrudeGeometry(s, { depth: 0.03, curveSegments: 12, bevelEnabled: true, bevelSize: 0.02, bevelThickness: 0.015, bevelSegments: 2 });
  g.rotateX(-Math.PI / 2);
  g.translate(0, -0.015, 0);
  return g;
}

export type DuckRig = {
  scene: THREE.Scene;
  root: THREE.Group;
  /** Мировая точка кончика клюва (после apply) */
  beakTip(out: THREE.Vector3): THREE.Vector3;
  /** Мировой центр головы (после apply) */
  headCenter(out: THREE.Vector3): THREE.Vector3;
  apply(p: DuckPose): void;
  dispose(): void;
};

export function createDuckRig(): DuckRig {
  const scene = new THREE.Scene();
  const geometries: THREE.BufferGeometry[] = [];
  const materials: THREE.Material[] = [];
  const geo = <G extends THREE.BufferGeometry>(g: G) => {
    geometries.push(g);
    return g;
  };
  const mat = (color: number, roughness: number, vertexColors = false) => {
    const m = new THREE.MeshStandardMaterial({ color, roughness, metalness: 0, vertexColors });
    // крутая кривая: тень быстро уходит в тёмные глифы, свет остаётся плотным
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
  // тона как у кряквы: светлые бока, тёмная голова и шея, белый ошейник, ещё темнее крыло, яркий клюв
  // Цвет материалов служебный: решётка глифов по набору ярких каналов узнаёт часть утки (нейтральный — тело, пурпурный — лапы,
  // зелёный — голова, жёлтый — клюв, красный — грудь, синий — крыло и спина, голубой — ошейник), а яркость берёт
  // по самому яркому каналу — она та же, что у прежних серых материалов. Видимые цвета — DUCK_RAMPS в glyphs.ts.
  const featherMat = mat(0xa8a8a8, 0.9);
  const bodyMat = mat(0xa8a8a8, 0.9, true);
  const headMat = mat(0x006800, 0.55); // чуть светлее прежнего 0x5c
  const collarMat = mat(0x00f0f0, 0.8);
  const wingMat = mat(0x00006a, 0.8);
  const mirrorMat = mat(0x0000ff, 0.5);
  const beakMat = mat(0xe0e000, 0.4);
  const legMat = mat(0xb800b8, 0.6); // пурпурная метка: лапы в своей (оранжевой) палитре; ярче, чтобы не уходили в общую тень
  const eyeMat = new THREE.MeshBasicMaterial({ color: 0x000000 });
  materials.push(eyeMat);
  const mesh = (g: THREE.BufferGeometry, m: THREE.Material) => new THREE.Mesh(geo(g), m);

  const root = new THREE.Group();
  scene.add(root);

  // --- тело ---
  const body = new THREE.Group(); // шарнир у бёдер: при вытягивании корпус поднимается и запрокидывается
  body.position.set(-0.1, 0, 0);
  root.add(body);
  const BODY_RX = 1.25;
  const bodyGeo = blob(BODY_RX, 0.6, 0.58, (v) => {
        const u = v.x / BODY_RX;
        if (u < 0) {
          const k = -u;
          const s = 1 - 0.55 * Math.pow(k, 1.5); // к хвосту сужается в клин
          v.y *= s;
          v.z *= s;
          v.y += 0.4 * k * k;
        } else {
          v.y -= 0.06 * u * u;
          v.z *= 1 - 0.12 * u * u; // грудь уже, чем у «мячика»
        }
        if (v.y < 0) v.y *= 0.9;
      }, 64, 0.05, 3.4, 1.3);
  {
    // грудь спереди помечена красным: в палитре она чуть рыжее тела
    const pos = bodyGeo.getAttribute("position");
    const col = new Float32Array(pos.count * 3);
    for (let k = 0; k < pos.count; k++) {
      const chest = smooth(0.45, 0.75, pos.getX(k) / BODY_RX) * (1 - smooth(0.25, 0.45, pos.getY(k)));
      col[k * 3] = 1;
      col[k * 3 + 1] = col[k * 3 + 2] = 1 - chest;
    }
    bodyGeo.setAttribute("color", new THREE.BufferAttribute(col, 3));
  }
  const bodyMesh = mesh(bodyGeo, bodyMat);
  body.add(bodyMesh);

  // хвост
  const tail = new THREE.Group();
  tail.position.set(-1.08, 0.34, 0);
  body.add(tail);
  const tailMesh = mesh(
    blob(0.34, 0.05, 0.2, (v) => {
      const k = clamp01((-v.x / 0.34 + 1) / 2);
      v.z *= 0.55 + 0.7 * Math.sin(Math.PI * Math.min(1, k * 0.9 + 0.1));
      v.y += 0.035 * Math.sin(v.z * 22);
    }, 48, 0.05, 3, 5.1),
    featherMat,
  );
  tailMesh.position.set(-0.22, 0, 0);
  tail.add(tailMesh);

  // крылья с «зеркальцем»
  const makeWing = (side: 1 | -1) => {
    const g = blob(0.86, 0.36, 0.08, (v) => {
      const k = clamp01((-v.x / 0.86 + 1) / 2);
      v.y *= 1 - 0.65 * k;
      v.z *= 1 - 0.4 * k;
      v.y += 0.14 * k * k;
      v.z -= side * 0.08 * (v.y / 0.36) ** 2;
      const row = Math.sin(v.y * 19 - v.x * 3.5);
      v.z += side * (0.03 * row + 0.015 * Math.sin(v.y * 41 + v.x * 6)) * (0.4 + 0.6 * k);
    }, 64, 0.03, 3.5, 2.7);
    const pivot = new THREE.Group();
    pivot.position.set(0.4, 0.22, 0.56 * side);
    pivot.rotation.y = -0.3 * side;
    const m = mesh(g, wingMat);
    m.position.set(-0.6, -0.1, 0);
    m.rotation.z = -0.16;
    pivot.add(m);
    const mirror = mesh(blob(0.26, 0.045, 0.03, undefined, 24), mirrorMat);
    mirror.position.set(-0.72, -0.14, 0.06 * side);
    mirror.rotation.z = -0.2;
    pivot.add(mirror);
    body.add(pivot);
    return pivot;
  };
  const wingNear = makeWing(1);
  const wingFar = makeWing(-1);

  // раскрытые крылья для полёта: размах вдоль Z, к кончику сужаются и загибаются вверх и назад
  const SPAN = 1.55;
  const flightWingMat = mat(0x0000e8, 0.8); // ярче сложенного крыла: раскрытое освещено сверху и спереди
  const makeSpread = (side: 1 | -1) => {
    const g = blob(0.5, 0.04, SPAN, (v) => {
      const t = clamp01((v.z * side + SPAN) / (2 * SPAN)); // 0 — у корпуса, 1 — кончик
      v.x = v.x * (1 - 0.5 * t) - 0.34 * t * t; // хорда сужается, задняя кромка уходит назад
      v.y += 0.14 * t * t; // кончик чуть загнут вверх
      v.y += 0.03 * Math.sin(v.x * 14 + v.z * side * 3) * t; // перьевая рябь
    }, 64, 0.02, 3, 3.1);
    g.translate(0, 0, side * SPAN); // корень крыла — в начале координат
    const pivot = new THREE.Group();
    pivot.position.set(0.28, 0.36, 0.22 * side);
    pivot.add(mesh(g, flightWingMat));
    body.add(pivot);
    pivot.visible = false;
    return pivot;
  };
  const spreadNear = makeSpread(1);
  const spreadFar = makeSpread(-1);

  // --- шея и голова ---
  const neck = new THREE.Group();
  neck.position.set(0.9, 0.26, 0);
  body.add(neck);
  const neckMesh = mesh(
    blob(0.16, 0.44, 0.16, (v) => {
      const t = clamp01((v.y / 0.44 + 1) / 2);
      const flare = 1 + 1.4 * Math.pow(1 - t, 2.2); // тонкая шея плавно уходит в грудь
      v.x = v.x * flare + 0.1 * t - 0.06 * Math.sin(Math.PI * t); // лёгкий S-изгиб
      v.z *= flare;
    }, 48, 0.04, 3.5, 4.4),
    headMat,
  );
  neckMesh.position.set(0, 0.16, 0);
  neck.add(neckMesh);
  const collar = mesh(new THREE.TorusGeometry(0.17, 0.022, 8, 32), collarMat);
  collar.rotation.x = Math.PI / 2;
  collar.position.set(0.04, 0.02, 0);
  neck.add(collar);

  const head = new THREE.Group();
  head.position.set(0.12, 0.5, 0);
  neck.add(head);
  // голова маленькая и вытянутая, темя плоское, лоб длинным скатом переходит в клюв
  const headMesh = mesh(blob(0.3, 0.22, 0.19, (v) => {
    if (v.y > 0) v.y *= 0.85;
    if (v.x > 0.04) v.y -= 0.3 * (v.x - 0.04);
    if (v.x < -0.1) v.y += 0.05 * (-v.x - 0.1); // затылок чуть выше
  }, 48, 0.02, 4, 8.2), headMat);
  headMesh.position.set(0.02, 0.04, 0);
  head.add(headMesh);
  const beakDeform = (rx: number, drop: number) => (v: THREE.Vector3) => {
    const k = clamp01((v.x / rx + 1) / 2);
    v.z *= 0.72 + 0.4 * smooth(0, 1, k);
    v.y -= drop * k * k;
  };
  const beakUp = mesh(blob(0.34, 0.05, 0.12, beakDeform(0.34, 0.03), 36), beakMat);
  beakUp.position.set(0.4, -0.02, 0);
  head.add(beakUp);
  const jaw = new THREE.Group();
  jaw.position.set(0.12, -0.06, 0);
  head.add(jaw);
  const beakLow = mesh(blob(0.28, 0.03, 0.1, beakDeform(0.28, 0.015), 36), beakMat);
  beakLow.position.set(0.24, 0, 0);
  jaw.add(beakLow);
  const tip = new THREE.Object3D();
  tip.position.set(0.74, -0.04, 0);
  head.add(tip);
  const eyeGeo = geo(new THREE.SphereGeometry(0.032, 10, 8));
  for (const side of [1, -1]) {
    const eye = new THREE.Mesh(eyeGeo, eyeMat);
    eye.position.set(0.12, 0.08, 0.17 * side);
    head.add(eye);
  }

  // --- ноги ---
  const thighGeo = geo(blob(0.16, 0.2, 0.14, undefined, 32, 0.05, 3.5, 6.6));
  const shinGeo = geo(new THREE.CylinderGeometry(0.045, 0.038, 1, 10));
  shinGeo.translate(0, -0.5, 0);
  const footGeo = geo(webbedFoot().scale(0.62, 1, 0.62));
  const makeLeg = (side: 1 | -1) => {
    const hip = new THREE.Group();
    const thigh = new THREE.Mesh(thighGeo, featherMat);
    thigh.position.y = 0.02;
    hip.add(thigh);
    const shin = new THREE.Mesh(shinGeo, legMat);
    hip.add(shin);
    const ankle = new THREE.Group();
    hip.add(ankle);
    const foot = new THREE.Mesh(footGeo, legMat);
    foot.rotation.y = -0.2 * side;
    ankle.add(foot);
    root.add(hip);
    return { hip, shin, ankle, side };
  };
  const legs = [makeLeg(1), makeLeg(-1)];

  // --- свет ---
  scene.add(new THREE.HemisphereLight(0xffffff, 0x404040, 0.2));
  const key = new THREE.DirectionalLight(0xffffff, 3.6);
  key.position.set(4.5, 5.5, 4); // справа сверху спереди
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.7);
  fill.position.set(-2, -2, 6); // снизу спереди: живот читается тусклыми глифами, а не пропадает
  scene.add(fill);
  const rim = new THREE.DirectionalLight(0xffffff, 2.4);
  rim.position.set(-2, 5, -5);
  scene.add(rim);

  const apply = (p: DuckPose) => {
    const s = p.stretch;
    const lift = 0.3 * s; // насколько поднялся корпус над обычным
    root.position.x = p.x;
    root.rotation.y = p.facing;

    const ph = p.step * Math.PI * 2;
    const w = p.walk;
    const breathe = Math.sin(p.time * 1.9) * 0.02 * (1 - w);

    body.position.y = lift - 0.04 * Math.cos(2 * ph) * w + breathe;
    body.rotation.z = 0.12 + 0.34 * s + 0.05 * Math.sin(2 * ph) * w; // грудь вверх
    body.rotation.x = 0.08 * Math.sin(ph) * w; // переваливается с лапы на лапу
    tail.rotation.y = 0.25 * Math.sin(2 * ph + 1.1) * w + 0.06 * Math.sin(p.time * 2.3);
    tail.rotation.z = -0.22 + 0.25 * s; // при вытягивании хвост опускается

    // шея вытягивается и выпрямляется, голова держит клюв горизонтально
    neck.rotation.z = 0.32 * s - 0.08 * Math.sin(2 * ph - 0.6) * w;
    neckMesh.scale.y = 1 + 0.35 * s;
    neckMesh.position.y = 0.16 * (1 + 0.35 * s);
    collar.position.y = 0.0 + 0.05 * s;
    head.position.y = 0.5 + 0.16 * s;
    neck.position.x = 0.9 + 0.05 * Math.sin(2 * ph - 0.6) * w; // кивок в такт шагам
    head.rotation.order = "YZX";
    head.rotation.y = p.headYaw;
    head.rotation.z = -0.66 * s + p.headPitch - 0.08;
    jaw.rotation.z = -0.38 * p.jaw;

    const flap = p.flap;
    const beat = Math.sin(p.time * 22) * flap;
    wingNear.rotation.x = -(0.9 * flap + 0.4 * beat);
    wingFar.rotation.x = 0.9 * flap + 0.4 * beat;

    // полёт: поверх обычной позы. Сложенные крылья убираются, раскрытые машут (вверх до ~65°, вниз ниже корпуса)
    const f = p.flight ?? 0;
    root.position.y = p.y ?? 0;
    const flying = f > 0.02;
    bodyMesh.scale.set(1, 1 - 0.3 * f, 1 - 0.24 * f); // в полёте корпус стройнее: вытянутый, а не «шар»
    spreadNear.visible = flying;
    spreadFar.visible = flying;
    wingNear.visible = f < 0.5;
    wingFar.visible = f < 0.5;
    if (flying) {
      // взмахи сериями: несколько неспешных (~1,5 в секунду), потом парение с поднятыми и почти неподвижными крыльями
      const m = (p.time % 4.4) / 4.4; // цикл 4,4 с: первая половина — взмахи, вторая — парение
      const env = Math.sin(Math.PI * Math.min(1, m / 0.5)) * (m < 0.5 ? 1 : 0); // 0..1..0 в первой половине цикла
      const w = Math.sin(p.time * 9.4) * env;
      const up = 0.3 + 0.85 * w + 0.04 * Math.sin(p.time * 1.7); // в парении крылья чуть приподняты и еле заметно играют
      for (const [sp, sd] of [[spreadNear, 1], [spreadFar, -1]] as const) {
        sp.scale.setScalar(Math.max(0.001, f));
        sp.rotation.x = -sd * up * f;
        sp.rotation.y = -0.16 * sd * f; // крыло чуть подано вперёд
      }
      body.position.y += 0.14 * w * f; // корпус вздымается с каждым взмахом
      body.rotation.z = body.rotation.z * (1 - f) + (0.04 - 0.07 * w) * f;
      neck.rotation.z = neck.rotation.z * (1 - f) - 1.18 * f;
      head.rotation.z = head.rotation.z * (1 - f) + (1.18 - 0.08 + p.headPitch) * f;
      tail.rotation.z = tail.rotation.z * (1 - f) - 0.02 * f;
    }
    for (const leg of legs) leg.hip.scale.setScalar(Math.max(0.001, 1 - f)); // в полёте лапы поджаты

    // ноги: бедро привязано к корпусу, стопа — к земле; голень тянется, когда утка поднимается
    for (const leg of legs) {
      const phase = leg.side === 1 ? p.step : (p.step + 0.5) % 1;
      let sn: number;
      let up = 0;
      let toe = 0;
      if (phase < 0.5) sn = STRIDE * (1 - 2 * (phase / 0.5));
      else {
        const v = (phase - 0.5) / 0.5;
        sn = STRIDE * (-1 + 2 * smooth(0, 1, v));
        up = Math.sin(Math.PI * v) * 0.18;
        toe = Math.sin(Math.PI * v);
      }
      sn *= w;
      up *= Math.min(1, w * 2.5); // лапу поднимает заметно, даже когда шаг короткий
      toe *= w;
      const a = Math.asin(sn);
      const hipY = HIP_Y + body.position.y;
      const len = (hipY - GROUND - up) / Math.cos(a);
      leg.hip.position.set(-0.02, hipY, 0.26 * leg.side);
      leg.hip.rotation.z = a;
      leg.shin.scale.y = len;
      leg.ankle.position.y = -len;
      leg.ankle.rotation.z = -a - 0.45 * toe;
    }
  };

  return {
    scene,
    root,
    beakTip(out) {
      scene.updateMatrixWorld(true);
      return tip.getWorldPosition(out);
    },
    headCenter(out) {
      scene.updateMatrixWorld(true);
      return headMesh.getWorldPosition(out);
    },
    apply,
    dispose() {
      for (const g of geometries) g.dispose();
      for (const m of materials) m.dispose();
      scene.clear();
    },
  };
}
