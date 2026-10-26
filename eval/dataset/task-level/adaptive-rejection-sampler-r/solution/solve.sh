#!/usr/bin/env bash
set -euo pipefail
cd /app

cat > ars.R <<'RSCRIPT'
## Adaptive Rejection Sampling (Gilks & Wild, 1992) for univariate log-concave
## targets on [lower, upper] (bounds may be finite or +/-Inf).

.ars_eval_logf <- function(g, x) {
  ## Support both vectorized and scalar-only log-density callbacks.
  out <- tryCatch(g(x), error = function(e) NULL)
  if (is.null(out) || length(out) != length(x)) {
    out <- vapply(x, function(xi) as.numeric(g(xi)), numeric(1))
  }
  as.numeric(out)
}

.ars_deriv <- function(g, x, h = 1e-5) {
  (.ars_eval_logf(g, x + h) - .ars_eval_logf(g, x - h)) / (2 * h)
}

build_log_density <- function(family, params) {
  family <- tolower(trimws(family))
  p <- params
  if (family == "gamma") {
    shape <- p[["shape"]]; rate <- p[["rate"]]; if (is.null(rate)) rate <- 1
    function(x) ifelse(x > 0, (shape - 1) * log(x) - rate * x, -Inf)
  } else if (family == "weibull") {
    shape <- p[["shape"]]; scale <- p[["scale"]]
    function(x) ifelse(x > 0, (shape - 1) * log(x) - (x / scale)^shape, -Inf)
  } else if (family == "exp") {
    rate <- p[["rate"]]
    function(x) ifelse(x > 0, -rate * x, -Inf)
  } else if (family == "beta") {
    a <- p[["shape1"]]; b <- p[["shape2"]]
    if (is.null(a)) a <- p[["a"]]; if (is.null(b)) b <- p[["b"]]
    function(x) ifelse(x > 0 & x < 1, (a - 1) * log(x) + (b - 1) * log(1 - x), -Inf)
  } else {
    stop(sprintf("unknown family: %s", family))
  }
}

true_moments <- function(family, params) {
  family <- tolower(trimws(family))
  p <- params
  if (family == "gamma") {
    shape <- p[["shape"]]; rate <- p[["rate"]]; if (is.null(rate)) rate <- 1
    list(mean = shape / rate, sd = sqrt(shape) / rate)
  } else if (family == "weibull") {
    shape <- p[["shape"]]; scale <- p[["scale"]]
    m <- scale * gamma(1 + 1 / shape)
    v <- scale^2 * (gamma(1 + 2 / shape) - gamma(1 + 1 / shape)^2)
    list(mean = m, sd = sqrt(v))
  } else if (family == "exp") {
    rate <- p[["rate"]]
    list(mean = 1 / rate, sd = 1 / rate)
  } else if (family == "beta") {
    a <- p[["shape1"]]; b <- p[["shape2"]]
    if (is.null(a)) a <- p[["a"]]; if (is.null(b)) b <- p[["b"]]
    m <- a / (a + b)
    v <- (a * b) / ((a + b)^2 * (a + b + 1))
    list(mean = m, sd = sqrt(v))
  } else {
    stop(sprintf("unknown family: %s", family))
  }
}

.ars_init_abscissae <- function(h, hp, lower, upper) {
  finite_lo <- is.finite(lower); finite_hi <- is.finite(upper)
  if (finite_lo && finite_hi) {
    pts <- lower + c(0.25, 0.5, 0.75) * (upper - lower)
  } else if (finite_lo && !finite_hi) {
    pts <- lower + c(0.5, 1, 2, 4)
  } else if (!finite_lo && finite_hi) {
    pts <- upper - c(4, 2, 1, 0.5)
  } else {
    pts <- c(-1, 0, 1)
  }
  pts <- sort(unique(pts[is.finite(h(pts))]))
  if (!finite_hi) {
    step <- 1
    while (hp(pts[length(pts)]) >= 0 && step < 1e6) {
      step <- step * 2; pts <- c(pts, pts[length(pts)] + step)
    }
  }
  if (!finite_lo) {
    step <- 1
    while (hp(pts[1]) <= 0 && step < 1e6) {
      step <- step * 2; pts <- c(pts[1] - step, pts)
    }
  }
  sort(unique(pts[is.finite(h(pts))]))
}

