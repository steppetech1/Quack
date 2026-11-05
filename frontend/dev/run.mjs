// `npm run dev:stub` and `npm run dev:real`: one command each, no env juggling on Windows.
//   node dev/run.mjs stub [-- next args]     stub backend (dev/stub-api.mjs) + next dev against it
//   node dev/run.mjs real [-- next args]     next dev against the real backend (QUACK_API_URL, default http://localhost:8000)
//   node dev/run.mjs mock                    next dev on the old mock data (no backend at all)
import { spawn } from "node:child_process";

const [mode = "stub", ...rest] = process.argv.slice(2);
const nextArgs = rest.filter((a) => a !== "--");
const stubPort = process.env.STUB_PORT ?? "8001";
const env = { ...process.env };
const procs = [];

if (mode === "stub") {
  env.NEXT_PUBLIC_QUACK_API = "1";
  env.QUACK_API_URL = `http://localhost:${stubPort}`;
  // the canvas WebSocket goes straight to the API (the `/api` proxy of Next does not carry upgrades)
  env.NEXT_PUBLIC_QUACK_WS ??= `ws://localhost:${stubPort}`;
  // the stub never asks for a login and sets no cookie: `/` stays the app (proxy.ts sends guests to the landing)
  env.QUACK_GUEST_LANDING = "0";
  procs.push(spawn(process.execPath, ["dev/stub-api.mjs"], { stdio: "inherit", env }));
} else if (mode === "real") {
  env.NEXT_PUBLIC_QUACK_API = "1";
  env.QUACK_API_URL ??= "http://localhost:8000";
  env.NEXT_PUBLIC_QUACK_WS ??= env.QUACK_API_URL.replace(/^http/, "ws");
} else if (mode === "mock") {
  delete env.NEXT_PUBLIC_QUACK_API;
} else {
  console.error(`unknown mode "${mode}": stub | real | mock`);
  process.exit(1);
}

console.log(`[dev] mode=${mode}${env.QUACK_API_URL && mode !== "mock" ? ` api=${env.QUACK_API_URL}` : ""}`);
procs.push(spawn("npx", ["next", "dev", ...nextArgs], { stdio: "inherit", env, shell: true }));

const stop = () => {
  for (const p of procs) p.kill();
  process.exit(0);
};
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
for (const p of procs) p.on("exit", () => stop());
