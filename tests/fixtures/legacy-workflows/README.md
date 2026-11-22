# Legacy workflow fixtures

These files preserve the original workflow configuration bytes under an inert
`.fixture` extension. Regression tests inspect their authorization, failure and
source-pinning rules. Four test files are adapted only to locate them here.

They are outside `.github/workflows`, so GitHub does not execute them. No active
Actions workflow or repository credential is included in this snapshot. Names
such as `OPENAI_API_KEY` refer to secret bindings, not stored secret values.
Historical approval labels are not permission to initiate experiments.

The launch configurations target original scientific commit identities; do not
copy them into active workflows or substitute the new publication commit without
a separate operational review.
