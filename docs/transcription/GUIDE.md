# Snow-profile chart transcription guide (schema `transcription-1`)

Purpose: turn rendered snow-profile charts (PDF/PNG/JPG exports) into structured layers for
TRAINING, CALIBRATION and EVALUATION. A transcription is derived data with reading error. It is
always labelled with its method and `reviewed: false` until a person checks it.

## Golden rules
1. **Never invent.** If a value is not legible, set it to `null` and add the field name to that
   layer's `uncertain_fields` (e.g. `["hardness", "grain_size_mm"]`). Do not interpolate or "fix".
2. Transcribe what the chart shows, not what you think is physically likely.
3. Record the symbol as seen (`grain_symbol_as_seen`, e.g. `"□"`, `"●(□)"`, `"∧ with bar"`) and
   the translated code (`grain_form`, `grain_form_2`).
4. Layers are listed **top (surface) to bottom**. Use `height_above_ground` (cm) unless the chart
   is clearly labelled as depth from surface.
5. One JSON file per record, exactly the schema below. Validate it with
   `.venv/bin/python -m snowagent.obs.transcribe_cli validate <file>` and fix schema errors only.
6. If the image is not a snow profile (photo, markup, unrelated), set `is_snow_profile: false`,
   `readable: false`, `layers: []` and explain in `unreadable_reason`.

## Grain-form symbols (IACS 2009 / CAA OGRS) -> code
Visual key: `docs/transcription/iacs_symbol_key.png` (icons from snowpyt, MIT licence).

| Symbol | Code | Notes |
|---|---|---|
| `+` | PP | precipitation particles; `+r` / subscript r = rimed: use PP (or DF for `/r`) and comment "rimed" |
| `✱` / `∗` asterisk | PPsd | stellar dendrites |
| hexagon ⬡ | PPpl | plates |
| ─ (short bar, columns) | PPco | only if clearly PP context |
| `↔` double-headed arrow / `⊸` needle | PPnd | needles |
| filled hexagon/`✳` in circle | PPgp | graupel ("Graupel" often written) |
| ▲ filled triangle | PPhl | hail |
| `/` single slash | DF | decomposing/fragmented |
| broken slash `⁄ ⁄` | DFbk | wind-broken |
| `●` filled circle | RG | rounded grains; small `•` RGsr |
| `●` with slash through | RGwp | wind packed |
| filled half-circle under a square frame | RGxf | faceted rounded |
| `□` open square | FC | faceted crystals |
| `□` with diagonal | FCsf | near-surface faceted |
| square with rounded top (dome on a box) | FCxr | rounding faceted |
| `∧` / `^` caret | DH | depth hoar (hollow cups) |
| `⊓` open box upside down | DHpr | hollow prisms |
| `∧` with a branch | DHch | chains |
| rounded `∩` arch | DHxr | rounding depth hoar |
| `∨` V | SH | surface hoar |
| `∨` with arc above | SHcv | cavity/crevasse hoar |
| rounded `∪` | SHxr | rounding surface hoar |
| `○` open circle / three linked circles | MF / MFcl | melt forms, clustered |
| three circles joined (filled core) | MFpc | rounded polycrystals |
| three separate circles | MFsl | slush |
| two linked circles `◎◯` / `∞`-circles / `⊚⊚` | MFcr | melt-freeze crust |
| filled bar `▬` | IF / IFil | ice layer |
| vertical filled bar | IFic | ice column |
| `=` two horizontal lines | IFrc | rain crust (OGRS/IACS "=") |
| sun-crust mark or "sun crust" written | IFsc | |

Secondary form in parentheses, e.g. `●(□)` -> `grain_form: "RG", grain_form_2: "FC"`;
`/(□)` -> DF with FC; `∧(□)` -> DH with FC. Two symbols without parentheses: first is primary.
If a symbol does not match the table, write it in `grain_symbol_as_seen`, set `grain_form: null` and
add `"grain_form"` to `uncertain_fields`.

