# Author-run validation artifacts

`raw-validation-logs.zip` preserves original stdout, JUnit XML, environment,
summary and their original SHA256SUMS byte-for-byte, including whitespace in
pytest failure traces. These are archived rather than whitespace-normalized.
The outer SHA256SUMS covers this ZIP and the readable environment/summary copies.

Read `summary.json` for all four runs, including the original failures; do not
report a single clean 551-case run. The successful 163-case run uses identical
committed runtime/test source exported outside OneDrive. See the task design
for scope and remaining SDK/binder/VM gates. No independent signoff is implied.
