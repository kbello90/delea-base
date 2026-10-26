export type ArtKind = "ring" | "necklace" | "earring" | "star";

/** A best guess from the name; products have no category in v1. */
export function artKind(name: string): ArtKind {
  if (/\bring\b|anillo/i.test(name)) return "ring";
  if (/necklace|pendant|chain|collar/i.test(name)) return "necklace";
  if (/earring|stud|hoop|arete/i.test(name)) return "earring";
  return "star";
}