## Other fields
- **Grain size (mm):** `"0.5"` -> `[0.5]`; `"1-2"`, `"1.0-2.0"` -> `[1.0, 2.0]`; `".25-1"` -> `[0.25, 1.0]`.
- **Hardness:** read from the bar's extent on the hand-hardness axis (F, 4F, 1F, P, K, I) using
  `+`/`-` only when the bar clearly sits between classes (e.g. between 4F and 1F nearer 4F -> `4F+`).
  Slanted bars (gradient): `hardness` = at layer top, `hardness_bottom` = at layer bottom.
- **Moisture:** D, M, W, V, S as written (dry, moist, wet, very wet, soaked).
- **Temperatures:** each plotted point as `{"height_cm", "t_c"}`; read from the temperature axis.
- **Tests:** keep the literal text in `raw` (e.g. `"CT13SP on ∨ 4.0-6.0 Jan 3"`, `"ECTX"`,
  `"CT11 SC below MFC on FC's sz 2"`) plus parsed `type`, `result`, `score`, `fracture_character`,
  `height_cm` (where the test arrow/line points), `layer_date_tag`.
- **date_tag:** layer names/labels written beside layers ("Jan 3", "Nov 13").
- **Header:** copy what is printed: site name, date (YYYY-MM-DD), time (HH:MM, local as printed),
  lat/lon (decimal), elevation in m (convert ft x 0.3048 and say so in notes), aspect, slope, HS,
  pit depth if partial, air temp, sky, precipitation, wind, foot/ski penetration, notes.
- **confidence:** `high` = printed numbers for boundaries and clear symbols; `medium` = boundaries read
  from an axis (+-2 cm) or a few uncertain symbols; `low` = blurry/partial/many uncertainties.

## Format notes
- **Avanet (2017-19):** boundary heights printed on the right axis; grain type, size, water content
  in columns; hardness bars grow leftward from F (right) toward I (left); temperature axis at top
  (0 C at right). Tests are boxes on the left, attached by a dashed line to a height. The hatched top
  row is "surface" (surface grains above HS) - record as `header.notes` ("surface: ∨ 10-15 mm"), not a layer.
- **SnowPilot (2019-23):** header block at top (name, observer, date DD/MM/YYYY - convert!, HS, PF, PS,
  co-ords, elevation). Height axis in the middle (cm above ground, ticks every 10 cm); layer
  boundaries are the horizontal lines in the Form/Size/Moisture table and bar edges; hardness bars grow
  leftward from the height axis (F nearest). Tests are arrows on the right ("CT3, SC @28cm").
- **Propagation Labs (2023-26):** header in the PDF text (also printed), chart with Name/Grain Form/
  Grain Size columns left, height axis (cm) and hardness bars growing rightward (F, 4f, 1f, P, K),
  temperature points on the top axis (0 to -10 C). Tests are yellow boxes with a line to a height.
  Hardness bars may grow in EITHER direction and the vertical axis may be "Height (cm)" or "Depth (cm)".

## Rules added after the pilot (apply to all formats)
- **Hardness axis direction varies** (even within one app version). Always read the axis labels
  (F, 4F, 1F, P, K, I) and measure the bar end against them; never assume left/right.
- **Numeric hand-hardness index** (niViz and others; 1=F, 2=4F, 3=1F, 4=P, 5=K, 6=I):
  n.0 -> class n; n.33 -> class n `+`; n.67 -> class n+1 `-`; n.5 -> class n `+` AND add
  `"hardness"` to `uncertain_fields`.
- **Temperature drawn as a line without point markers:** record the line's break points (or 10 cm
  samples if smooth) and say so in `transcriber_notes`.
- **Surface row above HS** (hatched/grey row in Avanet, top row above HS in SnowPilot): record in
  `header.notes` (e.g. "surface: / 1 mm"), never as a layer.
- **Blank vs illegible:** a cell that is blank in the source -> null, NOT listed in `uncertain_fields`,
  and add "blank in source: <fields>" to the layer `comment`. Illegible -> null AND listed.
- **Two density values in one layer:** `density_kg_m3` = their mean; put both values in `comment`.
- **Unmapped glyphs:** e.g. `∀` (V with crossbar): grain_form null + uncertain. Three joined circles
  whose exact subclass is unclear: use the class `MF` (not a guessed subclass).
