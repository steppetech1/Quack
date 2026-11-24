import type { Canvas, CheatSheet, ChatMsg, ChatPart, Domain, SessionData, Skill, TopicDetails, WorkspaceMock } from "./types";

/** Everything the app needs as locale-dependent mock content. One module per locale provides it. */
export type MockData = {
  domains: Domain[];
  canvases: Canvas[];
  radiusSession: SessionData;
  /** Teacher chat thread per canvas id */
  canvasChat: Record<string, ChatMsg[]>;
  /** Stub teacher replies per canvas id */
  canvasReplies: Record<string, ChatPart[][]>;
  genericReply: ChatPart[];
  /** Seed skills (user skills live in localStorage) */
  skills: Skill[];
  /** Hand-written topic pages by topic id; others get derived moments */
  topicDetails: Record<string, TopicDetails>;
  /** Cheat sheets by hub id */
  cheatSheets: Record<string, CheatSheet>;
  /** What the tabs of the workspace pane show in mock mode */
  workspace: WorkspaceMock;
};
