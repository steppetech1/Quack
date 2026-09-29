// The realism scale is the one thing the two sides word differently, and the difference is invisible
// on screen: a wrong mapping does not crash, it just tells the student the wrong thing about their
// chances. So it is pinned here rather than checked by eye (ТЗ §7.1, F2.2).

import { describe, expect, it } from "vitest";
import type { BackendMatch } from "@/api/backend";
import { REALISM_LEVEL } from "./catalog";
import { LEVEL_LABEL, type Level } from "./programs";

describe("REALISM_LEVEL", () => {
  it("keeps the backend's three verdicts in the student's words", () => {
    expect(REALISM_LEVEL).toEqual({
      possible: "realistic",
      try: "try",
      impossible: "unlikely",
    });
  });

  it("covers every realism the backend can send, and nothing else", () => {
    // The type already makes the build fail on a missing key; this catches a stray one
    const backendSide: BackendMatch["realism"][] = ["possible", "try", "impossible"];
    expect(Object.keys(REALISM_LEVEL).sort()).toEqual([...backendSide].sort());
  });

  it("lands on levels the screens can label", () => {
    for (const level of Object.values(REALISM_LEVEL)) {
      expect(LEVEL_LABEL[level as Level]).toBeTruthy();
    }
  });

  it("never turns a refusal into encouragement", () => {
    // impossible must not read as anything but the most cautious level the UI has
    expect(REALISM_LEVEL.impossible).toBe("unlikely");
    expect(REALISM_LEVEL.possible).not.toBe("unlikely");
  });
});
