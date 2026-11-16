# Worker for r4py.RSession. Runs under Rscript and serves requests from Python.
#
# Protocol (one request at a time):
#   Python writes the code to a file and sends "<code file>\t<result file>\n" on
#   stdin. The worker evaluates it, writes a JSON result to the result file and
#   prints a sentinel line on stdout. Files avoid any framing or escaping of the
#   code itself, and keep the result separate from anything the code prints.

r4py_esc <- function(s) {
  if (length(s) == 0L) return(character())
  s <- enc2utf8(s)
  s <- gsub("\\", "\\\\", s, fixed = TRUE)
  s <- gsub("\"", "\\\"", s, fixed = TRUE)
  s <- gsub("\n", "\\n", s, fixed = TRUE)
  s <- gsub("\r", "\\r", s, fixed = TRUE)
  s <- gsub("\t", "\\t", s, fixed = TRUE)
  if (any(grepl("[[:cntrl:]]", s))) {
    for (i in setdiff(1:31, c(9L, 10L, 13L))) {
      s <- gsub(intToUtf8(i), sprintf("\\u%04x", i), s, fixed = TRUE)
    }
  }
  paste0("\"", s, "\"")
}

r4py_str <- function(x) {
  out <- r4py_esc(ifelse(is.na(x), "", x))
  out[is.na(x)] <- "null"
  out
}

r4py_num <- function(x) {
  vapply(x, function(v) {
    if (is.nan(v)) return("NaN")
    if (is.na(v)) return("null")
    if (v == Inf) return("Infinity")
    if (v == -Inf) return("-Infinity")
    s <- sprintf("%.15g", v)
    if (as.numeric(s) != v) s <- sprintf("%.17g", v)
    # Keep doubles recognisable as floats on the Python side.
    if (!grepl("[.eE]", s)) s <- paste0(s, ".0")
    s
  }, character(1), USE.NAMES = FALSE)
}

r4py_atomic <- function(x) {
  if (is.logical(x)) return(ifelse(is.na(x), "null", ifelse(x, "true", "false")))
  if (is.integer(x)) return(ifelse(is.na(x), "null", as.character(x)))
  if (is.numeric(x)) return(r4py_num(as.double(x)))
  if (is.character(x)) return(r4py_str(x))
  stop(sprintf("cannot convert <%s> to Python", class(x)[1L]), call. = FALSE)
}

r4py_object <- function(names, items) {
  paste0("{", paste(r4py_str(names), items, sep = ":", collapse = ","), "}")
}

# arr = TRUE keeps length-one vectors as arrays (used for data frame columns).
r4py_json <- function(x, arr = FALSE) {
  if (is.null(x)) return("null")
  if (is.factor(x)) x <- as.character(x)
  if (inherits(x, c("Date", "POSIXt"))) x <- as.character(x)
  if (inherits(x, "difftime")) x <- as.numeric(x)
  if (is.data.frame(x)) {
    cols <- vapply(x, function(col) r4py_json(col, TRUE), character(1), USE.NAMES = FALSE)
    return(paste0(
      "{\"__r4py__\":\"data.frame\",\"columns\":[",
      paste(r4py_str(names(x)), collapse = ","), "],\"data\":[",
      paste(cols, collapse = ","), "],\"nrow\":", nrow(x), "}"
    ))
  }
  if (is.matrix(x) && is.atomic(x)) {
    rows <- lapply(seq_len(nrow(x)), function(i) r4py_json(x[i, ], TRUE))
    return(paste0("[", paste(unlist(rows), collapse = ","), "]"))
  }
  if (is.atomic(x)) {
    items <- r4py_atomic(x)
    nm <- names(x)
    if (length(x) > 0L && !is.null(nm) && !anyNA(nm) && all(nzchar(nm))) {
      return(r4py_object(nm, items))
    }
    if (length(x) == 1L && !arr) return(items)
    return(paste0("[", paste(items, collapse = ","), "]"))
  }
  if (is.list(x)) {
    items <- unname(vapply(x, function(v) r4py_json(v), character(1)))
    nm <- names(x)
    if (length(x) > 0L && !is.null(nm) && !anyNA(nm) && all(nzchar(nm))) {
      return(r4py_object(nm, items))
    }
    return(paste0("[", paste(items, collapse = ","), "]"))
  }
  stop(sprintf("cannot convert <%s> to Python", paste(class(x), collapse = "/")), call. = FALSE)
}

r4py_handle <- function(request) {
  warnings_seen <- character()
  messages_seen <- character()
  value <- NULL
  failure <- NULL
  printed <- utils::capture.output({
    withCallingHandlers(
      tryCatch({
        exprs <- parse(file = request, encoding = "UTF-8", keep.source = FALSE)
        for (expr in exprs) value <- eval(expr, globalenv())
      }, error = function(e) failure <<- conditionMessage(e)),
      warning = function(w) {
        warnings_seen <<- c(warnings_seen, conditionMessage(w))
        invokeRestart("muffleWarning")
      },
      message = function(m) {
        messages_seen <<- c(messages_seen, conditionMessage(m))
        invokeRestart("muffleMessage")
      }
    )
  })
  encoded <- "null"
  if (is.null(failure)) {
    failure <- tryCatch({
      encoded <- r4py_json(value)
      NULL
    }, error = function(e) conditionMessage(e))
  }
  paste0(
    "{\"ok\":", if (is.null(failure)) "true" else "false",
    ",\"error\":", if (is.null(failure)) "null" else r4py_esc(failure),
    ",\"value\":", if (is.null(failure)) encoded else "null",
    ",\"stdout\":", r4py_esc(paste(printed, collapse = "\n")),
    ",\"stderr\":", r4py_esc(paste(messages_seen, collapse = "")),
    ",\"warnings\":[", paste(r4py_esc(warnings_seen), collapse = ","), "]}"
  )
}

r4py_serve <- function() {
  con <- file("stdin")
  open(con)
  cat("<<r4py-ready>>\n")
  flush(stdout())
  repeat {
    line <- readLines(con, n = 1L)
    if (length(line) == 0L) break
    parts <- strsplit(line, "\t", fixed = TRUE)[[1L]]
    result <- r4py_handle(parts[[1L]])
    out <- file(parts[[2L]], "wb")
    writeLines(enc2utf8(result), out, useBytes = TRUE)
    close(out)
    cat("<<r4py-done>>\n")
    flush(stdout())
  }
}

r4py_serve()
