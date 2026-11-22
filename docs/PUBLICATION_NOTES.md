# Publication boundaries and contributor setup

This candidate is prepared for owner approval before creation of a public
repository. A repository name, software license and publication authorization
remain owner decisions. The proposed publication is independent of the original
research repository and imports no Git history.

## Retained source and omitted records

Source modules, analysis tools and synthetic test data are retained. Historical
workflow files are renamed as `.yml.fixture` under tests, where GitHub will not
execute them. Existing approval strings are workflow guard labels, not API keys
or authorization from the researcher. No paid workflow or active Actions
configuration is included.

Raw run data, personal Git metadata, Actions logs/artifacts and private operational
handoffs are excluded. The broader historical source audit found no confirmed
credential, but publication contents receive a new scan rather than relying on
that finding. Scientific rule/model-interface source has not been altered to
remove substantive experimental information.

## Future commits

Use the account email settings at https://github.com/settings/emails to enable
email privacy and blocking of command-line pushes that expose the account email.
Use the GitHub-provided no-reply address as the effective Git author/committer
email. Check repository-local overrides as well as global configuration:

```bash
git config --show-origin --get user.email
```

Automated/API-created commits must explicitly use the intended no-reply identity
when they bypass local Git settings. Initial publication should use that identity
too. No personal email value is embedded in these instructions.

Enable Secret Protection and Push protection for the new repository after
creation. These controls do not substitute for email privacy or review of model
records. Contributors should keep generated runs and credentials outside source
control and avoid publishing private journals in issues or workflow artifacts.

## Limits of the publication

Checksums identify bytes; they do not authenticate scientific claims. Excluding
history and raw data limits chronological and empirical reanalysis. Original
scientific references remain available in the retained research record. No
historical paid result is relabeled as newly reproduced on the public snapshot.
