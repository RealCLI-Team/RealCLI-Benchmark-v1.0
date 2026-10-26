# Behavior tests for the `uv auth` command family.
#
# These tests build the uv binary from /app and then exercise the public CLI
# contract of `uv auth login|logout|token|dir` against an isolated plaintext
# credentials store (UV_CREDENTIALS_DIR pointed at a fresh temp directory per
# test): storage semantics, URL parsing and normalization, conflict and
# validation errors, exit codes, and message structure.

import os
import subprocess
import tomllib
import uuid

import pytest

APP_DIR = "/app"
WORK_DIR = "/tmp/uv-auth-verify"
UV_BIN = "/app/target/debug/uv"

BUILD_TIMEOUT = 1500
CMD_TIMEOUT = 60


@pytest.fixture(scope="session", autouse=True)
def uv_binary():
    """Build the uv binary from /app once for the whole test session."""
    os.makedirs(WORK_DIR, exist_ok=True)
    build_log = os.path.join(WORK_DIR, "build.log")
    with open(build_log, "w") as logf:
        proc = subprocess.run(
            ["cargo", "build", "--bin", "uv"],
            cwd=APP_DIR, stdout=logf, stderr=subprocess.STDOUT,
            timeout=BUILD_TIMEOUT,
        )
    assert proc.returncode == 0, \
        "cargo build --bin uv failed; see {0}".format(build_log)
    assert os.path.isfile(UV_BIN), "uv binary not found at {0}".format(UV_BIN)
    return UV_BIN


@pytest.fixture()
def store_env(tmp_path):
    """An isolated environment: fresh HOME and UV_CREDENTIALS_DIR."""
    home = tmp_path / "home"
    home.mkdir()
    cred_dir = tmp_path / "credentials"
    return {"home": str(home), "cred_dir": str(cred_dir)}


def run_uv(store_env, *args, extra_env=None, timeout=CMD_TIMEOUT):
    """Run the built uv binary with a hermetic, non-interactive environment."""
    env = dict(os.environ)
    env["HOME"] = store_env["home"]
    env["UV_CREDENTIALS_DIR"] = store_env["cred_dir"]
    env["UV_NO_CONFIG"] = "1"
    # Make sure no preview / keyring / XDG settings leak in from the outside.
    for var in ("UV_PREVIEW_FEATURES", "UV_KEYRING_PROVIDER", "XDG_DATA_HOME"):
        env.pop(var, None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [UV_BIN] + list(args),
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=timeout, env=env, cwd=store_env["home"],
    )


def unique_host(tag):
    return "{0}-{1}.example.com".format(tag, uuid.uuid4().hex[:10])


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------

def test_cli_surface_and_existing_commands_untouched(uv_binary, store_env):
    proc = run_uv(store_env, "--version")
    assert proc.returncode == 0, (proc.returncode, proc.stderr)

    proc = run_uv(store_env, "auth", "--help")
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    for sub in ("login", "logout", "token", "dir"):
        assert sub in proc.stdout, proc.stdout

    # Pre-existing commands keep working.
    proc = run_uv(store_env, "pip", "--help")
    assert proc.returncode == 0, (proc.returncode, proc.stderr)


# ---------------------------------------------------------------------------
# login: storage semantics
# ---------------------------------------------------------------------------

def test_login_stores_credentials_in_plaintext_file(uv_binary, store_env):
    host = unique_host("login")
    proc = run_uv(store_env, "auth", "login", "https://" + host,
                  "--username", "public", "--password", "heron")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "Stored credentials for" in proc.stderr, proc.stderr
    assert "public@https://{0}/".format(host) in proc.stderr, proc.stderr

    # The plaintext backend persists to <UV_CREDENTIALS_DIR>/credentials.toml.
    cred_file = os.path.join(store_env["cred_dir"], "credentials.toml")
    assert os.path.isfile(cred_file), \
        "expected a plaintext credentials file at {0}".format(cred_file)
    with open(cred_file, "rb") as f:
        parsed = tomllib.loads(f.read().decode())
    strings = []

    def _collect(node):
        if isinstance(node, str):
            strings.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                _collect(value)
        elif isinstance(node, list):
            for value in node:
                _collect(value)

    _collect(parsed)
    # Credentials are stored as plaintext: no encryption or encoding.
    assert "heron" in strings, strings
    assert "public" in strings, strings
    assert any(host in s for s in strings), strings


