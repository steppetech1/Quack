// The frames here are the ones backend/app/api/sse.py actually writes: `data: {json}\n\n` per event,
// `: keepalive\n\n` every 15 idle seconds, and nothing else. Chunk boundaries are whatever the
// network hands us, so the parser has to survive a split in the middle of anything.

import { describe, expect, it } from "vitest";
import { createFrameParser } from "./stream";

/** Feeds a whole stream in the given pieces and returns the payloads the parser emitted */
function run(chunks: string[]): string[] {
  const out: string[] = [];
  const feed = createFrameParser((data) => out.push(data));
  chunks.forEach(feed);
  return out;
}

const delta = (text: string) => `data: ${JSON.stringify({ type: "text_delta", text })}\n\n`;
const done = `data: ${JSON.stringify({
  type: "done",
  event_id: 7,
  mode: null,
  gave_task_instance_id: null,
  hint_level: null,
  referenced_skill_ids: [],
})}\n\n`;

describe("createFrameParser", () => {
  it("reads one frame per event", () => {
    expect(run([delta("При"), delta("вет"), done])).toEqual([
      '{"type":"text_delta","text":"При"}',
      '{"type":"text_delta","text":"вет"}',
      done.slice("data: ".length, -2),
    ]);
  });

  it("joins frames split across chunks", () => {
    const whole = delta("hello") + done;
    for (let cut = 1; cut < whole.length; cut++) {
      expect(run([whole.slice(0, cut), whole.slice(cut)])).toEqual(run([whole]));
    }
  });

  it("survives one byte at a time", () => {
    const whole = delta("a") + delta("b") + done;
    expect(run(whole.split(""))).toEqual(run([whole]));
  });

  it("drops keepalive comments", () => {
    expect(run([": keepalive\n\n", delta("x"), ": keepalive\n\n", done])).toHaveLength(2);
  });

  it("accepts CRLF line endings from proxies", () => {
    const crlf = (delta("x") + done).replace(/\n/g, "\r\n");
    expect(run([crlf])).toEqual(run([delta("x") + done]));
  });

  it("keeps an unterminated tail buffered instead of emitting half a frame", () => {
    const out: string[] = [];
    const feed = createFrameParser((d) => out.push(d));
    feed('data: {"type":"text_delta","te');
    expect(out).toEqual([]);
    feed('xt":"ok"}\n\n');
    expect(out).toEqual(['{"type":"text_delta","text":"ok"}']);
  });

  it("joins multi-line data fields the way SSE specifies", () => {
    expect(run(["data: first\ndata: second\n\n"])).toEqual(["first\nsecond"]);
  });

  it("tolerates a missing space after `data:`", () => {
    expect(run(['data:{"type":"error","code":"internal","message":"x"}\n\n'])).toEqual([
      '{"type":"error","code":"internal","message":"x"}',
    ]);
  });
});
