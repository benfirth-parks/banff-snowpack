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
| `⊸`/needle | PPnd | |
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
