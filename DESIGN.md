# Campus Compass interface

The interface is designed as a student service desk: a campus masthead, direct navigation, a labelled question form, common-question rows, and a document reference shelf.

## GitHub references consulted

- [Indiana University Rivet](https://github.com/indiana-university/rivet-source). Reviewed the [starter layout](https://github.com/indiana-university/rivet-source/blob/2.0.0-master/src/index.html) for its masthead/title hierarchy, skip link, contained main content, and practical service footer.
- [GOV.UK Frontend](https://github.com/alphagov/govuk-frontend). Reviewed [service navigation](https://github.com/alphagov/govuk-frontend/blob/main/packages/govuk-frontend/src/govuk/components/service-navigation/template.njk) for named navigation and current-item indication, and the [textarea template](https://github.com/alphagov/govuk-frontend/blob/main/packages/govuk-frontend/src/govuk/components/textarea/template.njk) for explicit labels and associated hints.
- [University of Maryland design system](https://github.com/UMD-Digital/design-system). Consulted the project overview for separation of tokens, layout, components, and accessibility utilities.

These projects informed the approach. The React markup, CSS, monogram, and application styling here are original; university trademarks, logos, and branded assets were not imported. No additional design-system dependency was added.

## Local design decisions

- Warm paper background, burgundy accent, Georgia headings, and system UI body text. No external font request is needed.
- Horizontal text navigation; current section is indicated by both font weight and an underline.
- Main task first: the question form appears before common questions.
- Reference shelf displays live document titles, departments, and sample labels rather than invented campus notices or statistics.
- At narrow widths, the resource rail moves below the question form, then stacks into a single column.
- Keyboard skip link, visible focus rings, labelled fields, reduced-motion support, and focus handling in source dialogs.
- Keep demonstration material explicitly labelled; college identity and verified contact details can be configured in a later content pass.

Visual regression baseline from before this redesign is saved locally in `work/design-before/`.
