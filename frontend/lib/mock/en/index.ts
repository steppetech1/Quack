import type { MockData } from "../data";
import { canvasChat, canvasReplies, genericReply } from "./canvas-chat";
import { canvases } from "./canvases";
import { domains } from "./domains";
import { radiusSession } from "./session";
import { skills } from "./skills";
import { cheatSheets, topicDetails } from "./topic-details";

/** English mock content. Same ids as ../ru; anything missing here falls back to Russian. */
export const en: Partial<MockData> = {
  domains,
  canvases,
  radiusSession,
  canvasChat,
  canvasReplies,
  genericReply,
  skills,
  topicDetails,
  cheatSheets,
};
