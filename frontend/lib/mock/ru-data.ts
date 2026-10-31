import type { MockData } from "./data";
import { canvasChat, canvasReplies, genericReply } from "./ru/canvas-chat";
import { canvases } from "./ru/canvases";
import { domains } from "./ru/domains";
import { radiusSession } from "./ru/session";
import { skills } from "./ru/skills";
import { cheatSheets, topicDetails } from "./ru/topic-details";

export const ru: MockData = {
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
