# Multi-Agent Research: Applied Test Queue

These are user-selected research tasks, separate from the synthetic E01–E30
conformance suite. Recording a task does not mean its investigation has run.

## T01: Sony Spatial Reality Display camera access for gesture capture

- Priority: first applied test, selected by the user on 2026-09-16.
- Original request: "sony spatial reality display access to camera for gestures capture".
- Research question: Can an application access a Sony Spatial Reality Display's
  camera for gesture capture?
- Status: documentation investigation completed on 2026-09-16; hardware
  feasibility remains provisional. See [T01 run record](t01-sony-camera.md).
- Configuration requested: identify the display model, host operating system, SDK/API
  versions, and whether the user needs raw camera frames or a supported gesture
  interface. Record the available documentation and hardware access.
- Acceptance used: an evidence-backed answer about documented access and
  limitations for the identified configuration; distinguish documented API
  support from an experiment actually performed. If direct access is unavailable,
  identify documented alternatives and unresolved questions.
- Outcome: Neural Lab documents built-in-camera gesture capture, but the user's
  model/OS/driver remain unknown and no device experiment was performed. Custom
  raw-frame API access and coexistence with Sony tracking are unresolved.
- Resource boundary: existing subscription only; no additional spending.

The E01–E30 suite remains a separate test of protocol behavior. T01 is not a
replacement for those cases or evidence that the protocol improves research.