def test_login_token_uses_placeholder_username(uv_binary, store_env):
    host = unique_host("tok")
    proc = run_uv(store_env, "auth", "login", "https://" + host,
                  "-t", "test-token")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "Stored credentials for" in proc.stderr, proc.stderr
    assert "https://{0}/".format(host) in proc.stderr, proc.stderr
    # The __token__ placeholder is an internal detail, not shown to the user.
    assert "__token__" not in proc.stderr, proc.stderr

    # A token login is retrievable without specifying a username.
    proc = run_uv(store_env, "auth", "token", "https://" + host)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stdout.strip() == "test-token", proc.stdout


def test_login_does_not_validate_credentials_remotely(uv_binary, store_env):
    # A wrong password is stored without complaint: there is no remote check.
    host = unique_host("nocheck")
    proc = run_uv(store_env, "auth", "login", "https://" + host,
                  "--username", "nobody", "--password", "definitely-wrong")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)


# ---------------------------------------------------------------------------
# token: retrieval semantics
# ---------------------------------------------------------------------------

def test_token_prints_password_to_stdout(uv_binary, store_env):
    host = unique_host("show")
    proc = run_uv(store_env, "auth", "login", "https://" + host,
                  "--username", "public", "--password", "heron")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    # The password goes to stdout as a single bare line for scripting use.
    proc = run_uv(store_env, "auth", "token", "https://" + host,
                  "-u", "public")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stdout.strip() == "heron", proc.stdout
    assert "Stored credentials" not in proc.stdout, proc.stdout

    # A username-based credential is not returned without its username ...
    proc = run_uv(store_env, "auth", "token", "https://" + host)
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "Failed to fetch credentials for" in proc.stderr, proc.stderr

    # ... nor with a different username.
    proc = run_uv(store_env, "auth", "token", "https://" + host,
                  "--username", "wronguser")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "Failed to fetch credentials for" in proc.stderr, proc.stderr
    assert "wronguser@https://{0}".format(host) in proc.stderr, proc.stderr


def test_token_prefix_match_on_deeper_paths(uv_binary, store_env):
    host = unique_host("prefix")
    proc = run_uv(store_env, "auth", "login", "https://" + host,
                  "--username", "public", "--password", "heron")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    # Credentials stored for a service root apply to deeper request paths.
    proc = run_uv(store_env, "auth", "token",
                  "https://{0}/simple".format(host), "--username", "public")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stdout.strip() == "heron", proc.stdout


# ---------------------------------------------------------------------------
# logout: removal semantics
# ---------------------------------------------------------------------------

def test_logout_removes_credentials(uv_binary, store_env):
    host = unique_host("out")
    proc = run_uv(store_env, "auth", "login", "https://" + host,
                  "--username", "public", "--password", "heron")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    proc = run_uv(store_env, "auth", "logout", "https://" + host,
                  "--username", "public")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "Removed credentials for" in proc.stderr, proc.stderr
    assert "public@https://{0}/".format(host) in proc.stderr, proc.stderr

    # The credential is really gone.
    proc = run_uv(store_env, "auth", "token", "https://" + host,
                  "--username", "public")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)

    # Logging out twice reports the missing entry.
    proc = run_uv(store_env, "auth", "logout", "https://" + host,
                  "--username", "public")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "No matching entry found for" in proc.stderr, proc.stderr


