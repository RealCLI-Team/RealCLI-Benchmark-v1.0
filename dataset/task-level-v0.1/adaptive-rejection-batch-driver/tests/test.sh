#!/usr/bin/env bash
# HARBOR_REWARD_WRAPPER_V02
set +e
mkdir -p /logs/verifier
_harbor_inner_script_v02="$(mktemp)"
sed '1,/^__HARBOR_ORIGINAL_SCRIPT_V02__$/d' "$0" > "$_harbor_inner_script_v02"
bash "$_harbor_inner_script_v02"
_harbor_rc_v02=$?
rm -f "$_harbor_inner_script_v02"
if [ "$_harbor_rc_v02" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit 0
__HARBOR_ORIGINAL_SCRIPT_V02__
set -uo pipefail

APP=${APP:-/app}
DATA=$APP/inputs/data
fail() { echo "FAIL: $*" >&2; exit 1; }

# Ensure R has a writable scratch directory even if the system /tmp is unusable.
pick_tmpdir() {
  local cand probe
  for cand in "${TMPDIR:-}" /tmp "$APP/.rtmp-tests"; do
    [ -n "$cand" ] || continue
    mkdir -p "$cand" 2>/dev/null || continue
    probe="$cand/.probe.$$"
    if mkdir "$probe" 2>/dev/null && printf 'x' > "$probe/w" 2>/dev/null; then
      rm -rf "$probe"
      printf '%s\n' "$cand"
      return 0
    fi
    rm -rf "$probe" 2>/dev/null || true
  done
  return 1
}
SCRATCH=$(pick_tmpdir) || fail "no writable temporary directory available"
export TMPDIR="$SCRATCH" TMP="$SCRATCH" TEMP="$SCRATCH"

[ -f "$APP/ars.R" ]        || fail "missing $APP/ars.R"
[ -f "$APP/results.tsv" ]  || fail "missing $APP/results.tsv"
[ -f "$DATA/cases.tsv" ]   || fail "missing cases.tsv fixture"
[ -f "$DATA/overrides.tsv" ] || fail "missing overrides.tsv fixture"

# Locate Rscript: prefer PATH, otherwise probe the usual install locations so the
# checks still work when PATH is minimal in the grading environment.
RSCRIPT="${RSCRIPT:-$(command -v Rscript 2>/dev/null || true)}"
if [ -z "$RSCRIPT" ]; then
  for cand in /usr/lib/R/bin/Rscript /usr/local/lib/R/bin/Rscript \
              /usr/local/bin/Rscript /usr/bin/Rscript /opt/R/*/bin/Rscript \
              /opt/conda/bin/Rscript /opt/conda/envs/*/bin/Rscript \
              "$HOME"/envs/*/bin/Rscript /volume/*/users/*/envs/*/bin/Rscript; do
    if [ -x "$cand" ]; then RSCRIPT="$cand"; break; fi
  done
fi
[ -n "$RSCRIPT" ] || fail "Rscript not available in this environment"

# --- leak scan on produced outputs ------------------------------------------
if grep -RInE '(https?://|(25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])(\.(25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])){3}|/home/[A-Za-z0-9._-]+|/root/|token|api[_-]?key)' \
     "$APP/results.tsv" $APP/case_*_samples.txt >/dev/null 2>&1; then
  fail "output leaks an absolute path, IP, URL or token"
fi

# --- source-has-no-side-effects ---------------------------------------------
SRCCHK=$(mktemp -d "$SCRATCH/arschk.XXXXXX") || fail "cannot create scratch directory"
cp "$APP/ars.R" "$SRCCHK/ars.R"
( cd "$SRCCHK" && "$RSCRIPT" -e 'source("ars.R"); cat("sourced\n")' >/dev/null 2>&1 ) || fail "sourcing ars.R errored"
created=$(find "$SRCCHK" -type f ! -name ars.R | wc -l)
[ "$created" -eq 0 ] || fail "sourcing ars.R created artifacts"

# --- negative-path contracts ------------------------------------------------
"$RSCRIPT" - "$APP/ars.R" <<'RNEG' || fail "negative-path contracts not enforced"
args <- commandArgs(trailingOnly = TRUE)
source(args[1])
errs <- function(e) tryCatch({ force(e); FALSE }, error = function(x) TRUE)
lf <- function(x) log(x) - x   # gamma(shape=2, rate=1), log-concave on (0, Inf)
stopifnot(errs(ars(lf, -3, c(0, Inf))))
stopifnot(errs(ars(lf, 3.5, c(0, Inf))))
stopifnot(errs(ars(lf, 100, c(5, 1))))
stopifnot(errs(ars(lf, 100, c(NA, 1))))
stopifnot(errs(ars(42, 100, c(0, 1))))
bimodal <- function(x) log(exp(-(x - 2)^2 / 0.2) + exp(-(x + 2)^2 / 0.2))
stopifnot(errs(ars(bimodal, 100, c(-5, 5))))
s <- ars(lf, 40, c(0, Inf))
stopifnot(length(s) == 40, all(is.finite(s)), all(s > 0))
cat("neg-ok\n")
RNEG

# --- content verification of the batch outputs ------------------------------
"$RSCRIPT" - "$APP" <<'RVER' || fail "output content verification failed"
app <- commandArgs(trailingOnly = TRUE)[1]
stop2 <- function(...) { cat("VERIFY-FAIL:", ..., "\n"); quit(status = 1) }

## independent density + moments (do NOT reuse solver code)
dens <- function(family, pp) {
  g <- function(k) as.numeric(pp[[k]])
  switch(family,
    gamma   = function(x) x^(g("shape")-1) * exp(-g("rate")*x),
    weibull = function(x) x^(g("shape")-1) * exp(-(x/g("scale"))^g("shape")),
    exp     = function(x) exp(-g("rate")*x),
    normal  = function(x) exp(-((x-g("mean"))^2)/(2*g("sd")^2)),
    logistic= function(x){ z <- (x-g("location"))/g("scale"); exp(-z)/(1+exp(-z))^2 },
    beta    = function(x) x^(g("a")-1) * (1-x)^(g("b")-1),
    stop("fam"))
}
logconcave <- function(family, pp) {
  g <- function(k) as.numeric(pp[[k]])
  switch(family,
    gamma   = g("shape") >= 1,
    weibull = g("shape") >= 1,
    beta    = g("a") >= 1 && g("b") >= 1,
    exp = TRUE, normal = TRUE, logistic = TRUE, FALSE)
}
tmoments <- function(family, pp, sup) {
  f <- dens(family, pp)
  Z  <- integrate(function(x) f(x),      sup[1], sup[2], rel.tol=1e-9, subdivisions=400L)$value
  m1 <- integrate(function(x) x*f(x),    sup[1], sup[2], rel.tol=1e-9, subdivisions=400L)$value
  m2 <- integrate(function(x) x^2*f(x),  sup[1], sup[2], rel.tol=1e-9, subdivisions=400L)$value
  mean <- m1/Z; list(mean = mean, sd = sqrt(max(m2/Z - mean^2, 0)))
}
pparse <- function(s) {
  out <- list(); if (is.na(s) || !nchar(trimws(s))) return(out)
  for (kv in strsplit(s, ";", fixed=TRUE)[[1]]) {
    kv <- trimws(kv); if (!nchar(kv)) next
    p <- strsplit(kv, "=", fixed=TRUE)[[1]]; out[[trimws(p[1])]] <- p[2]
  }
  out
}
rd <- function(p) read.delim(p, sep="\t", quote="", comment.char="", header=TRUE,
                             colClasses="character", strip.white=FALSE, check.names=FALSE,
                             na.strings=character(0))

cs <- rd(file.path(app, "inputs/data/cases.tsv"))
cs <- cs[!duplicated(cs$case_id, fromLast=TRUE), , drop=FALSE]   # last wins
ov <- rd(file.path(app, "inputs/data/overrides.tsv"))
for (i in seq_len(nrow(ov))) {
  j <- which(cs$case_id == ov$case_id[i])
  if (length(j) == 1) for (c in c("n","mean_tol","sd_tol"))
    if (nchar(trimws(ov[[c]][i]))) cs[[c]][j] <- ov[[c]][i]
}

res <- rd(file.path(app, "results.tsv"))
if (!identical(names(res), c("case_id","status","n_samples","sample_mean","sample_sd")))
  stop2("bad header/column order:", paste(names(res), collapse=","))
if (!identical(res$case_id, res$case_id[order(res$case_id, method="radix")]))
  stop2("results not sorted by ascending byte order")
if (nrow(res) != nrow(cs)) stop2("row count mismatch", nrow(res), nrow(cs))

# near-duplicate ids both present
if (!("dupc" %in% res$case_id) || !(" dupc" %in% res$case_id))
  stop2("near-duplicate case_id rows missing")

miss <- function(v) is.na(v) || !nchar(trimws(v))
for (i in seq_len(nrow(cs))) {
  cid <- cs$case_id[i]; r <- res[res$case_id == cid, , drop=FALSE]
  if (nrow(r) != 1) stop2("case not uniquely resolved:", cid)
  fam <- cs$family[i]; par <- cs$params[i]; sup <- cs$support[i]; nn <- cs$n[i]
  sfile <- file.path(app, paste0("case_", cid, "_samples.txt"))
  invalid <- miss(fam) || miss(par) || miss(sup) || miss(nn)
  pp <- if (!miss(par)) pparse(par) else list()
  nonlc <- (!invalid) && !logconcave(fam, pp)
  if (invalid || nonlc) {
    if (r$status != "ERROR") stop2("expected ERROR for", cid, "got", r$status)
    if (as.integer(r$n_samples) != 0L) stop2("ERROR n_samples not 0 for", cid)
    if (toupper(r$sample_mean) != "NA" || toupper(r$sample_sd) != "NA")
      stop2("ERROR moments not NA for", cid)
    if (file.exists(sfile)) stop2("ERROR case produced a sample file:", cid)
    next
  }
  spv <- strsplit(sup, ",", fixed=TRUE)[[1]]
  support <- c(as.numeric(spv[1]), as.numeric(spv[2]))
  nval <- as.integer(nn)
  if (r$status != "PASS") stop2("expected PASS for", cid, "got", r$status)
  if (as.integer(r$n_samples) != nval) stop2("n_samples != resolved n for", cid)
  if (!file.exists(sfile)) stop2("missing sample file for", cid)
  x <- suppressWarnings(as.numeric(readLines(sfile)))
  if (length(x) != nval) stop2("sample line count wrong for", cid, length(x))
  if (!all(is.finite(x))) stop2("non-finite samples for", cid)
  if (!all(x >= support[1] & x <= support[2])) stop2("samples out of support for", cid)
  tm <- tmoments(fam, pp, support)
  mt <- as.numeric(cs$mean_tol[i]); st <- as.numeric(cs$sd_tol[i])
  sm <- mean(x); ss <- sd(x)
  if (abs(sm - tm$mean) > mt) stop2("mean out of tol for", cid, sm, tm$mean, mt)
  if (abs(ss - tm$sd)   > st) stop2("sd out of tol for", cid, ss, tm$sd, st)
}

# explicit override / dedup checks
if (as.integer(res$n_samples[res$case_id == "wbl"]) != 7000L)
  stop2("override n not applied for wbl")
if (as.integer(res$n_samples[res$case_id == "dupc"]) != 4000L)
  stop2("dedup winner (last occurrence) not used for dupc")
if (res$status[res$case_id == "bad_missing"] != "ERROR")
  stop2("missing-family row not ERROR")
if (res$status[res$case_id == "ncv"] != "ERROR")
  stop2("non-log-concave row not ERROR")

cat("VERIFY-OK\n")
RVER

echo "ALL TESTS PASSED"
