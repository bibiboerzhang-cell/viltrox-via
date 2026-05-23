# ADR: Estimate New Launch Acceptance With Rules Before Models

## Context

P6.71 defined fire metrics, P6.72 standardized time-series anchors, and P6.73
added rule-based trend detection. The next step is a practical launch shortlist
that combines product fit, current market signals, and risk without claiming a
trained forecast.

## Decision

P6.74 implements a read-only rule estimator:

- KOL fit comes from the existing Product Campaign Card.
- Platform momentum comes from P6.73 trend signals.
- Market risk comes from existing competitor signal evidence.
- Candidate scores stay explainable and evidence-backed.
- The estimator outputs Top N candidates only; it does not create a campaign or
  recommendation record.

## Rationale

This gives campaign planning a usable shortlist while preserving the data trust
rules. It also creates a baseline that can be calibrated in P6.75 by comparing
the estimate against observed next-day truth.

## Consequences

- Recommendation layers must not consume the score as an automatic decision.
- P6.75 must record prediction-versus-truth before any model calibration.
- LLM/Gemini remains out of scope for this layer.