def test_logout_defaults_to_token_entry(uv_binary, store_env):
    host = unique_host("outtok")
    proc = run_uv(store_env, "auth", "login", "https://" + host,
                  "--token", "test-token")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    # Without --username, logout removes the token entry.
    proc = run_uv(store_env, "auth", "logout", "https://" + host)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "Removed credentials for" in proc.stderr, proc.stderr
    assert "https://{0}/".format(host) in proc.stderr, proc.stderr
    assert "__token__" not in proc.stderr, proc.stderr

    proc = run_uv(store_env, "auth", "token", "https://" + host)
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)

    # With only a username-based credential stored, the default (token)
    # removal finds nothing.
    other = unique_host("outbasic")
    proc = run_uv(store_env, "auth", "login", "https://" + other,
                  "--username", "public", "--password", "heron")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    proc = run_uv(store_env, "auth", "logout", "https://" + other)
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "No matching entry found for" in proc.stderr, proc.stderr


# ---------------------------------------------------------------------------
# SERVICE parsing and index URL normalization
# ---------------------------------------------------------------------------

def test_service_url_parsing(uv_binary, store_env):
    host = unique_host("bare")
    # A bare domain gets https:// prepended.
    proc = run_uv(store_env, "auth", "login", host,
                  "--username", "test", "--password", "test")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "test@https://{0}/".format(host) in proc.stderr, proc.stderr

    # Only HTTPS is supported; HTTP is rejected at argument parsing time.
    proc = run_uv(store_env, "auth", "login", "http://" + host,
                  "--username", "test", "--password", "test")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "only HTTPS is supported" in proc.stderr, proc.stderr

    # Unparseable service names are rejected.
    proc = run_uv(store_env, "auth", "login", "not a valid url",
                  "--username", "test", "--password", "test")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)


def test_index_url_simple_suffix_is_normalized(uv_binary, store_env):
    host = unique_host("norm")
    # `/simple` is stripped down to the index root.
    proc = run_uv(store_env, "auth", "login", "https://{0}/simple".format(host),
                  "--username", "testuser", "--password", "testpass")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "testuser@https://{0}/".format(host) in proc.stderr, proc.stderr

    # The stored credential answers for the root and for the `/simple` path.
    proc = run_uv(store_env, "auth", "token", "https://" + host,
                  "--username", "testuser")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stdout.strip() == "testpass", proc.stdout
    proc = run_uv(store_env, "auth", "token", "https://{0}/simple".format(host),
                  "--username", "testuser")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stdout.strip() == "testpass", proc.stdout

    # devpi-style `/+simple` suffixes are stripped as well.
    devpi = unique_host("devpi")
    proc = run_uv(store_env, "auth", "login",
                  "https://{0}/root/+simple".format(devpi),
                  "--username", "devpiuser", "--password", "devpipass")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "devpiuser@https://{0}/root".format(devpi) in proc.stderr, proc.stderr
    proc = run_uv(store_env, "auth", "token",
                  "https://{0}/root/+simple".format(devpi),
                  "--username", "devpiuser")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stdout.strip() == "devpipass", proc.stdout

    # A trailing slash before the suffix does not matter.
    trailing = unique_host("slash")
    proc = run_uv(store_env, "auth", "login",
                  "https://{0}/simple/".format(trailing),
                  "--username", "slashuser", "--password", "slashpass")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "slashuser@https://{0}/".format(trailing) in proc.stderr, proc.stderr

    # Non-index paths are preserved as-is.
    custom = unique_host("custom")
    proc = run_uv(store_env, "auth", "login",
                  "https://{0}/api/v1".format(custom),
                  "--username", "apiuser", "--password", "apipass")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "apiuser@https://{0}/api/v1".format(custom) in proc.stderr, \
        proc.stderr

    # Logout applies the same normalization, so an index URL and its root
    # address the same entry.
    proc = run_uv(store_env, "auth", "logout", "https://{0}/simple".format(host),
                  "--username", "testuser")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "Removed credentials for" in proc.stderr, proc.stderr
    proc = run_uv(store_env, "auth", "token", "https://" + host,
                  "--username", "testuser")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)


