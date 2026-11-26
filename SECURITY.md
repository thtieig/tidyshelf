# Security policy

tidyshelf moves and can delete files, so a bug in its guardrails is a security problem.

## Reporting

Please report security problems privately through GitHub's
**"Report a vulnerability"** button (Security → Advisories) on this repository.
Do not open a public issue for them.

Useful to include: the tidyshelf version, the plan file (with names anonymised),
the config sections `[roots]`, `[safety]`, `[exclude]`, and what happened versus what you expected.

## Scope

In scope: anything that lets `tidyshelf-apply` touch a path outside its configured roots,
overwrite a file, delete from a library root, follow a symlink, or run a plan that differs
from the one that was approved.

Out of scope: damage caused by running with `--execute` on a plan you did not read.
Dry-run is the default for a reason; keep backups.
