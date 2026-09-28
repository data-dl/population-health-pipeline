#!/usr/bin/env Rscript
# Screening validation and scoring, written independently of the Python pipeline in R (data.table).
#
#   Rscript r/score_screenings.R <screenings file> <extract date YYYY-MM-DD> <output folder> [config folder]
#
# Reads one screening-platform file exactly as the supplier sent it and applies the file-intrinsic
# rules of config/contracts/screenings.yaml - required fields, a real date (format detected for the
# whole file), not after the extract, item responses whole numbers in range, social-needs answers
# Y/N/D, a recognised administration mode, one version of each screening - then scores what passes:
# one row per screening and instrument administered, with a total only when every item is answered.
#
# Writes rejects.csv (row, rule) and scores.csv. tests/test_r_parity.py runs this beside the Python
# pipeline on the same file and requires the two to agree row for row. Roster checks are left to the
# pipeline: they need the warehouse, not the file.

suppressPackageStartupMessages({
  library(data.table)
  library(yaml)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3) stop("usage: score_screenings.R <file> <extract date> <out dir> [config dir]")
input <- args[[1]]
extract <- as.IDate(args[[2]])
out_dir <- args[[3]]
config <- if (length(args) >= 4) args[[4]] else "config"

instruments <- read_yaml(file.path(config, "reference", "instruments.yaml"))$instruments
maps <- read_yaml(file.path(config, "reference", "code_maps.yaml"))
admin_modes <- toupper(trimws(unlist(maps$admin_mode, use.names = FALSE)))

dt <- fread(input, colClasses = "character", na.strings = "", encoding = "UTF-8")
dt[, row := .I]
trimmed <- function(x) { x <- trimws(x); x[x == ""] <- NA_character_; x }
for (col in setdiff(names(dt), "row")) set(dt, j = col, value = trimmed(dt[[col]]))

# Step 1: one date format for the whole file - whichever parses more of it (ISO wins a tie).
iso <- as.IDate(dt$screen_date, format = "%Y-%m-%d")
us <- as.IDate(dt$screen_date, format = "%m/%d/%Y")
dt[, date := if (sum(!is.na(us)) > sum(!is.na(iso))) us else iso]

# Step 2: the file-intrinsic rules, one row per failure.
rejects <- list()
fail <- function(rule, rows) if (length(rows)) rejects[[length(rejects) + 1]] <<- data.table(row = rows, rule = rule)

fail("SCR-004", dt[is.na(screening_id) | is.na(mrn) | is.na(screen_date), row])
fail("SCR-005", dt[!is.na(screen_date) & is.na(date), row])
fail("SCR-006", dt[!is.na(date) & date > extract, row])

scored <- Filter(function(i) i$kind == "scored", instruments)
domains <- Filter(function(i) i$kind == "domains", instruments)
item_cols <- unlist(lapply(scored, `[[`, "items"), use.names = FALSE)
long <- melt(dt[, c("row", item_cols), with = FALSE], id.vars = "row", variable.name = "item",
             value.name = "raw", variable.factor = FALSE)
limits <- rbindlist(lapply(scored, function(i) data.table(item = unlist(i$items), lo = i$item_min, hi = i$item_max)))
long <- limits[long, on = "item"]
long[, value := fifelse(grepl("^-?[0-9]+$", raw), suppressWarnings(as.integer(raw)), NA_integer_)]
fail("SCR-007", long[!is.na(raw) & (is.na(value) | value < lo | value > hi), unique(row)])

sdoh_cols <- unlist(lapply(domains, `[[`, "items"), use.names = FALSE)
sdoh <- melt(dt[, c("row", sdoh_cols), with = FALSE], id.vars = "row", variable.name = "item", value.name = "raw",
             variable.factor = FALSE)
allowed <- unlist(lapply(domains, `[[`, "item_values"), use.names = FALSE)
fail("SCR-008", sdoh[!is.na(raw) & !(toupper(raw) %in% allowed), unique(row)])
fail("SCR-009", dt[!is.na(admin_mode) & !(toupper(admin_mode) %in% admin_modes), row])

content <- setdiff(names(dt), c("row", "date"))
dt[, versions := uniqueN(.SD), by = screening_id, .SDcols = setdiff(content, "screening_id")]
fail("SCR-011", dt[!is.na(screening_id) & versions > 1, row])

rejected <- if (length(rejects)) unique(rbindlist(rejects)) else data.table(row = integer(), rule = character())
setorder(rejected, row, rule)

# Step 3: score what passed. Exact duplicate rows are the same screening, scored once.
keep <- unique(dt[!row %in% rejected$row], by = content)
scores <- list()
for (name in names(scored)) {
  spec <- scored[[name]]
  items <- unlist(spec$items)
  v <- long[row %in% keep$row & item %in% items, .(answered = sum(!is.na(value)), total = sum(value, na.rm = TRUE)),
            by = row]
  v <- v[answered > 0]
  bands <- rbindlist(lapply(spec$bands, as.data.table))
  v[, `:=`(complete = answered == length(items), instrument = name)]
  v[, total_score := fifelse(complete, total, NA_integer_)]
  v[, positive := fifelse(complete, total >= spec$positive_at, NA)]
  v[, band := vapply(total_score, function(t) if (is.na(t)) NA_character_ else bands[min <= t & max >= t, band][1],
                     character(1))]
  scores[[name]] <- v
}
for (name in names(domains)) {
  spec <- domains[[name]]
  items <- unlist(spec$items)
  v <- sdoh[row %in% keep$row & item %in% items,
            .(answered = sum(!is.na(raw)), needs = sum(toupper(raw) == spec$positive_value, na.rm = TRUE)), by = row]
  v <- v[answered > 0]
  v[, `:=`(complete = answered == length(items), instrument = name, positive = needs > 0, band = NA_character_)]
  v[, total_score := fifelse(complete, needs, NA_integer_)]
  scores[[name]] <- v
}
scores <- rbindlist(scores, fill = TRUE)
scores <- keep[, .(row, screening_id)][scores, on = "row"]
scores <- scores[, .(screening_id, instrument, items_answered = answered, complete, total_score, positive, band)]
setorder(scores, screening_id, instrument)

dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
fwrite(rejected, file.path(out_dir, "rejects.csv"), na = "")
fwrite(scores, file.path(out_dir, "scores.csv"), na = "")
message(sprintf("%d rows: %d rejected, %d scores", nrow(dt), uniqueN(rejected$row), nrow(scores)))
