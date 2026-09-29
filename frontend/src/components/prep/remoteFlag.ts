// Where «Подготовка» takes its data from. One definition, so a module cannot disagree with another
// about whether the server owns the section — they used to declare this three times.
//
//   local  — the demo plan and the demo skills in prepData.ts, everything recomputed in the browser;
//   remote — the plan, the skills and the knowledge model come from the backend, and the browser
//            keeps only what no endpoint speaks for (see prepStore.ts).
//
// The domain flag wins over the common one, so «Подготовка» can be tried against a live backend while
// the rest of the app still runs on demo data.

export const REMOTE_PREP =
  typeof process !== "undefined" &&
  (process.env.NEXT_PUBLIC_SRC_PREP === "remote" ||
    (process.env.NEXT_PUBLIC_SRC_PREP !== "local" &&
      process.env.NEXT_PUBLIC_DATA_SOURCE === "remote"));
