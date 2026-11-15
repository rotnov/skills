---
name: apple-hig-design
description: Design and review interfaces or design systems using Apple Human Interface Guidelines. Use for Apple-informed UI design, reusable component libraries, or HIG-based UX audits.
---

# Apple HIG Design

Translate relevant Apple guidance into a platform-appropriate interface and
reusable design rules. Preserve the product's identity and distinguish source
recommendations from choices made for this project.

## Establish the scope

Inspect the supplied screens, design file, code, and existing components first.
Identify the target platform, input methods, available window sizes, and whether
the user wants advice, an audit, a component, or a design system. Infer these
from the project when possible; ask only when an ambiguity changes the result.
Keep a bounded component request bounded. An audit does not authorize edits.

For an existing system, reuse compatible tokens, styles, and component masters.
Do not replace the brand font, navigation model, or platform merely to make the
product look more like an Apple application.

## Ground the decisions

Read [the source map](references/hig-sources.md) when selecting guidance or
checking a numerical claim. Open only the official sections relevant to the
current decision. Record the URL and retrieval date for material recommendations.
Apple's pages may offer a Markdown link when the HTML needs JavaScript; use that
link or another available browser/HTTP reader. No particular connector is required.

If live sources cannot be read, use supplied or bundled guidance provisionally,
identify what was not reverified, and continue work that does not depend on it.
Do not claim a fresh HIG check or invent current numerical requirements.

Keep three kinds of decisions explicit:

- **Apple guidance:** identify the section, platform, and applicable context.
- **Another standard:** name it separately, for example WCAG for a web product.
- **Product choice:** record the chosen value or behavior and its rationale.

Before attributing a number to Apple, check the table heading, platform, unit,
and whether it is a default, minimum, or recommendation. An 8 px grid, 40 px
buttons, 6/8 px corner radii, or Inter are possible product choices, not universal
Apple requirements. Do not treat Apple platform points as interchangeable with
CSS pixels, or use a touch target size as an icon glyph size.

## Apply the relevant principles

**Layout.** Make alignment express relationships; separate unrelated groups with
space or restrained separators. Reveal advanced controls when needed. Preserve
recognizable navigation as the window changes. Test long labels and increased
text size rather than relying only on the default screenshot. Give important
content enough space before adding decoration or new controls.

**Typography.** Define a small hierarchy of shared text styles, including line
height and weight. Retain a suitable existing font; native system typography and
custom fonts have different implementation responsibilities. Avoid thin weights
for small important text. Let containers grow or reflow when text grows instead
of clipping labels to preserve a fixed card height.

**Appearance.** Use semantic roles for surfaces, text, borders, actions, and
status, with explicit light/dark values. Keep component structure and meaning
consistent across modes. Check assets separately; changing the interface theme
does not authorize altering scientific imagery or other fidelity-critical
content. Apple currently favors following the system appearance. Treat an
existing manual theme switch as a product decision to discuss, not something
to silently remove during a review.

**Accessibility.** Pair color with a label, shape, icon, or other cue. Check
foreground/background contrast using actual rendered colors, including alpha
composition, and the standard applicable to this product. Size interactive
targets for the platform and input method. Account for keyboard focus, accessible
names, text enlargement, and alternatives to gesture-only or audio-only actions.
Distinguish checks possible in a design file from checks requiring a running app.

## Make the system reusable

For a design-system deliverable, agree the inventory from the requested screens
and existing product. Use semantic color tokens, a documented spacing/radius
scale, and shared text styles. Label any starter values as project decisions;
the source map includes an optional desktop example, not a default to impose.

Use linked component instances and meaningful properties for labels, icons,
states, and supported layouts. Define only states relevant to the component:
default, hover, pressed, focus, disabled, selected, loading, or error as needed.
Record unsupported states rather than presenting a static specimen as complete.

When converting existing screens, preserve valid master references, frame
identities, comments, prototype destinations, imagery, and content overrides.
Check the target tool's capabilities before promising preservation. Keep native
editable layers; a flattened screenshot is reference evidence, not a component.
Library publication or code integration requires its own task scope.

For rounded controls, inspect both the outline and the filled corners. A correct
radius can still show square child fills. Clip the background/content where
appropriate while keeping focus indicators and intentional popovers visible.
Do not compensate for a clipping defect by changing the entire radius scale.

## Verify and hand off

Inspect rendered output in each supported appearance and at representative small
and large sizes. Check spacing, baseline alignment, wrapping, clipping, icon
legibility, and component state coverage. After a refactor, compare with the
original and verify instance links and navigation separately from appearance.
Use the available editor or application tooling; without it, deliver an annotated
review or specification and explicitly state which edits or checks were not run.

For implemented interfaces, check keyboard use, focus visibility, accessible
names, and text resizing in the running app. Screenshot inspection alone does
not prove those behaviors or establish accessibility conformance.

Report the changed or reviewed artifacts, important findings, verification
performed, and remaining gaps. Cite the relevant Apple sections close to their
recommendations and identify deliberate product deviations. Say which guidance
was reviewed rather than declaring the whole product "Apple compliant."
