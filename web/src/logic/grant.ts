import type { LandWaiting } from "../api/types";

/** The state of a waiting ask's grant, as landqueue.land_queue reads it: a
 * void or lapsed grant reserves no slot, so it must not read as "granted". */
export function grantState(w: Pick<LandWaiting, "granted" | "grant_void" | "grant_lapsed">): string | null {
  if (typeof w.grant_void === "string" && w.grant_void) return "grant void: " + w.grant_void;
  if (w.grant_lapsed === true) return "grant lapsed";
  return w.granted ? "granted" : null;
}