- **Dates:** the date printed on the chart goes in `header.date_local` and wins over the file name; do
  not change `record_id`. Mention a disagreement in `transcriber_notes`.
- **Layer comments/notes columns** ("Dec 31 V decomposed", "Problematic layer") go in the layer
  `comment`; date labels ("Dec 31", "Nov 13 crust") also go in `date_tag`.
- **niViz** saved pages: use `source_format: "niviz"`. If the symbol font is missing (letters such as
  `e(d)` in the form column), do not decode the letters: grain_form null + uncertain.

## Rules added after wave 1
- **Depth charts** ("Depth (cm)", 0 at top): keep depths as shown and set
  `height_reference: "depth_from_surface"`. Every vertical position in the record (layers, tests,
  temperatures; the fields are still named `*_cm`/`height_cm`) then means depth below the surface.
  Do not convert; conversion to height above ground (HS - depth) happens downstream.
- **Boundaries come from the chart, not the table:** apps draw thin layers taller in the table than
  on the axis; bar edges and leader lines decide the heights.
- **Single open circle ○ = MF** (IACS class symbol). Do not upgrade to MFcr because a layer is hard.
- **Secondary-form size** in parentheses ("1-2(0.5)", "2.0(3.0)") -> `grain_size_2_mm`.
- **Shear quality** (Q1/Q2/Q3, e.g. "CT5Q1") -> `shear_quality`; `fracture_character` only for
  SP/SC/RP/PC/BRK (or as written in notes, e.g. "sudden collapse" -> SC).
- **"-"/"+" at the soft end** (bar stops ~1/3 class short of F) -> `F-` is intended.
- **Hardness-only charts** (no grain table): grain fields null, comment "blank in source: grain";
  confidence reflects boundary/hardness quality.
- **SnowPilot/Avanet PF/PS** = foot penetration / ski penetration (cm).
- More unmapped glyphs (null + uncertain): `∀`; arch with a crossbar and no box beneath.
- Missing HS field: `hs_cm` null (do not take the axis top). Test with no leader line: `height_cm` null.
- More unmapped glyphs: asterisk over a triangle (SnowPilot; possibly graupel/rime) - null + uncertain,
  describe it in `grain_symbol_as_seen`.
- **Bar reaching the plot frame** beyond the last labelled class: record the class at the frame and add
  `"hardness"` to `uncertain_fields` (possibly clipped).
- `profile_depth_cm` = observed pit depth measured from the surface (HS minus pit-bottom height).
- **Glyph size is not a subclass:** SnowPilot draws secondary forms smaller; a small `•` or `□` in
  parentheses is the class (RG, FC), not RGsr/FCso. Use subclasses only when the glyph shape differs.
- Hardness-only charts are kept (useful for HS, boundaries and hardness); they are not discarded.
  A chart with no grain columns at all: grain fields null, NOT uncertain, comment
  "chart has no grain columns".
- **Grain info inside test boxes** ("ECTP23 on □ 2.0-3.0") stays in that test (`raw`/`comment`);
  never copy it into a layer.
- **Coloured highlights** (red bars/lines marking problem layers): add "highlighted red" to the layer
  `comment`.
- **Bar end hidden under a test box:** give the best reading and add `"hardness"` to `uncertain_fields`.
- **No-fracture tests without a score** (CTN, ECTX): `height_cm` null even if the box is drawn at 0 cm.
  Scored results that fractured (e.g. ECTN12, CTM14) keep the height of their drawn line.
- More unmapped glyphs: short thick bar "–" in the form column (could be IF or PPco).
- Subclass calls (RGsr vs RG, PPsd vs PPgp) are informational; evaluation uses the 2-letter class.
  Never copy a form from test text into a layer. Fracture character written only in Notes may be used;
  the drawn test line decides the height.
- Irregular/compressed height axes: read against the nearest printed labels (local scale) and say so.

## Rules added during wave 2
- Moisture written as a range ("D-M") or size that cannot be parsed ("-1"): null, list the field in
  `uncertain_fields`, quote the literal text in `comment`.
