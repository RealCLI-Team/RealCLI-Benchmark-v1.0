#!/usr/bin/env bash
set -uo pipefail
cd /app

if [ ! -f ars.R ]; then
  echo "FAIL: /app/ars.R not found"
  exit 1
fi

Rscript --vanilla - <<'RCHK'
fail <- function(msg) { cat("FAIL:", msg, "\n"); quit(status = 1) }
ok   <- function(msg) cat("ok:", msg, "\n")
chk  <- function(cond, msg) if (isTRUE(cond)) ok(msg) else fail(msg)

## ---- independent analytic moments (do NOT use solution's true_moments) ----
imom <- function(family, p) {
  family <- tolower(trimws(family))
  if (family == "gamma") {
    r <- p[["rate"]]; if (is.null(r)) r <- 1
    list(mean = p[["shape"]] / r, sd = sqrt(p[["shape"]]) / r)
  } else if (family == "weibull") {
    k <- p[["shape"]]; l <- p[["scale"]]
    list(mean = l * gamma(1 + 1/k),
         sd = sqrt(l^2 * (gamma(1 + 2/k) - gamma(1 + 1/k)^2)))
  } else if (family == "exp") {
    list(mean = 1 / p[["rate"]], sd = 1 / p[["rate"]])
  } else if (family == "beta") {
    a <- p[["shape1"]]; b <- p[["shape2"]]
    list(mean = a / (a + b), sd = sqrt((a*b) / ((a+b)^2 * (a+b+1))))
  } else fail(paste("unknown family in checker:", family))
}
pparams <- function(s) {
  s <- trimws(s); if (nchar(s) == 0) return(list())
  parts <- trimws(strsplit(s, "[;,]")[[1]])
  parts <- parts[nchar(parts) > 0]; out <- list()
  for (kv in parts) { pp <- strsplit(kv, "=")[[1]]
    out[[trimws(pp[1])]] <- as.numeric(trimws(pp[2])) }
  out
}

## ---- 1. source ars.R and check interface -----------------------------------
e <- tryCatch({ source("ars.R"); TRUE }, error = function(e) conditionMessage(e))
chk(isTRUE(e), paste("source(ars.R) has no side effects / errors:", if (isTRUE(e)) "" else e))
for (fn in c("ars","read_cases","build_log_density","true_moments","test"))
  chk(exists(fn) && is.function(get(fn)), paste("function exists:", fn))

## ---- parse the public case table independently -----------------------------
raw <- readLines("inputs/data/cases.tsv", warn = FALSE)
raw <- raw[nchar(trimws(raw)) > 0]
hdr <- trimws(strsplit(raw[1], "\t")[[1]])
rows <- lapply(raw[-1], function(ln) {
  f <- trimws(strsplit(ln, "\t")[[1]]); length(f) <- length(hdr)
  setNames(as.list(f), hdr)
})
byid <- setNames(rows, vapply(rows, function(r) trimws(r$case_id), ""))

valid_ids   <- c("g_gamma_a","g_gamma_b","w_weib","e_exp","g_gamma_ws","b_beta")
invalid_ids <- c("inv_n0","inv_bounds","inv_nonconcave")

read_samples <- function(id) {
  fp <- file.path("out", paste0(id, "_samples.txt"))
  chk(file.exists(fp), paste("sample file exists:", id))
  v <- suppressWarnings(as.numeric(readLines(fp, warn = FALSE)))
  v
}

