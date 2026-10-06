# Security

Report vulnerabilities privately through GitHub's "Report a vulnerability"
(Security Advisories) on this repository, not in a public issue.

crossweft reads files named by the model and prints values it extracts. It
refuses model and config paths that are absolute, contain `..`, or resolve
(through symlinks) outside the repository, so a model in a pull request cannot
make CI print files from elsewhere on the runner. It never executes code from
the repository it checks, except validators that `crossweft validators` runs
on purpose.
