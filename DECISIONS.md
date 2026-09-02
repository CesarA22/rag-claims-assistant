# Technical decisions

Tradeoffs, what was cut, and why. Appended as sessions land.

## S1 — `model` and `provider` in success meta, never in errors

T-10 forbids the model name in error payloads. Every successful answer
envelope still carries `meta.model` and `meta.provider`.

That is deliberate. An error payload is the artefact that travels — it gets
forwarded, pasted into a ticket and screenshotted — and provider error strings
are the place where prompt fragments and version detail leak. The ban is on
that uncontrolled channel.

The success envelope is telemetry for an authenticated internal tool. An
analyst disputing an answer needs to know which model and which provider
produced it. The identifier itself is not the leak; putting it on a
problem+json that leaves the building is.
