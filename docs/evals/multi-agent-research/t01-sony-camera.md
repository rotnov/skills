# T01: Sony Spatial Reality Display camera access

## Mission and allowance

- Started: 2026-09-16; user authorized the applied skill test.
- Goal: determine whether an application can use the built-in camera for gesture
  capture, separating raw frames, tracking data, and alternative gesture input.
- Acceptance: primary-source evidence for documented interfaces and limitations;
  device-specific claims remain conditional until model/OS/SDK are known.
- Sources/actions: public documentation and read-only source inspection; local
  research records. No hardware access is established. No installation, device
  changes, external messages, publication, or additional purchases authorized.
- Resource authority: existing subscription only. Two rounds; three initial
  island assignments, at most two targeted follow-ups and one verification
  assignment. Root retains synthesis/reporting work. No provisioned services.
- Reserves: first three assignments explore; two follow-ups resolve conflicts or
  alternatives; final assignment checks the answer. Stop within this envelope.
- Steering authority: user; root may refine research tasks within this scope.
- Output: Russian answer with sources and this local, reviewable test record.
- Snapshot: public pages retrieved during this run; page versions/dates recorded
  where available. This is not a frozen web corpus or a controlled comparison.

## Execution and formulations

- Mode: isolated-parallel initial investigations through native collaboration.
- Context separation: workers start with `fork_turns: none`; shared filesystem
  and tools mean procedural separation, not a security boundary.
- Initial exposure: common mission and own charter only, no sibling findings.
- Model: inherited host model; immutable version and per-worker usage unknown.
- Runtime capabilities: native spawn/follow-up and public web reads available;
  no hard token/currency control or device execution established.

| Problem | Relation to goal | Island and method | Outstanding obligation |
| --- | --- | --- | --- |
| P1: supported built-in-camera API | Direct | A: official SDK/API contract | Distinguish raw frames from tracking outputs |
| P2: OS-level camera capture | Direct, if accessible | B: device/driver and capture evidence | Verify actual device exposure and concurrent tracking |
| P3: gestures through another sensor | Useful alternative only | C: documented gesture integrations | Does not satisfy reuse of the built-in camera |

Initial charters A1/B1/C1 each receive one bounded investigation and a checkpoint.
C1 has a protected initial pass even if the built-in-camera route looks promising.
No imported findings are initially available. Each island must return evidence,
limitations, and a discriminating next check; absence of an API in searched pages
is not proof of impossibility.

## First checkpoint: frozen findings

All initial findings below are revision 1. Complete cited public pages were read
unless a limitation says otherwise. Source inspection is not a device experiment.
The execution epoch is this native collaboration run, with immutable model
versions and token usage unavailable.

| Finding / origin | Scoped result and evidence status | Evidence and remaining check |
| --- | --- | --- |
| A-F1 / A1 / P1 | Supported: Unity exposes head position through `WatcherAnchor`, not sensor pixels | [Sony FacePose][facepose], updated 2026-08-21; exact installed plugin remains unknown |
| A-F3 / A1 / P1 | Supported: Unreal's image callback supplies a rendered display composite | [Sony display image][render]; does not establish physical camera capture |
| A-F4 / A1 / P1 | Supported: Sony Settings shows captured sensor imagery for SR1/SR2 | [Sony Settings][settings]; utility preview is not an application API contract |
| A-F5 / A1 / P1 | Raw-frame SDK contract unresolved in inspected web documentation | [Downloads][downloads] lists plugins 2.6.0 and Native API 2.5.0; Native API download reached an EULA, headers not inspected |
| B-F1 / B1 / P2 | Supported as vendor claim: AirTouch can reuse the built-in SRD camera with eye tracking through Windows sharing | [Neural Lab camera guide][camera], 2026-07-08; exact model/build/driver and reproduction missing |
| B-F2 / B1 / P2 | Supported as vendor claim: setup guide names both SR1 and SR2 and offers built-in camera input | [Neural Lab setup][setup], 2026-07-08; no separate per-model measurements |
| B-F3 / B1 / P2 | Supported: Sony documents runtime/session contention | [Troubleshooting][troubleshooting]; this does not prove OS capture impossibility |
| C-F2 / C1 / P3 | Supported: Sony's older AirTouch guide describes PC/external webcam gesture input | [Sony AirTouch][airtouch], updated 2026-03-03; establishes an alternative, not mandatory external hardware for every version |
| C-F1 / C1 / P3 | Supported: Sony suggests Leap Motion for gesture interaction | [Sony FAQ][faq]; separate input hardware does not fulfill built-in-camera reuse |