- "Didn't dig to ground" / "didn't dig below X": transcribe the bars as drawn (apps draw to 0 cm) but put
  the note text in the lowest layer's `comment` and set `profile_depth_cm` = HS - X when X is given.
  Downstream processing trims unobserved layers; do not trim yourself.
- Grain information that appears only in layer notes stays in `comment` (like test text); never infer
  `grain_form` from notes.
- `date_tag` holds any layer name/label as written, including non-date names ("xmas facets").
- CTV (fractured while isolating): `result` "CTV", `score` null.
- Companion pit photos: `source_format` "other", non-profile, header null; name the matching chart
  record in `transcriber_notes`.
- A size printed only in parentheses with no secondary form ("/ (0.5)"): put it in `grain_size_mm`,
  add `"grain_size_mm"` to `uncertain_fields`, quote it in `comment`.
- The glyph-size rule applies to primary forms too: a small `•` is RG unless its shape differs.
- Several results in one test text ("CTM 14 and 17"): one `tests` entry per result, each keeping the full
  `raw` text.
- regObs-style results with a letter (CTM20, CTH24): `result` = literal text, `score` = the number.
- An explicit ground marker (regObs "GND", SnowPilot ground line with a value) IS the HS; set `hs_cm`.
  (Only the bare top of an axis is not HS.)
- A date label naming an interface ("Jan 4 layer at 88"): put `date_tag` on the layer whose top is at that
  height (the buried surface) and write "interface" in its `comment`.
- Multi-page documents: if the first pages are not a snow profile, check the text layer for profile terms
  before declaring the file non-profile.
- A bar end visible through a semi-transparent test box is read normally (not uncertain).
- PST outcome words (End/SF/Arr): keep the literal text in `result`, e.g. "PST85/100 (End)".
- Sideways/rotated images: read them rotated upright and say so in `transcriber_notes`.
- Only a secondary form in parentheses with an empty primary slot ("(⍝)"): `grain_form` null + uncertain,
  `grain_form_2` = the form.
- A layer thinner than the chart can resolve: `bottom_cm` null + uncertain (do not invent thickness).
- Tests reported at another location (e.g. "nearby, where HS was 90 cm...") go in `header.notes`, not `tests`.
- Phone screenshots of niViz/regObs-style charts without visible branding: `source_format` "other".
- Unclear subclass for ANY class (e.g. flat-apex caret DH vs DHxr, MFcl vs MFpc): record the class.
- Sub-centimetre boundaries: round to the nearest 0.5 cm; keep measured values in `comment` if useful.
- PDFs that wrap a raster chart: measure on the embedded image (sharper than the page render).
- Text overlays in a PDF that contradict the chart's own labels: record both (overlay in the field,
  underlying text in `comment`) and add the field to `uncertain_fields`.
- A boundary hidden under a test line: best reading + list `top_cm`/`bottom_cm` in `uncertain_fields`.
- A drawn axis break (zigzag) with bars stopping above 0 cm marks the pit bottom: `profile_depth_cm` =
  HS - break height.
- "No Hardness specified" printed on a layer: blank in source (null, not uncertain).
- Aspect printed in degrees: keep as written ("315°").
- PST: `result` literal ("PST37/100 (End)"), `score` = cut length.
- Notes the app attaches to a layer range stay on that layer; the interface rule applies to free text.
  An interface named at the pit bottom (no observed layer below) stays in `comment` only.
- SnowPilot "No Hardness specified" bands are not red highlights. A red problem line belongs to the layer
  named in the Layer Notes, whichever boundary it is drawn on.
- Descriptive notes without a date ("larger crystals") go in `comment`, not `date_tag`.
- Downstream normalisation (no need to re-transcribe): RG vs RGsr and PPsd vs PPgp are evaluated at class
  level; a parenthesised-only size without a secondary form is treated as the primary size (uncertain);
  an unmapped `↔` symbol is mapped to PPnd (flagged as rule-derived).
- Confidence: clean digital charts measured to +-0.5 cm with legible symbols are `high`; `medium` is for
  low resolution, interpolated axes or several uncertain fields.
