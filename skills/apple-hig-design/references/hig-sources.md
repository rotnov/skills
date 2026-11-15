# Source map and product decisions

These links are navigation aids, not a frozen copy of the guidelines. The core
sources were checked on 2026-11-15. Refresh the relevant section when using it
to justify a current recommendation, especially a platform-specific number.

## Core Apple sources

| Source | Read when deciding | Focus |
| --- | --- | --- |
| [Layout](https://developer.apple.com/design/human-interface-guidelines/layout) | Composition, spacing relationships, resizing, or content hierarchy | Alignment, grouping, progressive disclosure, available space, and text-size adaptation. |
| [Typography](https://developer.apple.com/design/human-interface-guidelines/typography) | Type styles, font selection, or text enlargement | Legibility, hierarchy, line spacing, system/custom fonts, and Dynamic Type where supported. |
| [Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility) | Contrast, input targets, state cues, or assistive interaction | Platform-specific target guidance, redundant cues, keyboard access, accessible names, and text enlargement. |
| [Dark Mode](https://developer.apple.com/design/human-interface-guidelines/dark-mode) | Appearance modes, theme tokens, or mode-dependent assets | System appearance, adaptive semantic colors, contrast, and asset legibility in both modes. |

## Component-specific sources

Open these only when the component or interaction is in scope:

- [Buttons](https://developer.apple.com/design/human-interface-guidelines/buttons):
  action presentation and platform-specific button behavior.
- [Toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars):
  frequently used contextual actions and their organization.
- [Sidebars](https://developer.apple.com/design/human-interface-guidelines/sidebars):
  sidebar navigation and its relationship to content. A task inspector is not
  automatically a navigation sidebar; choose guidance for the actual role.
- [Color](https://developer.apple.com/design/human-interface-guidelines/color):
  semantic/system colors, meaning, and custom color choices.
- [SF Symbols](https://developer.apple.com/design/human-interface-guidelines/sf-symbols):
  symbols in an Apple-platform interface. Check current usage terms before
  proposing Apple-provided assets for another platform.

Use the [HIG index](https://developer.apple.com/design/human-interface-guidelines/)
to find other relevant sections. Avoid loading an entire guideline catalog for
a small review.

## Web accessibility is a separate source contract

For a web product, use the applicable WCAG version and criteria directly rather
than inferring web conformance from HIG or a Figma screenshot. Useful starting
points are the [WCAG 2.2 standard](https://www.w3.org/TR/WCAG22/) and these
informative explanations of its criteria:

- [Contrast (Minimum), 1.4.3](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html):
  generally 4.5:1 for text and 3:1 for large text, with defined exceptions.
  Read the large-text definition; bold text is not automatically large text.
- [Non-text Contrast, 1.4.11](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html):
  generally 3:1 against adjacent colors for required control/state cues and
  meaningful graphics, subject to the criterion's applicability and exceptions.

Neither rule means that every decorative separator must meet 3:1. Do not round
a failing contrast result upward. Apple platform points and CSS units
need their own platform context; consult the standard before converting them.

## Optional dense desktop starting point

Use this example only if the project needs a starting scale and its content and
input density justify it. It is not an Apple preset or a claim of accessibility.

| Role | Example project choice |
| --- | --- |
| Spacing scale | 4, 8, 12, 16, 20, 24 CSS px |
| Main button height | 40 CSS px, with target size verified for the intended input |
| Control / panel radius | 6 / 8 CSS px |
| Type styles, size / line height | 22/28, 17/22, 14/20, 12/16 CSS px |
| Font | A legible existing brand font; Inter is one possible web choice |
| Main icon slot | 20 CSS px inside a larger interactive target |

Adapt these values after checking real labels, localization, text resizing, and
the smallest supported window. Prefer a platform's appropriate native defaults
when building native controls.

## Record the basis of a decision

For decisions that affect the deliverable, a compact record is enough:

| Decision | Basis | Application | Evidence |
| --- | --- | --- | --- |
| Group related actions | Apple Layout recommendation | Use shared alignment and spacing tokens | Relevant source URL and retrieval date |
| Follow system appearance | Apple Dark Mode recommendation | Document any intentional override | Source URL plus product rationale |
| 40 px main button | Product choice | Validate against actual input and content | Rendered example and sizing check |
| Text contrast target | Applicable WCAG criterion | Identify the actual foreground/background pair | Source, measured ratio, and method |

Keep implementation status separate: a named token is not proof of a binding,
a component specimen is not proof of an interaction, and a passing contrast
pair is not proof that the whole interface is accessible.