.ars_hull <- function(x, hx, hpx, lower, upper) {
  k <- length(x); z <- numeric(max(k - 1, 0))
  if (k >= 2) for (j in 1:(k - 1)) {
    denom <- hpx[j] - hpx[j + 1]
    if (abs(denom) < 1e-12) z[j] <- (x[j] + x[j + 1]) / 2
    else z[j] <- (hx[j + 1] - hx[j] - x[j + 1] * hpx[j + 1] + x[j] * hpx[j]) / denom
  }
  list(z = c(lower, z, upper))
}

.ars_segment_areas <- function(x, hx, hpx, zfull, offset) {
  k <- length(x); areas <- numeric(k)
  for (j in 1:k) {
    a <- zfull[j]; b <- zfull[j + 1]; m <- hpx[j]
    intercept <- hx[j] - m * x[j] - offset
    if (abs(m) < 1e-10) areas[j] <- exp(intercept) * (b - a)
    else areas[j] <- exp(intercept) * (exp(m * b) - exp(m * a)) / m
  }
  areas
}

.ars_sample_one <- function(x, hx, hpx, zfull, areas, offset) {
  k <- length(x); probs <- areas / sum(areas)
  j <- sample.int(k, 1, prob = probs)
  a <- zfull[j]; b <- zfull[j + 1]; m <- hpx[j]
  u <- runif(1)
  if (abs(m) < 1e-10) xt <- a + u * (b - a)
  else { ea <- exp(m * a); eb <- exp(m * b); xt <- log(ea + u * (eb - ea)) / m }
  list(x = xt, uval = hx[j] + m * (xt - x[j]))
}

.ars_lower_hull <- function(xt, x, hx) {
  k <- length(x)
  if (xt < x[1] || xt > x[k]) return(-Inf)
  j <- findInterval(xt, x); if (j < 1) j <- 1; if (j >= k) j <- k - 1
  x0 <- x[j]; x1 <- x[j + 1]
  ((x1 - xt) * hx[j] + (xt - x0) * hx[j + 1]) / (x1 - x0)
}

ars <- function(g, lower, upper, n, seed = NULL, ...) {
  if (!is.function(g)) stop("density callback g must be a function")
  if (length(n) != 1 || !is.finite(n) || n <= 0 || n != as.integer(n))
    stop("n must be a positive integer")
  if (!(length(lower) == 1 && length(upper) == 1))
    stop("lower and upper must be scalars")
  if (!(lower < upper)) stop("require lower < upper")
  if (!is.null(seed)) set.seed(seed)

  h  <- function(x) .ars_eval_logf(g, x)
  hp <- function(x) .ars_deriv(g, x)
  x <- .ars_init_abscissae(h, hp, lower, upper)
  if (length(x) < 2) stop("could not initialize abscissae for sampler")
  hx <- h(x); hpx <- hp(x)
  if (any(!is.finite(hx))) stop("log-density not finite at initial abscissae")
  if (any(diff(hpx) > 1e-4))
    stop("target is not log-concave (log-density derivative is increasing)")

  out <- numeric(0); guard <- 0L
  while (length(out) < n) {
    guard <- guard + 1L
    if (guard > 300000L) stop("sampler failed to converge")
    offset <- max(hx)
    zfull <- .ars_hull(x, hx, hpx, lower, upper)$z
    areas <- .ars_segment_areas(x, hx, hpx, zfull, offset)
    if (any(!is.finite(areas)) || sum(areas) <= 0)
      stop("envelope has non-finite area (support may be unbounded for this target)")
    s <- .ars_sample_one(x, hx, hpx, zfull, areas, offset)
    xt <- s$x
    if (!is.finite(xt) || xt < lower || xt > upper) next
    w <- runif(1)
    lval <- .ars_lower_hull(xt, x, hx)
    if (log(w) <= lval - s$uval) { out <- c(out, xt); next }
    hxt <- h(xt)
    if (is.finite(hxt) && log(w) <= hxt - s$uval) out <- c(out, xt)
    if (is.finite(hxt) && (xt < x[1] || xt > x[length(x)] || !(xt %in% x))) {
      hpxt <- hp(xt); idx <- findInterval(xt, x)
      x <- append(x, xt, after = idx); hx <- append(hx, hxt, after = idx)
      hpx <- append(hpx, hpxt, after = idx)
      if (any(diff(hpx) > 1e-3))
        stop("target is not log-concave (detected during adaptation)")
      if (length(x) > 400) {
        keep <- unique(round(seq(1, length(x), length.out = 200)))
        x <- x[keep]; hx <- hx[keep]; hpx <- hpx[keep]
      }
    }
  }
  out[1:n]
}

