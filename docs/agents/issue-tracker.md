# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues in `DhuolK/POLICY-GIUARD`. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --repo DhuolK/POLICY-GIUARD --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --repo DhuolK/POLICY-GIUARD --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --repo DhuolK/POLICY-GIUARD --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --repo DhuolK/POLICY-GIUARD --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --repo DhuolK/POLICY-GIUARD --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --repo DhuolK/POLICY-GIUARD --comment "..."`

## Pull requests as a triage surface

**PRs as a request surface: no.**

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --repo DhuolK/POLICY-GIUARD --comments`.

## Wayfinding operations

Used by `/wayfinder`. The map is a single issue with child issues as tickets. Use the repository above for all `gh` operations.