- Printed date partly hidden (e.g. SnowPilot region text over the day): use the legible digits; fill the
  rest only from a consistent source (file name, another chart of the same pit) and explain it in
  `transcriber_notes`. If the legible digits contradict the file name, the printed digits win.
- No-fracture tests (CTN, DTN, ECTX) keep `height_cm` null even when a height is printed ("DTN @80cm");
  the printed text stays in `raw`. A fracture without a score ("DT, PC @30cm") keeps its height, with
  `result` "DT" and `score` null.
- A repeated identical result written once with a count ("2x CTN", "x 2"): one entry, count kept in `raw`.
- Size ranges with equal ends ("1-1"): keep both values as written ([1.0, 1.0]).
- Solid filled rectangle in the form column -> IF; hollow rectangle -> PPco; a thin one-line dash stays
  unmapped (null + uncertain).
- Thin layers: when the table prints boundary heights, the printed values win over drawn bar edges.
- Site name, coordinates or elevation printed on the chart that conflict with the folder or look wrong:
  transcribe as printed and describe the conflict in `transcriber_notes` (QC flags it downstream).
- A render that is black or missing text: the source is probably a transparent PNG; open the source file
  and read it composited onto white (renders are now made that way) and say so in `transcriber_notes`.
- Temperatures drawn but their axis is cropped away: record no temperatures (never assume an app's
  default axis); mention the line in `transcriber_notes`.
- Chart cut off by the page/image crop (not by the pit): lowest `bottom_cm` null + uncertain, write
  "source cropped" in `transcriber_notes`, confidence at most `medium`. The partial-pit flag is expected.
- No date printed anywhere: `date_local` null even if the file name has one (the pipeline falls back to
  the file-name date and flags it).
- niViz comment cells spanning several layers: the comment belongs to the layer where its text starts.
- niViz linked-circles glyph with a ring on the left and a caret on the right (differs from niViz's MFcr
  glyph): unmapped, null + uncertain.
- A niViz red line on a "Grain size [mm]" axis is a grain-size profile, not temperature: record no
  temperatures from it.
- niViz threshold-sum ("lemons") columns (E, R, F, dE, dR, Depth) with asterisks: optional; if recorded,
  put the marked ones in the layer `comment` (e.g. "lemons: E*, R*").
- Temperature axes in deg F (Avanet): convert to deg C (1 decimal), calibrate on labelled ticks (not label
  centres) and say so in `transcriber_notes`; convert a printed air temperature the same way.
- Printed pit depth equal to HS but layers stop above ground: `profile_depth_cm` = HS - lowest boundary,
  quote the printed value in `transcriber_notes`.
- Printed HS above the first drawn bar (empty top table row): keep the drawn layers and the printed HS;
  the top is unobserved (the validator flag is expected). Never invent a layer to fill it.
- Comment cells: the comment belongs to the layer at the top of the cell (where its text starts), whether
  the cells follow table rows or chart heights.
- deg F axes whose label spacing disagrees with the tick marks: the tick marks win; note the disagreement
  and set confidence to `medium` if it exceeds 0.3 deg C.
- Avanet red highlight lines (no Layer Notes column): the layer whose bar edge the line touches gets
  "highlighted red" in `comment`.
- Avanet left-side boxes with a date and no test code ("Nov 27"): layer labels, apply the interface rule.
- Handwritten field-book forms: `hs_cm` only if HS is written as such; a grain entry on the top boundary row
  without hardness is a surface description (header notes); a last layer without a written bottom gets
  `bottom_cm` null + uncertain.
- Count plus verbal result ("2 Compression Tests NO RESULT"): one entry (count stays in `raw`), `result`
  literal ("NO RESULT"), height null.
