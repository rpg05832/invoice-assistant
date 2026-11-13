# Verification record

This repository was recovered from the previous chat's recorded edits. The old checkout was absent in the attached environment and the GitHub repository was empty. Two large recorded files were truncated: their missing server and browser portions were reimplemented and checked against the recovered acceptance tests. No claim of byte-for-byte recovery is made for those portions.

The local suite passed 105 tests, including twelve browser scenarios. It covers real CPU CTC training, private learning isolation, portable team transfer, conflicting labels and independent hold-out checks. Fast review verifies keyboard approval, saved navigation, OCR/source comparison, original-value highlighting, zoom, held-key suppression, check/date/currency blocking, supplier questions and Escape saving. Workspace tools cover source copying, magnification, numbered digit proposals, supplier search, decimal calculation, scoped snippets, previous-invoice comparison, thumbnails, preferences/resume, categorized queues, atomic bulk corrections and approval return before export. A delayed old-company response cannot replace the active company's documents. Browser coverage includes the hardware-aware download guide and optional Indra terms. These are functional checks, not a product accuracy percentage.

All 39 pinned public model files (10.06 GB total) were mirrored to GitHub Releases in run 43390737820. The local client downloaded and verified the Hebrew and small sequence weights from GitHub. It also downloaded/reassembled the 2,497,281,664-byte Qwen 4B weight file from two release assets and verified its whole-file SHA-256. Tests cover multipart resume, servers that ignore Range, corruption, cancellation, offline caches and blocking redirects outside GitHub. Original sources and model terms are retained beside the public weights; customer samples and personal weights are excluded.

The source-launch smoke checks two clean installs, OCR, personal training and portable learning transfer. The frozen/installed smoke additionally exercises the in-app GitHub download path and offline model guidance. The final CI result is authoritative for each pushed commit.

Checkpoint f9a23c7 passed 93 tests on Windows and Linux in run 43387019045. The installer build compiled successfully in run 43387953037, and its installed OCR/CTC/transfer checks passed; cleanup initially raced the uninstaller's log handle. Run 43390744855 passed both operating systems, standalone builds, installation, actual OCR/training/transfer and data-preserving uninstall after explicitly waiting for the uninstaller to release its log.

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

## Windows Hebrew profile regression

The previously published installer contained `heb`, `eng`, and `osd`, but its bundled Tesseract failed with absolute Hebrew model-directory arguments. Windows diagnostic run [43402011376](https://github.com/rpg05832/invoice-assistant/actions/runs/43402011376) reproduced an engine exit with narrow-argument path corruption; the exact same executable and weights listed all three languages when run from the Unicode working directory with `--tessdata-dir .`.

OCR now uses a Unicode executable/working directory and ASCII relative arguments. Unrelated Unicode image filenames are staged with their weights in a private temporary workspace and deleted afterward. Frozen applications select their own bundled engine, never an installed system engine. The installed and standalone smoke checks use Hebrew installation and user-profile directories, default per-user data paths, and a system-only PATH. They require reading actual synthetic Hebrew text and leading-zero numerals through the self-test endpoint, in addition to listing languages. This is a compatibility regression check, not a handwriting accuracy benchmark.

The guide reports runtime Hebrew availability separately from bundled file presence, provides a local reading test and SHA-256-verified base-file repair, and gives task-specific trial rankings with explicit RAM, free-memory, CPU and speed guidance.

Local validation after this fix: 114 tests passed, including the browser guide/self-test interaction and Unicode-path command regressions. The source smoke also passed actual Hebrew reading, private training, clean-install separation, GitHub downloading and explicit learning transfer. Windows packaged verification is performed by the corresponding commit's CI run before publishing the new installer.
