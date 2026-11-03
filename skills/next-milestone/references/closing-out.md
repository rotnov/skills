# Closing out a milestone

Read this when every criterion of the active milestone is ticked with evidence
or explicitly recorded as not met.

## Content complete

1. Re-verify every ticked criterion against the current default branch with
   [the evidence rules](evidence.md); a merge since the last check can break a
   prior result. Untick and resume work on any that no longer hold.
2. Add a dated "met" note to the milestone listing the evidence for each
   criterion, and a closing rationale for any criterion recorded as not met.
3. Ensure the milestone has no open issues: close completed ones, and move
   deliberately deferred ones to the next milestone with a comment explaining
   why. If no next milestone exists yet, define it first
   ([defining a milestone](defining-a-milestone.md)).
4. Close the milestone.
5. If the project keeps a status line elsewhere (a `ROADMAP.md` current-status
   section, a README status blurb), update it through the project's normal
   change process. These files are context, not the source of truth.

## Released

Content complete does not mean released. Find the project's release
procedure: a release document such as `RELEASING.md`, a release script or
workflow in the CI configuration, or tag conventions in the history. Follow it
exactly, including any post-completion verification it requires, and record
the release evidence (tag, release URL, verification run) in the milestone's
notes.

If no release procedure exists, do not invent one or push a tag on your own
judgment. Report the milestone as content complete but unreleased and name
what a release procedure would need to decide (versioning, artifacts,
publication target).

Report both states separately: content complete, with evidence, and released,
with the tag or release URL or the reason it is not released yet.
