/**
 * What the tool does not claim.
 *
 * This sits between the evidence and the decision control, and it is repeated
 * verbatim in every exported report (`backend/reporting.py:CALIBRATION_NOTICE`).
 * Keep the two in step: efficacy here is unmeasured against known-forged
 * documents, and a reviewer who over-trusts a flag and rejects a genuine
 * certificate is the worst outcome this product can produce.
 */
export const CALIBRATION_NOTICE = [
  "Parakh reports signals, not conclusions. A flagged check is a reason to look at the document, not evidence that it was forged.",
  "Detector accuracy has not been measured against known-forged documents. The true-positive rate of this system is unknown.",
  "Several checks report raw signal rather than a tuned threshold, so their flags carry no calibrated probability.",
  "A clear result is not a certificate of authenticity. It means no check in the selected set produced a signal.",
  "The recorded human decision is the verdict of record. The machine result is input to that decision, never a replacement for it.",
] as const;
