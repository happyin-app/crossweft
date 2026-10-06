# Methodology: contract-checked seams

The failure mode is always the same shape: **one truth lives in several
places; a change touches one place; nothing fails until integration or a
customer's machine.** Types, tests and linters stop at a language boundary.
The seams between a C++ client and a Go server, between a service and its
installer, between code and its `.env.example`, are held together by hand.

## Rules

1. **Every cross-component dependency is a declared link.** Blocks and links
   live in one model, next to the code, reviewed like code.
2. **Every link says how its sides agree** (`contract.enforcement`). A single
   owner -- shared code, a generator, shared test vectors -- is named in
   `defined_in`. Anything written by hand on both sides is a hand-written seam.
3. **Every hand-written seam has a guard that reads both sides**: a join (a
   value), a set (members) or a pair (fingerprinted regions). A guard that
   reads only one side does not count.
4. **Claims about the code are anchored** to literals in files. When the code
   moves, the map fails and is corrected -- it never drifts silently.
5. **Known drift is recorded, not ignored**: a finding carries the problem key,
   an owner and a next step. When the problem disappears, the check demands
   the finding be closed.
6. **Identity values have one pinned source.** A service name, a pipe name, a
   signer key, an origin -- compiled in once and derived everywhere else, never
   re-obtained from the environment where it may not exist yet. Where a second
   language cannot import the constant, a join guards the copy.
7. **Checks fail loud.** Zero inputs is an error, unknown config is an error,
   a path leaving the repository is an error, an allow entry that is no longer
   needed is an error. A green result must mean the check ran.

## In the development loop

- `crossweft impact` before committing: which seams the change touches and the
  other side of each.
- `crossweft check` in CI and in the agent's stop hook.
- `crossweft discover` when onboarding a repository: it lists values already
  typed into two languages, with ready-to-paste guards.

The cost is the model: each new seam is a small model edit and a guard. The
payoff is that a one-sided change fails at `check`, in the author's session,
instead of at install.