## ---- 2 & 3 & 8: shape, support, moments, anti-canned -----------------------
means <- list()
for (id in valid_ids) {
  r <- byid[[id]]
  p <- pparams(r$params)
  fam <- r$family
  lo <- as.numeric(r$lower); up <- as.numeric(r$upper)
  n  <- as.numeric(r$n)
  mt <- as.numeric(r$mean_tol); st <- as.numeric(r$sd_tol)
  v <- read_samples(id)
  chk(length(v) == n, paste("exactly n lines:", id, "expected", n, "got", length(v)))
  chk(all(is.finite(v)), paste("all finite:", id))
  chk(all(v >= lo & v <= up), paste("all within [lower,upper]:", id))
  chk(length(unique(v)) > 1 && var(v) > 0, paste("not canned/constant:", id))
  tm <- imom(fam, p)
  means[[id]] <- mean(v)
  chk(abs(mean(v) - tm$mean) <= mt, paste("mean within tol:", id, "|", round(mean(v),4), "vs", round(tm$mean,4)))
  chk(abs(sd(v)   - tm$sd)   <= st, paste("sd within tol:",   id, "|", round(sd(v),4),   "vs", round(tm$sd,4)))
}

## ---- 4. near-but-different consistency (rate 1.0 mean > rate 1.5 mean) ------
chk(means[["g_gamma_a"]] > means[["g_gamma_b"]] + 0.3,
    "close-parameter cases separated in the expected direction (g_gamma_a > g_gamma_b)")

## ---- 6. invalid rows: no sample file + direct ars() errors ------------------
for (id in invalid_ids)
  chk(!file.exists(file.path("out", paste0(id, "_samples.txt"))),
      paste("no sample file for invalid row:", id))

errs <- function(expr) inherits(tryCatch(force(expr), error = function(e) e), "error")
g_ok <- build_log_density("gamma", list(shape = 2, rate = 1.0))
chk(errs(ars(g_ok, 0, Inf, 0, seed = 1)),        "ars() errors on n = 0")
chk(errs(ars(g_ok, 5, 2, 10, seed = 1)),         "ars() errors on lower >= upper")
g_bad <- build_log_density("gamma", list(shape = 0.5, rate = 1.0))
chk(errs(ars(g_bad, 0, Inf, 10, seed = 1)),      "ars() errors on non-log-concave target")

## ---- 7. hidden generalization (params NOT in cases.tsv) ---------------------
gh <- build_log_density("gamma", list(shape = 4, rate = 2))
sh <- ars(gh, 0, Inf, 5000, seed = 4242)
chk(length(sh) == 5000 && all(is.finite(sh)) && all(sh >= 0), "hidden case shape/support")
tmh <- imom("gamma", list(shape = 4, rate = 2))
chk(abs(mean(sh) - tmh$mean) <= 0.15, paste("hidden case mean within tol:", round(mean(sh),4), "vs", tmh$mean))
chk(abs(sd(sh)   - tmh$sd)   <= 0.15, paste("hidden case sd within tol:",   round(sd(sh),4),   "vs", tmh$sd))

## hidden case with a scalar-only log-density callback (weibull)
g_scalar <- function(x) { if (x <= 0) return(-Inf); (1.5 - 1) * log(x) - (x / 2)^1.5 }
ss <- suppressWarnings(ars(g_scalar, 0, Inf, 5000, seed = 77))
tms <- imom("weibull", list(shape = 1.5, scale = 2))
chk(length(ss) == 5000 && all(is.finite(ss)), "scalar-callback hidden case shape")
chk(abs(mean(ss) - tms$mean) <= 0.15, paste("scalar-callback mean within tol:", round(mean(ss),4), "vs", round(tms$mean,4)))

## ---- 5. determinism: run test() twice, compare seeded file bytes ------------
test("inputs/data/cases.tsv", "out_chk_a")
test("inputs/data/cases.tsv", "out_chk_b")
fa <- readLines("out_chk_a/g_gamma_a_samples.txt")
fb <- readLines("out_chk_b/g_gamma_a_samples.txt")
chk(identical(fa, fb), "determinism: seeded case identical across runs")

cat("ALL_CHECKS_PASSED\n")
RCHK
rc=$?
if [ $rc -ne 0 ]; then
  echo "TESTS FAILED (rc=$rc)"
  exit 1
fi
echo "TESTS PASSED"
