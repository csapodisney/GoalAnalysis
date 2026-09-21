# Arthur v3.3.1 — Windows credential recovery

The dashboard failed to launch because its wrapper required successful DPAPI
decryption of the saved Odds key. Legacy writes appended CR/LF, while reads
passed raw file text directly into decryption. The exact user's Windows failure
is not reproduced here; no private key or encrypted credential was accessed.

The shared Windows helper now trims legacy encrypted input and stores new
encrypted files without trailing newlines. It verifies the encrypted temporary
file by decrypting disk contents before atomic replacement. Startup reads no
longer rewrite persistent keys from process environment overrides.

The targeted repair command tries the existing Odds key first and requests only
that key if it cannot be loaded. It prints no key, makes no network request and
does not touch Codex login or scheduled-task registration. The regular installer
uses the same helper and only re-prompts for the specific failed credential.
The scheduler uses the shared reader without interactive recovery.

The dashboard can now open with an unavailable sports credential, showing its
missing status and an actionable console warning. This permits viewing saved
reports; it does not claim that live provider access works.

Validation: the existing Python suite passes (365 tests). A real-Windows test
covers encrypted round-trip, legacy CR/LF, read-only environment overrides,
preserving stored data after blank input, damaged ciphertext, atomic replacement
and temporary-file cleanup. This test is skipped on Linux. PowerShell and DPAPI
could not be executed in this development environment; the Windows repair
command performs the actual account-specific read/decrypt and save verification.

The small ZIP overlays the existing v3.3 installation. It contains no private
configuration, secret files, ledger, reports or virtual environment.