.parse_params <- function(s) {
  s <- trimws(s); if (nchar(s) == 0) return(list())
  parts <- strsplit(s, "[;,]")[[1]]; parts <- trimws(parts[nchar(trimws(parts)) > 0])
  out <- list()
  for (kv in parts) {
    pp <- strsplit(kv, "=")[[1]]
    out[[trimws(pp[1])]] <- as.numeric(trimws(pp[2]))
  }
  out
}

read_cases <- function(path) {
  raw <- readLines(path, warn = FALSE)
  raw <- raw[nchar(trimws(raw)) > 0]
  if (length(raw) == 0) return(data.frame())
  header <- trimws(strsplit(raw[1], "\t")[[1]])
  recs <- lapply(raw[-1], function(ln) {
    f <- trimws(strsplit(ln, "\t")[[1]]); length(f) <- length(header)
    setNames(as.list(f), header)
  })
  do.call(rbind, lapply(recs, function(r) as.data.frame(r, stringsAsFactors = FALSE)))
}

test <- function(cases_path, out_dir) {
  if (!dir.exists(out_dir)) dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  cases <- read_cases(cases_path)
  if (nrow(cases) == 0) return(invisible(NULL))
  for (i in seq_len(nrow(cases))) {
    row <- cases[i, ]
    cid <- trimws(as.character(row$case_id))
    family <- trimws(as.character(row$family))
    res <- tryCatch({
      params <- .parse_params(as.character(row$params))
      lower <- as.numeric(row$lower); upper <- as.numeric(row$upper)
      n <- suppressWarnings(as.numeric(row$n))
      seed <- suppressWarnings(as.numeric(row$seed))
      mean_tol <- as.numeric(row$mean_tol); sd_tol <- as.numeric(row$sd_tol)
      g <- build_log_density(family, params)
      samples <- ars(g, lower, upper, n, seed = seed)
      writeLines(format(samples, scientific = FALSE, trim = TRUE),
                 file.path(out_dir, paste0(cid, "_samples.txt")))
      tm <- true_moments(family, params)
      smean <- mean(samples); ssd <- sd(samples)
      pass <- abs(smean - tm$mean) <= mean_tol && abs(ssd - tm$sd) <= sd_tol
      list(status = if (pass) "PASS" else "FAIL", mean = smean, sd = ssd)
    }, error = function(e) list(status = "FAIL", err = conditionMessage(e)))
    if (!is.null(res$err)) cat(sprintf("%s: FAIL (error: %s)\n", cid, res$err))
    else cat(sprintf("%s: %s mean=%.4f sd=%.4f\n", cid, res$status, res$mean, res$sd))
  }
  invisible(NULL)
}
RSCRIPT

Rscript -e 'source("ars.R"); test("inputs/data/cases.tsv","out")'
