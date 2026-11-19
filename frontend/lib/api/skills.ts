import { get, type S } from "./client";

export type SystemTemplate = S["SystemTemplate"];

/** The prompt files of the system with their versions (read only): the base a learner's skill sits on top of */
export const getSystemTemplates = () => get<S["SystemTemplatesResponse"]>("/v1/system-templates").then((r) => r.templates);
