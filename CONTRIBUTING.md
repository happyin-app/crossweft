# Contributing

- Python 3.9+, standard library only. No runtime dependencies, please.
- Run before a pull request:

  ```bash
  python -m unittest discover -s tests
  python -m crossweft self-test
  python -m crossweft check
  python -m crossweft --root examples/polyglot-shop check
  ```

- Every new check needs a planted failing case in the engine self-test or the
  demo tests. A check that cannot be shown to fail is not accepted.
- Every failure message should say what to do next.
- This repository guards its own seams (`seams/model/`): if you add a CLI
  command, a hook event or a version bump, `crossweft check` tells you what
  else to update.

## License of contributions

crossweft is licensed under Apache-2.0, and so are contributions: anything you
submit for inclusion is under the same license (Apache-2.0, section 5). No
separate agreement to sign.
