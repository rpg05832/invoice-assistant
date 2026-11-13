# Verification record

This repository was recovered from the previous chat's recorded edits. The old checkout was absent in the attached environment and the GitHub repository was empty. Two large recorded files were truncated: their missing server and browser portions were reimplemented and checked against the recovered acceptance tests. No claim of byte-for-byte recovery is made for those portions.

The local suite includes seven browser acceptance scenarios, real CPU CTC training, private learning isolation, cross-installation/team learning transfer, conflicting-label review and independent hold-out checks. Fast review coverage verifies keyboard approval and advancement, saving navigation, raw OCR/source comparison, original-value highlighting, zoom, held-key suppression, unconfirmed-check/date blocking, supplier questions and Escape saving. The source-launch smoke also passed OCR, personal training, export/import into a second clean installation and training from imported examples. These are functional checks, not a product accuracy percentage. The final CI result is authoritative for each pushed commit.

Committed checkpoint e297df0 passed 82 tests on Linux and Windows in run 43353807083, including Windows executable builds. The expanded Windows executable smoke passed OCR, personal training and explicit transfer between independent installations. Subsequent review-interface changes require their own CI result.

## Real browser coverage

Company creation; mapped XLSX supplier import; PDF upload; source review; check-number leading zeros; human approval; export preview; XLSX download; rejected receipt; editing and a new batch; received receipt; splitting two invoices on one page; dragging a check region outside the cropped invoice; actual OCR of that region; local source enlargement; additional lists; profiles; page joins and unjoins; metrics; supplier questions; writer profiles; real local training; learning bundle download and previewed import into a new company without ledger documents; desktop and tablet screenshots.

## First GitHub run and fixes

Run 43290606413 used the original recovered core. Windows: 35 passed, two backup tests failed with WinError 32 because SQLite context managers do not close handles. Backup connections now use explicit closing, paths are normalized to POSIX separators, and the ZIP streams from disk with cleanup after download. Linux: Playwright 1.58 did not support the newly selected ubuntu-latest (26.04). CI now pins ubuntu-24.04. The subsequent run 43297438919 passed on both operating systems. A later workflow adds standalone Windows executable validation, which must pass separately.

## OCR experiments

Environment: x64 AMD EPYC, 3 allocated CPUs, cgroup memory limit 8 GiB, no NVIDIA device. This does not establish the Windows user's hardware.

- Tesseract Fast Hebrew + English: actual Hebrew image smoke test passed. A 1600×1000 synthetic Hebrew image was read in about 0.34 seconds. This is a startup/compatibility test, not a representative invoice benchmark.
- Indra 1.1.2 / RTL models: actual Hebrew synthetic image read correctly, including a leading-zero numeral sequence; cold run approximately 2.95 seconds. Weights use modified OpenRAIL-M, separately from the Apache code license. Commercial activation requires checking those terms.
- MNIST ONNX: 26,143 bytes, CPU execution. On the standard 10,000 isolated test glyphs, 98.9% exact digit accuracy. Consecutive seven-glyph groups: 92.58% exact. Requiring every glyph confidence ≥0.99 retained 69.61% of groups and reached 99.09% on that retained subset. This excludes segmentation and photographed check annotations and must not be represented as check-number accuracy.
- Qwen3-VL-2B Q4_K_M + Q8 projector: verified downloads and actual CPU execution. About 3 GiB RSS and 51 seconds for a small page. Two prompts returned Hebrew letters in visual/reversed order, despite correct numeral strings. It is not accepted as the default Hebrew text engine. A larger candidate is under evaluation.

## External evidence still needed

A private 13-page scanned invoice sample and two user-confirmed handwritten check values were provided and tested locally; neither the documents nor raw results are published. No Hashavshevet version, successful import specimen, supplier index from the installation, provider keys, or accounting API credentials were provided. Consequently, no target accounting installation or paid cloud endpoint has been certified. Provider and API contract tests use simulated responses. The company-specific verification screen requires a received test batch, target version, evidence, and field-by-field checks, and invalidates evidence when its profile changes.

99% is a target, not a measured product result. The metrics screen measures exact fields and complete documents before editing and after approval, separates engine results, reports coverage, and includes a 95% Wilson lower bound. A curated representative corpus is required before claiming that target.

Detailed local model trials, limitations, licenses and download links: [model research](MODEL_RESEARCH.md).