# ---------------------------------------------------------------------------
# Credential source conflicts and validation
# ---------------------------------------------------------------------------

def test_url_embedded_credentials(uv_binary, store_env):
    host = unique_host("embed")
    # Credentials embedded in the URL are picked up.
    proc = run_uv(store_env, "auth", "login",
                  "https://test:password@{0}/simple".format(host))
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "test@https://{0}/".format(host) in proc.stderr, proc.stderr
    proc = run_uv(store_env, "auth", "token", "https://" + host,
                  "--username", "test")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stdout.strip() == "password", proc.stdout

    # A username in the URL combines with --password.
    host2 = unique_host("embed2")
    proc = run_uv(store_env, "auth", "login",
                  "https://test@{0}/simple".format(host2),
                  "--password", "password")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    # ... but a username in both the URL and the CLI is a conflict.
    proc = run_uv(store_env, "auth", "login",
                  "https://test@{0}/simple".format(host2),
                  "--username", "different", "--password", "password")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "Cannot specify a username both via the URL and CLI" in proc.stderr, \
        proc.stderr

    # ... and so is a password in both the URL and the CLI.
    proc = run_uv(store_env, "auth", "login",
                  "https://test:password@{0}/simple".format(host2),
                  "--password", "different")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "Cannot specify a password both via the URL and CLI" in proc.stderr, \
        proc.stderr

    # --token conflicts with URL-embedded credentials.
    proc = run_uv(store_env, "auth", "login",
                  "https://test:password@{0}/simple".format(host2),
                  "--token", "some-token")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)

    # The same username conflict applies to `token` and `logout`.
    proc = run_uv(store_env, "auth", "token",
                  "https://test@{0}/simple".format(host2),
                  "--username", "different")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "Cannot specify a username both via the URL and CLI" in proc.stderr, \
        proc.stderr
    proc = run_uv(store_env, "auth", "logout",
                  "https://test@{0}/simple".format(host2),
                  "--username", "different")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "Cannot specify a username both via the URL and CLI" in proc.stderr, \
        proc.stderr


def test_login_option_conflicts_and_missing_inputs(uv_binary, store_env):
    host = unique_host("valid")
    # --token is mutually exclusive with --password and --username.
    proc = run_uv(store_env, "auth", "login", "https://" + host,
                  "--token", "t", "--password", "p")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    proc = run_uv(store_env, "auth", "login", "https://" + host,
                  "--token", "t", "--username", "u")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)

    # Non-interactive invocations fail instead of prompting.
    proc = run_uv(store_env, "auth", "login", "https://" + host)
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "No username provided" in proc.stderr, proc.stderr
    proc = run_uv(store_env, "auth", "login", "https://" + host,
                  "--username", "test")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "No password provided" in proc.stderr, proc.stderr

    # The SERVICE argument is required.
    proc = run_uv(store_env, "auth", "login")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "SERVICE" in proc.stderr, proc.stderr
    proc = run_uv(store_env, "auth", "logout")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    proc = run_uv(store_env, "auth", "token")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)


# ---------------------------------------------------------------------------
# Multiple usernames per service
# ---------------------------------------------------------------------------

def test_multiple_usernames_coexist_per_service(uv_binary, store_env):
    host = unique_host("multi")
    for user, password in (("user1", "pass1"), ("user2", "pass2")):
        proc = run_uv(store_env, "auth", "login", "https://" + host,
                      "--username", user, "--password", password)
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    for user, password in (("user1", "pass1"), ("user2", "pass2")):
        proc = run_uv(store_env, "auth", "token", "https://" + host,
                      "--username", user)
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        assert proc.stdout.strip() == password, proc.stdout

    # Removing one username leaves the other untouched.
    proc = run_uv(store_env, "auth", "logout", "https://" + host,
                  "--username", "user1")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    proc = run_uv(store_env, "auth", "token", "https://" + host,
                  "--username", "user1")
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    proc = run_uv(store_env, "auth", "token", "https://" + host,
                  "--username", "user2")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stdout.strip() == "pass2", proc.stdout


