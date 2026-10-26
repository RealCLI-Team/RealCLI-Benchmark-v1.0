# Add `uv auth` credential management commands to uv

Our team runs uv against internal private indexes (devpi, self-hosted Simple indexes, etc.). Credentials today can only be embedded in URLs or maintained in a `.netrc`, and neither CI nor developers' local machines can manage them uniformly: scripts have to scrape tokens out of URLs, and rotating a password means hand-editing a pile of places. We want a set of `uv auth` subcommands that make recording, querying, and deleting credentials first-class features (similar in shape to `gh auth login` / `gh auth token`). The repo is at `/app` (uv's Rust workspace); implement it directly in there.

## Command shape

Add a new `uv auth` command group with 4 subcommands; `uv auth --help` must list them:

- `uv auth login <SERVICE>`: record credentials for a service.
- `uv auth logout <SERVICE>`: delete credentials for a service.
- `uv auth token <SERVICE>`: print the password/token currently stored for a service, for scripts to consume directly.
- `uv auth dir`: print the path of the credential storage directory.

The first three subcommands all accept `--username`/`-u`; `login` additionally accepts `--password` and `--token`/`-t`. To stay compatible with uv's existing options, these three subcommands must also accept `--keyring-provider` (including the `UV_KEYRING_PROVIDER` environment variable); when no system-native backend is enabled, they always use the plaintext-file backend described below (the same applies with `--keyring-provider disabled`).

## SERVICE argument parsing

- `SERVICE` can be a full URL (`https://example.com/simple`) or just a domain (`example.com`, `example.com/simple`); when only a domain is given, automatically prepend the `https://` prefix.
- Only HTTPS is allowed: `http://...` must be rejected right at argument-parsing time, with an error stating `only HTTPS is supported`; URLs that cannot be parsed are likewise rejected.
- Credentials embedded in the URL are allowed: `https://user:pass@example.com/simple` is equivalent to explicitly giving the username and password; `https://user@example.com/...` combined with `--password` also works. But giving a username (or a password) both in the URL and on the command line must be rejected with an error, shaped like `Cannot specify a username both via the URL and CLI` (for passwords, `Cannot specify a password both via the URL and CLI`); using `--token` together with any credentials embedded in the URL must also be rejected with an error.
- Index URL normalization: when the path ends with `/simple` or `/+simple` (the devpi form; `simple` is case-insensitive), the credential must be normalized to the root of that index — for example, `https://example.com/simple` normalizes to `https://example.com/`, and `https://devpi.example.com/root/+simple` normalizes to `https://devpi.example.com/root`; other paths are kept as-is. The repo's `IndexUrl` already has similar root-normalization logic; both storing and deleting must go through the same normalization, so that "log in with an index URL, log out with the root URL" resolves to the same credential entry.

## login details

- `--token <TOKEN>` is mutually exclusive with `--username` and `--password`; giving them together must be rejected with an error; with `--token`, the username is recorded as the fixed `__token__` placeholder.
- Credential source precedence: command-line arguments > URL-embedded > interactive prompt. In non-interactive scenarios (the terminal is not a TTY), when none of the sources supplies everything: a missing username errors with `No username provided`, a missing password errors with `No password provided`, both exiting non-zero; on an interactive terminal, prompt the user for input instead.
- On success, exit 0 and print one line to **stderr**: `Stored credentials for <展示 URL>`: when logging in with a username, display it as `用户名@归一化后的 URL` (e.g. `test@https://example.com/`); when logging in with a token, display only the URL (do not expose the `__token__` placeholder).
- Credentials are not validated remotely: storing a wrong password does not error.

## logout details

- When `--username` is not given, delete the `__token__` entry by default (i.e. credentials stored via the token flow); a username embedded in the URL is equally valid, and the conflict rules with `--username` are the same as for login.
- On successful deletion, exit 0 and print `Removed credentials for <展示 URL>` to **stderr** (display rules same as login).
- When there is no matching entry, exit non-zero with the error `No matching entry found for <展示 URL>`, where the display URL uses the normalized service address.
- Deletion is local only; credentials are not revoked remotely.

## token details

- On a hit, print the password/token **verbatim** to **stdout** (on its own line) and exit 0. All other output goes to stderr and must never pollute stdout — scripts will consume it directly via `$(uv auth token ...)`.
- Lookup rules: exact match on (service, username); when `--username` is not given, look up `__token__`. On a miss, exit non-zero, with stderr shaped like `error: Failed to fetch credentials for <展示 URL>`; here the display URL uses the service address as given in the request (including any username in the URL).
- Lookup must support prefix matching: a credential stored for `https://example.com/` must also be found when querying with a deeper path like `https://example.com/simple`.

## Storage backend

- The default backend is a **plaintext TOML file**: `<凭据目录>/credentials.toml`. The credential directory defaults to uv's state directory (`$XDG_DATA_HOME/uv/credentials`, or `~/.local/share/uv/credentials` when unset) and can be overridden with the `UV_CREDENTIALS_DIR` environment variable.
- The file stores plaintext (no encryption, no encoding-based obfuscation); credentials for different usernames under the same service must coexist without affecting each other.
- Writes must take effect across processes: after `login` finishes, a newly started `token` / `logout` process must be able to read the same data; concurrent writes to the file must be protected by a lock and must not produce a corrupted file.
- Stored credentials must afterwards be usable by the request pipeline as one of the credential sources when uv accesses the corresponding service (similar in status to the existing `.netrc` source, consistent with the credential-source conventions documented in the repo).
- Exit codes follow uv's existing conventions: 0 on success, 2 for both usage errors and runtime errors (see the mapping from `ExitStatus` to process exit codes in `crates/uv/src/commands/mod.rs`).

## Verification

After the change, `cargo build --bin uv` must compile. Use the built binary to actually verify end to end: point `UV_CREDENTIALS_DIR` at a temporary directory, run the full login → token → logout flow covering the edge cases above (domain auto-completion, `/simple` and `/+simple` normalization, URL-embedded credentials and their conflicts, missing-username/password errors, token prefix matching, multiple usernames coexisting for the same service), confirm the external behavior matches the contract before wrapping up, and do not break the behavior of other existing uv commands.
