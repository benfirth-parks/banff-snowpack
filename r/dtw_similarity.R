# DTW alignment similarity between pairs of snow profiles (sarp.snowprofile.alignment; Herla et al. 2021).
# JSON in / JSON out (CLAUDE.md: R only via r/*.R scripts).
#   Rscript r/dtw_similarity.R pairs.json out.json
# pairs.json: [{"id": ..., "ref": {"hs": cm, "layers": [{"top": cm, "gtype": "FC", "hardness": 2.0}, ...]},
#               "query": {...}}, ...]   (layers top-down or bottom-up; heights above ground)
# out.json:   [{"id": ..., "sim": 0..1 (native depth), "sim_rescaled": 0..1 (query rescaled to ref HS),
#               "error": null|msg}, ...]
# Settings are the package defaults (simType "HerlaEtAl2021", resamplingRate 0.5 cm, open end), fixed a priori.

.libPaths(c("/root/R/library", .libPaths()))
suppressMessages({
  library(jsonlite)
  library(sarp.snowprofile)
  library(sarp.snowprofile.alignment)
})

args <- commandArgs(trailingOnly = TRUE)
pairs <- fromJSON(args[1], simplifyVector = FALSE)

to_sp <- function(p) {
  ly <- p$layers
  top <- vapply(ly, function(l) as.numeric(l$top), 0)
  gt <- vapply(ly, function(l) as.character(l$gtype), "")
  hd <- vapply(ly, function(l) if (is.null(l$hardness)) NA_real_ else as.numeric(l$hardness), 0)
  o <- order(top)
  snowprofile(hs = as.numeric(p$hs), type = "manual",
              layers = snowprofileLayers(height = top[o], gtype = gt[o], hardness = hd[o], hs = as.numeric(p$hs)))
}

out <- lapply(pairs, function(x) {
  res <- list(id = x$id, sim = NA, sim_rescaled = NA, error = NA)
  tryCatch({
    ref <- to_sp(x$ref)
    qry <- to_sp(x$query)
    res$sim <- dtwSP(qry, ref, open.end = TRUE)$sim
    res$sim_rescaled <- dtwSP(qry, ref, open.end = TRUE, rescale2refHS = TRUE)$sim
  }, error = function(e) res$error <<- conditionMessage(e))
  res
})
write(toJSON(out, auto_unbox = TRUE, na = "null", digits = 6), args[2])