# ---------------------------------------------------------------------------
# Storage location and `uv auth dir`
# ---------------------------------------------------------------------------

def test_auth_dir_reports_credentials_directory(uv_binary, store_env):
    proc = run_uv(store_env, "auth", "dir")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    # UV_CREDENTIALS_DIR overrides the storage location.
    assert proc.stdout.strip() == store_env["cred_dir"], proc.stdout

    # By default the directory lives in uv's state directory under the home.
    env = dict(os.environ)
    env["HOME"] = store_env["home"]
    env["UV_NO_CONFIG"] = "1"
    for var in ("UV_PREVIEW_FEATURES", "UV_KEYRING_PROVIDER",
                "XDG_DATA_HOME", "UV_CREDENTIALS_DIR"):
        env.pop(var, None)
    proc = subprocess.run(
        [UV_BIN, "auth", "dir"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=CMD_TIMEOUT, env=env, cwd=store_env["home"],
    )
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "/.local/share/uv/credentials" in proc.stdout, proc.stdout


# ---------------------------------------------------------------------------
# --keyring-provider compatibility flag
# ---------------------------------------------------------------------------

def test_keyring_provider_disabled_still_uses_file_backend(uv_binary, store_env):
    host = unique_host("kr")
    proc = run_uv(store_env, "auth", "login", "https://" + host,
                  "--username", "public", "--password", "heron",
                  "--keyring-provider", "disabled")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    proc = run_uv(store_env, "auth", "token", "https://" + host,
                  "--username", "public", "--keyring-provider", "disabled")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stdout.strip() == "heron", proc.stdout

    # The environment variable form of the option is honored as well.
    proc = run_uv(store_env, "auth", "logout", "https://" + host,
                  "--username", "public",
                  extra_env={"UV_KEYRING_PROVIDER": "disabled"})
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)


# ---------------------------------------------------------------------------
# Concurrent writers
# ---------------------------------------------------------------------------

def test_concurrent_logins_do_not_corrupt_store(uv_binary, store_env):
    # Parallel writers must be serialized by a lock so no credential is lost
    # and the store file is never corrupted. Seed the store first so the
    # concurrent writers all take the read-modify-write path under the lock.
    seed = unique_host("seed")
    proc = run_uv(store_env, "auth", "login", "https://" + seed,
                  "--username", "seeduser", "--password", "seedpass")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    hosts = [unique_host("conc{0}".format(i)) for i in range(8)]
    env = dict(os.environ)
    env["HOME"] = store_env["home"]
    env["UV_CREDENTIALS_DIR"] = store_env["cred_dir"]
    env["UV_NO_CONFIG"] = "1"
    for var in ("UV_PREVIEW_FEATURES", "UV_KEYRING_PROVIDER", "XDG_DATA_HOME"):
        env.pop(var, None)
    procs = [
        subprocess.Popen(
            [UV_BIN, "auth", "login", "https://" + host,
             "--username", "user{0}".format(i), "--password",
             "pass{0}".format(i)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=env, cwd=store_env["home"],
        )
        for i, host in enumerate(hosts)
    ]
    for proc in procs:
        _, stderr = proc.communicate(timeout=CMD_TIMEOUT)
        assert proc.returncode == 0, (proc.returncode, stderr)

    for i, host in enumerate(hosts):
        proc = run_uv(store_env, "auth", "token", "https://" + host,
                      "--username", "user{0}".format(i))
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        assert proc.stdout.strip() == "pass{0}".format(i), proc.stdout
