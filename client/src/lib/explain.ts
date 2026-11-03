const REASONS: Record<string, string> = {
  PROXIMITY_ONLY: "A person came close, but closeness is not evidence of contact.",
  TARGET_STATIONARY: "The object stayed where it was while the person was near.",
  TARGET_UNCHANGED: "Frames after the approach show the object unchanged.",
  MODEL_SUPPORT_OVERRULED: "A model check suggested a pickup, but the tracked geometry shows no movement, so it was not counted.",
  TARGET_MOVED: "The object's tracked position changed beyond the movement threshold.",
  CLEAR_LIFT: "The assessment saw a lift in the original frames.",
  CLEAR_RELEASE: "The assessment saw a release in the original frames.",
  STABLE_AFTER: "The object is steady in the frames after this moment.",
  POSSIBLE_OCCLUSION: "The object was hidden when the change may have happened.",
  TARGET_LOST: "The object was not detected again after it was hidden.",
  MOVED_WITH_ACTOR: "After it was hidden, the object was next seen moving with this person and no one else. The lift itself was not visible.",
  NO_VISIBLE_TRANSITION: "The footage does not show the object being lifted or released.",
  INCOMPLETE_COVERAGE: "Part of this window was not analysed, so it is unknown rather than empty.",
  COMPETING_ACTORS: "More than one person was equally close, so no single person can be named.",
  ACTOR_NOT_ESTABLISHED: "The footage does not establish an actor.",
  TRACK_FRAGMENTED: "A track was interrupted, so identity across the gap is not assumed.",
  SMALL_TARGET: "The object is small in the frame, which limits what can be seen.",
  ZONE_OVERLAP_ONLY: "The object overlaps the zone in the image, but placement inside it was not confirmed.",
  ASSESSMENT_UNAVAILABLE: "The visual assessment did not complete, so the event cannot be supported.",
  BOUNDARY_WINDOW: "The evidence window is cut short by the start or end of the clip.",
  PARTIAL_SCOPE: "Analysis coverage was incomplete, so this refers to the analysed part only.",
  REVISED_BY_INSPECTION: "This revision followed an extra visual inspection.",
};

const CLAIM_REJECTIONS: Record<string, string> = {
  CLAIM_EVENT_MISMATCH: "The claim did not match the stored event exactly.",
  EVENT_NOT_FOUND: "The claim cited an event that does not exist.",
  REJECTED_EVENT: "The claim tried to state a rejected candidate as fact.",
  CERTAINTY_EXCEEDS_EVIDENCE: "The claim was more certain than the evidence allows.",
  STALE_EVENT_REVISION: "The claim cited an outdated revision of the event.",
  EVIDENCE_DIGEST_MISMATCH: "The evidence bytes no longer match their recorded digest.",
  REVIEWER_REJECTED: "The secondary reviewer rejected this claim.",
  REVIEWER_NEEDS_EVIDENCE: "The secondary reviewer asked for more evidence, so the claim was withheld.",
  DRAFT_SCHEMA_INVALID: "The submitted claims were not in the required structure.",
};

export function explainReason(code: string): string | null {
  return REASONS[code] ?? null;
}

export function explainRejection(code: string): string {
  const base = code.split(":")[0];
  if (base === "UNSUPPORTED_ABSTENTION_REASON") return "An uncertainty message that this question does not justify was removed.";
  return CLAIM_REJECTIONS[base] ?? "The claim failed validation.";
}

export function whyNotSupported(codes: string[]): string[] {
  return codes.map(explainReason).filter((text): text is string => Boolean(text));
}