Root independently opened the vendor camera/setup pages, Sony FacePose, Settings,
AirTouch, FAQ, download and release-note pages. These repeated reads share the
same source roots; they are not additional implementations or replications.
B-F4's indexed Sony support excerpts about device identity are not used as
decisive evidence because full page reads timed out.

## Migration and second-round assignments

R-F1/r1 is a root-checked relay of B-F1/r1, preserving Neural Lab as its single
implementation-evidence root. R-F2/r1 is [Microsoft camera settings][ms-camera]:
some proprietary and DirectShow cameras do not appear in Windows Settings.

| Issued brief | Imports and disposition requested | Changed task and acceptance |
| --- | --- | --- |
| A2, replaces A1 | R-F1/r1 and R-F2/r1; test/adapt | Verify Windows multi-app mechanism and limits; separate OS support from Sony device compatibility |
| C2, replaces C1 | R-F1/r1 and C-F2/r1; test/adapt | Reconcile old external-camera guidance with newer built-in-camera claim; check missing model details and virtual-camera source-selection assumptions |
| V1, checking reserve | Candidate synthesis and B1 findings | Check source correctness and correspondence to raw-frame/gesture goal separately; reject universal compatibility and frame-rate assertions |

Allocation decision: root spent both reserved follow-ups on the positive
counterexample and its prerequisites instead of repeating broad searches.
Each follow-up allowed at most two searches plus decisive reads. Alternative C1
completed its protected first pass before receiving the counterexample.

Attempted fresh verifier creation failed with `agent thread limit reached`.
Root issued V1 to the existing B executor with its B1 exposure explicitly
preserved. Verification therefore uses an exposed context; it is not an
independent fresh-context review. No additional worker capacity was provisioned.

## Results

Both issued follow-ups and V1 executed and returned results. Configuration
clarification remains unanswered; no specific user device is assumed.

- A2 adopted R-F1 as a vendor claim and tested its OS prerequisite. A-F7/r2
  verifies Microsoft's [multi-app camera announcement][ms-sharing], dated
  2024-12-13 for Insider build 26120.2702; it does not guarantee availability on
  every Windows installation. A-F8/r2 identifies [MediaFrameReader][ms-frames]
  with `SharedReadOnly` as a documented frame-acquisition mechanism for supported
  sources, not proof that Sony's driver exposes one. The API flag and UI toggle
  are not established as interchangeable. A-F9/r2 retains Settings exclusions.
- C2 adapted C-F2 to revision 2 after inspecting R-F1. C-F6/r2 corrects an overly
  broad missing-model statement: the companion guide names SR1 and SR2, but
  supplies no per-model test configuration. C-F7/r2 identifies an unverified
  dependency in the AlterCam fallback: [AlterCam requires each consuming app to
  select its virtual camera][altercam], while the inspected Sony Settings guide
  does not document arbitrary tracking-camera selection. C-F8/r2 treats the
  approximately 10 fps statement as an unverified vendor claim about a feed,
  not a universal sensor or Sony tracking-frequency limit.
- V1 re-opened the two Neural Lab guides and Sony AirTouch guide. It accepted
  source attribution, rejected blanket SR1/SR2 and raw-camera API guarantees,
  and corrected the reconciliation: Sony's older guide requires a webcam in
  its described setup; the newer vendor guide differs. Dates alone do not prove
  technical supersession. Root adopted that correction in the final conclusion.

