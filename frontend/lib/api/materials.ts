import { get, upload, type S } from "./client";

export type Material = S["MaterialView"];

/** The backend reads a material in the background (`material.parse`): `uploaded` until it is done, then `parsed` */
export const MATERIAL_PARSED = "parsed";

/** A PDF or a photo of notes into the subject (Т-§4.3); the graph of chapters and topics is built from it */
export function uploadMaterial(domainId: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return upload<Material>(`/v1/domains/${domainId}/materials`, form);
}

export const listMaterials = (domainId: string) => get<S["MaterialList"]>(`/v1/domains/${domainId}/materials`);
