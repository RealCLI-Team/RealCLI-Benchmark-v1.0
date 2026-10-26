#!/usr/bin/env bash
set -euo pipefail

APP=${APP:-/app}
export APP
mkdir -p "$APP"

# Make sure R has a writable scratch directory even when the system /tmp is
# unusable (read-only or full); otherwise R aborts with "cannot create R_TempDir".
pick_tmpdir() {
  local cand probe
  for cand in "${TMPDIR:-}" /tmp "$APP/.rtmp"; do
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
SCRATCH=$(pick_tmpdir) || { echo "no writable temporary directory available" >&2; exit 1; }
export TMPDIR="$SCRATCH" TMP="$SCRATCH" TEMP="$SCRATCH"

# Locate Rscript: prefer PATH, otherwise probe the usual install locations so the
# run also works when PATH is minimal.
RSCRIPT="${RSCRIPT:-$(command -v Rscript 2>/dev/null || true)}"
if [ -z "$RSCRIPT" ]; then
  for cand in /usr/lib/R/bin/Rscript /usr/local/lib/R/bin/Rscript \
              /usr/local/bin/Rscript /usr/bin/Rscript /opt/R/*/bin/Rscript \
              /opt/conda/bin/Rscript /opt/conda/envs/*/bin/Rscript \
              "$HOME"/envs/*/bin/Rscript; do
    if [ -x "$cand" ]; then RSCRIPT="$cand"; break; fi
  done
fi
[ -n "$RSCRIPT" ] || { echo "Rscript not available in this environment" >&2; exit 1; }
export RSCRIPT
PATH="$(dirname "$RSCRIPT"):$PATH"; export PATH

cat > "$APP/ars.R" <<'ARS_EOF'
## Adaptive Rejection Sampler for 1-D log-concave targets.
## Loading this file has no side effects.

## ---- density library -------------------------------------------------------
.parse_params <- function(params) {
  params <- as.character(params)
  if (is.na(params) || nchar(trimws(params)) == 0) return(list())
  parts <- strsplit(params, ";", fixed = TRUE)[[1]]
  out <- list()
  for (p in parts) {
    p <- trimws(p)
    if (nchar(p) == 0) next
    kv <- strsplit(p, "=", fixed = TRUE)[[1]]
    if (length(kv) != 2) stop("bad param entry: ", p)
    out[[trimws(kv[1])]] <- as.numeric(kv[2])
  }
  out
}

build_log_density <- function(family, params) {
  family <- as.character(family)
  pp <- .parse_params(params)
  g <- function(k, d) if (!is.null(pp[[k]])) pp[[k]] else d
  if (family == "gamma") {
    shape <- g("shape", NA); rate <- g("rate", 1)
    function(x) (shape - 1) * log(x) - rate * x
  } else if (family == "weibull") {
    shape <- g("shape", NA); scale <- g("scale", 1)
    function(x) (shape - 1) * log(x) - (x / scale)^shape
  } else if (family == "exp") {
    rate <- g("rate", 1)
    function(x) -rate * x
  } else if (family == "normal") {
    mean <- g("mean", 0); sd <- g("sd", 1)
    function(x) -((x - mean)^2) / (2 * sd^2)
  } else if (family == "logistic") {
    location <- g("location", 0); scale <- g("scale", 1)
    function(x) { z <- (x - location) / scale; -z - 2 * log1p(exp(-z)) }
  } else if (family == "beta") {
    a <- g("a", NA); b <- g("b", NA)
    function(x) (a - 1) * log(x) + (b - 1) * log(1 - x)
  } else {
    stop("unknown family: ", family)
  }
}

.density_fun <- function(family, params) {
  lf <- build_log_density(family, params)
  function(x) exp(vapply(x, function(xi) as.numeric(lf(xi)), numeric(1)))
}

## ---- true moments by numerical integration ---------------------------------
true_moments <- function(family, params, support) {
  lo <- support[1]; hi <- support[2]
  f <- .density_fun(family, params)
  Z  <- integrate(function(x) f(x),          lo, hi, rel.tol = 1e-9, subdivisions = 400L)$value
  m1 <- integrate(function(x) x * f(x),       lo, hi, rel.tol = 1e-9, subdivisions = 400L)$value
  m2 <- integrate(function(x) x^2 * f(x),     lo, hi, rel.tol = 1e-9, subdivisions = 400L)$value
  mean <- m1 / Z
  var  <- m2 / Z - mean^2
  list(mean = mean, sd = sqrt(max(var, 0)))
}

## ---- core sampler ----------------------------------------------------------
ars <- function(logf, n, support, initial_points = NULL, max_points = 250L) {
  if (!is.function(logf)) stop("density callback must be a function")
  if (length(n) != 1 || !is.finite(n) || n <= 0 || n != floor(n))
    stop("n must be a positive integer")
  if (length(support) != 2) stop("support must have length 2")
  lo <- support[1]; hi <- support[2]
  if (is.na(lo) || is.na(hi)) stop("support endpoints must not be NA")
  if (!(lo < hi)) stop("support must be strictly increasing")

  ## scalar/vectorized-safe log density
  h <- function(x) vapply(x, function(xi) as.numeric(logf(xi)), numeric(1))
  hp <- function(x) {
    eps <- 1e-5 * pmax(1, abs(x))
    xl <- x - eps; xr <- x + eps
    if (is.finite(lo)) xl[xl <= lo] <- x[xl <= lo]
    if (is.finite(hi)) xr[xr >= hi] <- x[xr >= hi]
    (h(xr) - h(xl)) / (xr - xl)
  }

  finite_pt <- function() {
    if (is.finite(lo) && is.finite(hi)) return((lo + hi) / 2)
    if (is.finite(lo)) return(lo + 1)
    if (is.finite(hi)) return(hi - 1)
    0
  }

  ## locate mode with expanding bracket
  L <- if (is.finite(lo)) lo else finite_pt() - 1
  R <- if (is.finite(hi)) hi else finite_pt() + 1
  m <- finite_pt()
  for (it in 1:80) {
    o <- optimize(function(x) h(x), lower = L, upper = R, maximum = TRUE)
    m <- o$maximum
    grew <- FALSE
    span <- R - L
    if (!is.finite(lo) && (m - L) < span * 0.05) { L <- L - span; grew <- TRUE }
    if (!is.finite(hi) && (R - m) < span * 0.05) { R <- R + span; grew <- TRUE }
    if (!grew) break
  }
  hm <- h(m)

  ## finite working bounds capturing essentially all mass
  A <- lo; B <- hi
  if (!is.finite(lo)) {
    d <- max(1, abs(m)); A <- m - d
    while ((m - A) < 1e7 && is.finite(h(A)) && h(A) > hm - 50) { d <- d * 1.6; A <- m - d }
  }
  if (!is.finite(hi)) {
    d <- max(1, abs(m)); B <- m + d
    while ((B - m) < 1e7 && is.finite(h(B)) && h(B) > hm - 50) { d <- d * 1.6; B <- m + d }
  }

  ## log-concavity gate: derivative must be (numerically) non-increasing
  gx <- seq(A + (B - A) * 0.02, B - (B - A) * 0.02, length.out = 40L)
  gs <- hp(gx)
  if (any(!is.finite(gs))) {
    fin <- is.finite(gs); gx <- gx[fin]; gs <- gs[fin]
  }
  tol_c <- 1e-4 * (max(abs(gs)) + 1)
  if (length(gs) >= 2 && any(diff(gs) > tol_c))
    stop("target is not log-concave on the given support")

  ## initial abscissae
  if (!is.null(initial_points) && length(initial_points) > 0) {
    init <- sort(unique(as.numeric(initial_points)))
    init <- init[init > A & init < B]
  } else init <- numeric(0)
  if (length(init) < 2)
    init <- seq(A + (B - A) * 0.15, B - (B - A) * 0.15, length.out = 5L)
  X <- sort(unique(init))

  build_env <- function(X) {
    k <- length(X)
    s <- hp(X)
    hv <- h(X)
    b <- hv - s * X                       # line_j(x) = s_j x + b_j
    M <- max(hv)
    b <- b - M                            # shift for stability
    z <- numeric(k + 1)
    z[1] <- A; z[k + 1] <- B
    if (k >= 2) for (j in 1:(k - 1)) {
      if (abs(s[j] - s[j + 1]) < 1e-12) z[j + 1] <- (X[j] + X[j + 1]) / 2
      else z[j + 1] <- (b[j + 1] - b[j]) / (s[j] - s[j + 1])
    }
    z <- pmin(pmax(z, A), B)
    z <- sort(z)
    zL <- z[1:k]; zR <- z[2:(k + 1)]
    lm <- numeric(k)
    for (j in 1:k) {
      p <- zL[j]; q <- zR[j]; sj <- s[j]
      if (q <= p) { lm[j] <- -Inf; next }
      if (abs(sj) < 1e-12) lm[j] <- b[j] + log(q - p)
      else {
        mx <- max(sj * p, sj * q)
        lm[j] <- b[j] + mx + log(abs(exp(sj * q - mx) - exp(sj * p - mx))) - log(abs(sj))
      }
    }
    list(k = k, s = s, b = b, zL = zL, zR = zR, lm = lm, M = M)
  }

  draw_env <- function(env, nb) {
    lm <- env$lm
    w <- exp(lm - max(lm[is.finite(lm)]))
    w[!is.finite(w)] <- 0
    seg <- sample.int(env$k, nb, replace = TRUE, prob = w)
    p <- env$zL[seg]; q <- env$zR[seg]; sj <- env$s[seg]
    u <- runif(nb)
    x <- numeric(nb)
    flat <- abs(sj) < 1e-12
    x[flat] <- p[flat] + u[flat] * (q[flat] - p[flat])
    if (any(!flat)) {
      pp <- p[!flat]; qq <- q[!flat]; ss <- sj[!flat]; uu <- u[!flat]
      mx <- pmax(ss * pp, ss * qq)
      term <- (1 - uu) * exp(ss * pp - mx) + uu * exp(ss * qq - mx)
      x[!flat] <- (mx + log(term)) / ss
    }
    list(x = x, seg = seg)
  }

  env <- build_env(X)
  out <- numeric(0)
  guard <- 0L
  while (length(out) < n) {
    guard <- guard + 1L
    if (guard > 100000L) stop("sampling failed to converge")
    need <- n - length(out)
    nb <- max(need * 2L, 256L)
    d <- draw_env(env, nb)
    x <- d$x; seg <- d$seg
    line_val <- env$s[seg] * x + env$b[seg] + env$M
    hx <- h(x)
    ok <- is.finite(hx) & (log(runif(nb)) <= (hx - line_val))
    acc <- x[ok]
    if (length(acc) > 0) out <- c(out, acc[is.finite(acc)])
    ## adapt: add a few rejected abscissae
    rej <- x[!ok & is.finite(hx)]
    if (length(rej) > 0 && length(X) < max_points) {
      add <- unique(round(rej[seq_len(min(4, length(rej)))], 8))
      add <- add[add > A & add < B & !(add %in% X)]
      if (length(add) > 0) {
        X <- sort(unique(c(X, add)))
        if (length(X) > max_points) X <- X[round(seq(1, length(X), length.out = max_points))]
        env <- build_env(X)
      }
    }
  }
  out[1:n]
}

## ---- table IO --------------------------------------------------------------
.app_dir <- function() {
  a <- Sys.getenv("APP")
  if (nzchar(a) && dir.exists(file.path(a, "inputs", "data"))) return(a)
  if (dir.exists(file.path(getwd(), "inputs", "data"))) return(getwd())
  if (nzchar(a)) a else "/app"
}

read_cases <- function(path) {
  read.delim(path, header = TRUE, sep = "\t", quote = "", comment.char = "",
             stringsAsFactors = FALSE, colClasses = "character",
             strip.white = FALSE, check.names = FALSE)
}

.resolve_cases <- function(cases_path, overrides_path) {
  cs <- read_cases(cases_path)
  ## dedup exact case_id: keep last occurrence
  keep <- !duplicated(cs$case_id, fromLast = TRUE)
  cs <- cs[keep, , drop = FALSE]
  if (!is.null(overrides_path) && file.exists(overrides_path)) {
    ov <- read_cases(overrides_path)
    for (i in seq_len(nrow(ov))) {
      idx <- which(cs$case_id == ov$case_id[i])
      if (length(idx) == 1) {
        for (col in c("n", "mean_tol", "sd_tol"))
          if (col %in% names(ov) && nchar(trimws(ov[[col]][i])) > 0)
            cs[[col]][idx] <- ov[[col]][i]
      }
    }
  }
  cs
}

.is_missing <- function(v) is.na(v) || nchar(trimws(as.character(v))) == 0

run_cases <- function(cases_path = file.path(.app_dir(), "inputs/data/cases.tsv"),
                      overrides_path = file.path(.app_dir(), "inputs/data/overrides.tsv"),
                      out_dir = .app_dir()) {
  cs <- .resolve_cases(cases_path, overrides_path)
  rows <- list()
  for (i in seq_len(nrow(cs))) {
    cid <- cs$case_id[i]
    fam <- cs$family[i]; par <- cs$params[i]; sup <- cs$support[i]; nn <- cs$n[i]
    status <- "ERROR"; ns <- 0L; smean <- NA_real_; ssd <- NA_real_
    ok_row <- TRUE
    if (.is_missing(fam) || .is_missing(par) || .is_missing(sup) || .is_missing(nn))
      ok_row <- FALSE
    res <- tryCatch({
      if (!ok_row) stop("missing required field")
      sp <- strsplit(sup, ",", fixed = TRUE)[[1]]
      support <- c(as.numeric(sp[1]), as.numeric(sp[2]))
      nval <- as.numeric(nn)
      ip <- cs$initial_points[i]
      init <- NULL
      if (!.is_missing(ip)) init <- as.numeric(strsplit(ip, ";", fixed = TRUE)[[1]])
      logf <- build_log_density(fam, par)
      samples <- ars(logf, nval, support, initial_points = init)
      tm <- true_moments(fam, par, support)
      sm <- mean(samples); sd_ <- sd(samples)
      mt <- as.numeric(cs$mean_tol[i]); st <- as.numeric(cs$sd_tol[i])
      pass <- abs(sm - tm$mean) <= mt && abs(sd_ - tm$sd) <= st
      list(status = if (pass) "PASS" else "FAIL", ns = length(samples),
           smean = sm, ssd = sd_, samples = samples)
    }, error = function(e) NULL)
    if (!is.null(res)) {
      status <- res$status; ns <- res$ns; smean <- res$smean; ssd <- res$ssd
      writeLines(formatC(res$samples, format = "f", digits = 10),
                 file.path(out_dir, paste0("case_", cid, "_samples.txt")))
    }
    rows[[length(rows) + 1]] <- data.frame(
      case_id = cid, status = status, n_samples = ns,
      sample_mean = smean, sample_sd = ssd,
      stringsAsFactors = FALSE)
  }
  res_tbl <- do.call(rbind, rows)
  ord <- order(res_tbl$case_id, method = "radix")
  res_tbl <- res_tbl[ord, , drop = FALSE]
  fmt <- function(v) ifelse(is.na(v), "NA", formatC(v, format = "f", digits = 6))
  con <- file(file.path(out_dir, "results.tsv"), "w")
  writeLines("case_id\tstatus\tn_samples\tsample_mean\tsample_sd", con)
  for (i in seq_len(nrow(res_tbl))) {
    writeLines(paste(res_tbl$case_id[i], res_tbl$status[i], res_tbl$n_samples[i],
                     fmt(res_tbl$sample_mean[i]), fmt(res_tbl$sample_sd[i]),
                     sep = "\t"), con)
  }
  close(con)
  invisible(res_tbl)
}

test <- function(cases_path = file.path(.app_dir(), "inputs/data/cases.tsv"),
                 overrides_path = file.path(.app_dir(), "inputs/data/overrides.tsv"),
                 out_dir = .app_dir()) {
  set.seed(20260812)
  tbl <- run_cases(cases_path, overrides_path, out_dir)
  for (i in seq_len(nrow(tbl)))
    cat(sprintf("%s: %s (mean=%s sd=%s)\n", tbl$case_id[i], tbl$status[i],
                ifelse(is.na(tbl$sample_mean[i]), "NA", formatC(tbl$sample_mean[i], digits = 4, format = "f")),
                ifelse(is.na(tbl$sample_sd[i]), "NA", formatC(tbl$sample_sd[i], digits = 4, format = "f"))))
  invisible(tbl)
}

## ---- batch entrypoint (runs only when executed, not when sourced) ----------
if (sys.nframe() == 0L) {
  test()
}
ARS_EOF
cd "$APP"
"$RSCRIPT" "$APP/ars.R"