### Decision and verification gates

**Candidate revision 2:** Neural Lab documents gesture control using the SRD's
built-in camera, including a camera-sharing workflow. This provides a concrete
candidate to test, not a reproduced result or a Sony-supported raw-frame API
contract. The inspected Sony documentation establishes tracking coordinates and
rendered-image access separately. It does not resolve custom sensor-frame access.

- Artifact gate: passed for the bounded documentation claims and source dates;
  no hardware replication, binary/header audit, or performance measurement.
- Goal-correspondence gate: partial. Existing-software gesture control has a
  vendor-documented candidate. Custom raw-frame acquisition and simultaneous
  Sony tracking remain unverified for the user's configuration. External webcam
  and Leap Motion routes satisfy gesture interaction only, not built-in reuse.
- Evidence roots: Sony documentation, Neural Lab implementation claims, and
  Microsoft OS contracts. AirTouch's two articles share one implementation root;
  Sony's AirTouch article describes that same product. Microsoft corroborates
  the general OS mechanism only. Agent agreement adds no device evidence.
- Unresolved: model/firmware/Windows build/driver/SDK, exposed capture API and
  formats, simultaneous tracking, hand visibility, latency and frame rate.
- Next discriminating experiment, not executed: record that configuration,
  enumerate camera sources through relevant APIs, capture frames with Sony
  applications closed, then repeat with functioning SRD tracking and measure
  frame timestamps and tracking continuity. Settings absence alone is insufficient.

### Observed skill behavior

Three fresh-history initial workers completed A1/B1/C1; two targeted revised
briefs A2/C2 completed; the reused B executor completed verification V1. One
additional fresh-verifier spawn failed before execution. Initial investigations
were procedurally separated; second-round imports were explicit. Total actual
worker assignments: six across three worker contexts, plus root coordination.
Initial workers each reported approximately four search queries/batches; A2 and
C2 each reported two targeted searches. These reports are not precise token or
cost metering. Token usage, monetary usage, and immutable model versions are
unknown. No paid service, license, or additional model capacity was enabled.

The test exercised a useful counterexample migration: the OS/device island's
positive vendor claim changed the alternatives island's brief and triggered
specific follow-up checks. The follow-ups actually ran; the result was not merely
a combined summary. A host limit forced exposed-context verification, recorded
above. The real-world test is complete as a conditional documentation assessment;
device feasibility remains open. This is neither a controlled efficacy comparison
nor completion of the E01–E30 behavioral evaluation suite. No skill instructions
were changed during this test.

[facepose]: https://xyn.sony.net/en/developer/setup/spatial-reality-display/unity/howtogetfacepose
[render]: https://xyn.sony.net/en/developer/setup/spatial-reality-display/unrealengine/get-the-image-displayed-on-the-spatial-reality-diaplay
[settings]: https://xyn.sony.net/en/developer/setup/spatial-reality-display/spatial-reality-display-settings
[downloads]: https://xyn.sony.net/en/developer/setup/spatial-reality-display/download-info
[camera]: https://neural-lab.com/blog/sony-srd-camera-hand-tracking
[setup]: https://neural-lab.com/blog/how-to-set-up-hand-tracking-sony-srd
[troubleshooting]: https://xyn.sony.net/en/developer/setup/spatial-reality-display/troubleshooting
[airtouch]: https://xyn.sony.net/en/learn/spatial-reality-display/air-touch
[faq]: https://xyn.sony.net/en/developer/spatial-reality-display
[ms-camera]: https://support.microsoft.com/en-us/windows/hardware/camera/manage-cameras-with-camera-settings-in-windows-11
[ms-sharing]: https://blogs.windows.com/windows-insider/2024/12/13/announcing-windows-11-insider-preview-build-26120-2702-dev-channel/
[ms-frames]: https://learn.microsoft.com/en-us/windows/apps/develop/camera/process-media-frames-with-mediaframereader
[altercam]: https://altercam.com/articles/split-webcam.html
