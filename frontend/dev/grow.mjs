// `npm run dev:grow`: the next step of growth of the Calculus graph in the stub (see dev/stub-api.mjs).
// To watch the "New" indicator: open the graph, leave it (open a topic, come back), run this, open the graph again.
// `npm run dev:grow -- reset` starts the stub data over.
const port = process.env.STUB_PORT ?? "8001";
const path = process.argv[2] === "reset" ? "reset" : "grow";
const res = await fetch(`http://localhost:${port}/__stub/${path}`, { method: "POST" });
const body = await res.json();
console.log(body.added ? `step ${body.stage}: ${body.added}` : (body.message ?? JSON.stringify(body)));