- Avanet: a thin layer drawn entirely in red is read like any bar (hardness from the red bar's width); say
  which edge a red line runs along in `comment`.
- Avanet density values are printed at measurement heights: give each to the layer containing that height;
  a value exactly on a boundary goes to the layer above with `density_kg_m3` uncertain.
- Avanet temperature axis: labels are offset from their ticks; 0 deg C is the right edge of the hardness
  area. Calibrate on that and on the printed air temperature, never on label centres. A positive air
  temperature is not drawn.
- Unbranded screenshots of any app's chart: `source_format` "other".
- Two bold test entries in one box ("CT11 SC ..." / "CT13 SC"): two `tests` entries, each with its own `raw`,
  sharing the leader height.
- Descriptive labels naming a range inside a drawn layer ("Knife hard Crust 0-3cm") stay in that layer's
  `comment`; do not split the layer.
- Avanet height axes start at the PIT BOTTOM (top labelled "<snowpit depth> SURFACE"). Transcribe heights as
  drawn; set `hs_cm` = printed snowpack depth (null if "--") and `profile_depth_cm` = printed snowpit depth.
  The pipeline converts (shift by HS - pit depth, or depths below the surface when HS is unknown).
- Test lines that look like depths entered on a height axis: record as drawn and write "possible
  depth/height mix-up" in that test's `comment`.
- Free-text test boxes without a result code ("No results with compression test"): one entry, `type` "CT",
  `result` = the literal text, `height_cm` null.
- Red highlight between two bars of equal width: comment on the layer above, naming the boundary height.
- The end of Avanet's dashed air-temperature line is the air temperature (`header.air_temp_c`), not a snow
  temperature.
- niViz charts that colour bars by grain type (e.g. red MFcr, green DF): the colours are not highlights.
- A test leader pointing inside a thin layer (not at a boundary): assign to the layer containing that height.
- A fractured test whose leader ends at the 0 cm plot corner with no line into the plot: `height_cm` null.
- Do not snap temperatures to 0.5 deg C steps; record the measured value (0.1 deg C).
- Avanet temperature calibration (restated, it biases values by 0.1-0.5 deg C): 0 deg C is the bar-origin
  (right) edge of the plot, NOT the "0 deg C" label; fix the scale with the dashed air-temperature segment
  against the printed air temperature.
- Avanet labels/red lines naming a layer that exists in the grain column go on that layer; a label naming
  a layer that is not drawn (e.g. SH with no SH row) follows the interface rule.
- Descending size text ("2-1", "1.5-.5"): null + uncertain, literal in `comment`.
- Letter result with a number in parentheses ("CTE (2)", "CTM (23)"): `result` literal, `score` = the number.
- A date line FIRST in a test box ("DEC 15" above the result) is the layer's `date_tag`; after the result it
  stays in the test `raw` only.

## JSON template
```json
{
  "schema_version": "transcription-1",
  "record_id": "<from task>", "source_file": "<from task>", "source_sha256": "<from task>",
  "source_format": "propagation_labs",
  "transcriber": {"method": "vision_model", "agent": "<agent id>", "reviewed": false, "reviewer": null},
  "readable": true, "unreadable_reason": null, "is_snow_profile": true,
  "header": {"site_name_as_written": "Goats Eye", "date_local": "2026-01-12", "time_local": "14:28",
             "lat": 51.089, "lon": -115.7504, "elevation_m": 2282, "aspect": null, "slope_deg": null,
             "hs_cm": 149, "profile_depth_cm": null, "air_temp_c": -2.0, "sky": "Overcast",
             "precipitation": "Snow 1cm/hr", "wind": "Moderate SW", "foot_pen_cm": null, "ski_pen_cm": null,
             "notes": null},
  "height_reference": "height_above_ground",
  "layers": [
    {"top_cm": 149, "bottom_cm": 139, "grain_form": "PP", "grain_form_2": null, "grain_symbol_as_seen": "+",
     "grain_size_mm": [0.5], "hardness": "F-", "hardness_bottom": null, "moisture": null,
     "density_kg_m3": null, "date_tag": null, "comment": null, "uncertain_fields": []}
  ],
  "temperatures": [{"height_cm": 149, "t_c": -4.0}],
  "tests": [{"raw": "CT13SP on ∨ 4.0-6.0 Jan 3", "type": "CT", "result": "CT13", "score": 13,
             "fracture_character": "SP", "height_cm": 114, "layer_date_tag": "Jan 3", "comment": null}],
  "confidence": "medium",
  "transcriber_notes": "boundaries read from axis +-2 cm"
}
```
