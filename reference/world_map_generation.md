# How `world_map.yaml` was generated

This document explains how the current `world_map.yaml` was made: the inputs and where they came
from, the methods, the calibration choices and their reasons, and the known limitations. For the
file format itself, see `world_map_schema.md`.

**Produced by:**
- `gen_map.py`: terrain, ice, sand, rivers, and the YAML and preview output;
- `etopo_blocks.py`: reduces the elevation data to a 5-arc-minute grid;
- `validate_map.py`: checks the result.

Everything uses the **Python standard library only**; nothing was installed. Windows' built-in .NET
System.Drawing was used once, to resize the satellite JPEG.

---

## 1. Goal

Build a Civilization-style map of the Earth with one tile per **0.5° × 0.5°** of latitude and
longitude (720 × 360 = 259,200 tiles). Each tile records:
- links to its eight neighbours;
- its **landform** as percentages of water, plains, hills and mountains;
- its **cover** (what is on its land and frozen water) as percentages of grass, forest, desert, sand,
  wetlands, tundra and ice;
- which rivers run through it.

---

## 2. Inputs

All downloads are in `data/`. `data/SOURCES.txt` records each URL and the download date
(2026-10-03).

| dataset | source URL | used for |
|---------|------------|----------|
| NASA Blue Marble: Next Generation, **July 2004**, 21600×10800 (500 m), land and shallow water, no relief shading | https://eoimages.gsfc.nasa.gov/images/imagerecords/74000/74092/world.200407.3x21600x10800.jpg | water, and cover colour classes (grass, forest, desert, tundra; snow) |
| NOAA ETOPO1 Global Relief, ice surface, grid-registered, raw 16-bit (1 arc-minute) | https://www.ngdc.noaa.gov/mgg/global/relief/ETOPO1/data/ice_surface/grid_registered/binary/etopo1_ice_g_i2.zip | plains / hills / mountains; coastal lowland for sand; tree line for alpine tundra |
| Natural Earth 1:10m Glaciated Areas | https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_glaciated_areas.geojson | ice cover on land (glaciers, ice caps, Greenland and Antarctic ice sheets) |
| Natural Earth 1:10m Antarctic Ice Shelves | https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_antarctic_ice_shelves_polys.geojson | ice cover on water (floating shelves) |
| NSIDC Sea Ice Index G02135 v4.0, Arctic extent, **September 2025** | https://noaadata.apps.nsidc.org/NOAA/G02135/north/monthly/shapefiles/shp_extent/09_Sep/extent_N_202509_polygon_v4.0.zip | ice cover (Arctic sea ice) |
| NSIDC Sea Ice Index G02135 v4.0, Antarctic extent, **February 2025** | https://noaadata.apps.nsidc.org/NOAA/G02135/south/monthly/shapefiles/shp_extent/02_Feb/extent_S_202502_polygon_v4.0.zip | ice cover (Antarctic sea ice) |
| Global Lakes and Wetlands Database v2 (GLWD v2.0, Lehner et al. 2025, CC BY 4.0), "combined classes" GeoTIFFs at 15″ | https://doi.org/10.6084/m9.figshare.28519994 (file https://ndownloader.figshare.com/files/54001814) | wetlands |
| Natural Earth 1:10m Rivers + Lake Centerlines | https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_rivers_lake_centerlines.geojson | rivers |
| `world_population.yaml` (already in the project) | — | country codes, used only to tell apart rivers that share a name |

Also downloaded, and used for `world_population.yaml` rather than this map: JRC GHSL GHS-POP R2023A
for 2020 at 30 arc-seconds (~1 km), from
https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/GHS_POP_E2020_GLOBE_R2023A_4326_30ss/V1-0/GHS_POP_E2020_GLOBE_R2023A_4326_30ss_V1_0.zip
(see `world_population_schema.md`).

### Why these choices
- **July image:** it has the least seasonal snow in the north, where most land is. White areas there
  are then permanent ice. (The previous March image covered Siberia and Canada in snow.) It has no
  relief shading, because relief now comes from real elevation data.
- **ETOPO1 ice surface:** it's a raw binary grid that the standard library can stream, and it gives
  the top of the ice sheets.
- **September sea ice:** September is the annual minimum, so ice still present then survives all
  year. That is the "solid ice" cap. 2025 is the most recent complete season.

### Derived files
- `data/bmng_200407_8640x4320.bmp`: the satellite image resized to 8640×4320 (2.5-arc-minute
  pixels, 24 per degree, **144 per 0.5° tile**), used for analysis. That's about 4× the detail of the
  2048×1024 image used before.
- `bmng_200407_8192x4096.jpg`: an 8192×4096 copy, named as `map.source_image` and the default globe
  texture. `bmng_200407_16384x8192.jpg` and `bmng_200407_4096x2048.jpg` are made the same way; the
  viewer picks the largest the graphics card supports. The full 21,600 px image is wider than any
  common graphics card's texture limit (16,384).
- `data/etopo1_5min.bin`: ETOPO1 reduced to 4320×2160 blocks of 5×5 arc-minutes (about 9 km),
  storing each block's minimum, maximum and mean elevation. It is built by `etopo_blocks.py`
  (about 35 s) and cached.

---

## 3. Grid and neighbours

Unchanged from the first version:
- The grid is defined once in `grid.py`, shared by every script and the server.
- Tiles are named after their south-west corner in tenths of a degree: `N395W1055` is 39.5°N–40.0°N,
  105.5°W–105.0°W.
- Neighbours are found by moving ±1 in latitude and/or longitude. Longitude wraps around; moving
  past a pole gives `null`.
- Each 2.5′ analysis pixel belongs to exactly one tile, 24 × 24 pixels per tile.

---

## 4. Terrain and cover

Each tile has **two independent sets of percentages**:

- **`terrain`, the landform** (`water`, `plains`, `hills`, `mountains`): shares of the whole tile,
  summing to 100. Mostly from elevation and outline data, not colour.
- **`cover`, what is on it** (`grass`, `forest`, `desert`, `sand`, `wetlands`, `tundra`, `ice`): shares
  of the tile's *covered surface*, meaning its land (plains + hills + mountains) plus any frozen water
  (sea ice, ice shelves), summing to 100. All zero for open sea.

Ice is cover, not a landform. An ice sheet or glacier keeps the landform of its ice surface: the
Greenland and Antarctic interiors are plains, the Transantarctic Mountains hills and mountains, and an
Alpine glacier mountains. Floating ice (sea ice and ice shelves) is water under ice cover.

The two are recorded separately because one doesn't determine the other. A tile can be forested hills,
grassy plains, wetland plains or desert mountains, and splitting the groups keeps both facts. A tile
doesn't say how cover is distributed across its landforms, so a game should assume it is spread evenly.

Every 2.5′ analysis pixel (144 per tile) gets one landform. Land pixels and frozen-water pixels get
100 units of cover (normally all to one type; see 4.6 for wetlands). Each tile's percentages are its
pixel totals, rounded so each group sums exactly to 100. Leftover points go to the largest fractional
parts. If a tile's land is too small to survive the rounding (it would show 0% plains, hills and
mountains), its cover keeps only its frozen water (`ice: 100`), or is all zeros if it has none.

### 4.1 Colour classes from the satellite image
Calibrated by sampling known places in the July image:

| place | RGB | place | RGB |
|-------|-----|-------|-----|
| open ocean | (2, 5, 20) | Sahara | (202, 169, 123) |
| shallow shelf (Bahamas) | (7, 44, 38) | Outback | (131, 70, 37) |
| lakes (Superior, Victoria) | (0, 2, 7) / (0, 7, 2) | Gobi | (124, 106, 74) |
| Amazon | (19, 33, 8) | US Great Plains | (91, 84, 51) |
| taiga (60°N 100°E) | (26, 43, 11) | Germany | (64, 68, 30) |
| Canadian tundra | (148, 146, 135) | Greenland | (252, 254, 253) |

Rules, checked in order (`Y` = 0.3R + 0.59G + 0.11B):

| rule | colour class |
|------|--------------|
| R ≤ 12 and G ≤ 60 and B ≤ 60 | water |
| min(R,G,B) > 170 and spread < 30 | snow (resolved in 4.6) |
| \|lat\| ≥ 62, grey (spread < 25) and Y > 80 | tundra |
| R ≥ 115 and R − B ≥ 45 | desert |
| G ≥ R, G > B, Y < 42 | forest |
| \|lat\| ≥ 62 | tundra |
| otherwise | grass |

Results are cached by colour, so the 37 million pixels take seconds.

### 4.2 Landform: plains, hills and mountains from elevation
Relief comes from **ETOPO1**, not image texture:

- **Relief** of a 5′ block = highest point − lowest point. Ocean depths count as **0 m (sea level)**,
  so a low coast never registers as relief, while a genuinely high coast (fjords, cliffs) still does.
- Thresholds were chosen from the median 5′ relief of reference tiles:

  | mountains | relief | hills | relief | flat | relief |
  |-----------|--------|-------|--------|------|--------|
  | Hindu Kush | 1386 m | Scotland | 341 m | Congo | 59 m |
  | Himalaya | 1265 m | Urals | 268 m | Ukraine | 53 m |
  | Alps | 1250 m | Brazilian Highlands | 266 m | US Great Plains | 39 m |
  | Andes | 1105 m | Tibetan Plateau | 239 m | Sahara | 28 m |
  | Ethiopian Highlands | 710 m | Appalachians | 200 m | Amazon | 12 m |
  | Rockies (Colorado) | 707 m | Massif Central | 186 m | India (Ganges plain) | 6 m |
  | Zagros | 657 m | Ozarks | 86 m | Netherlands | 6 m |

- **Mountains: relief ≥ 500 m. Hills: 100–499 m. Plains: under 100 m.** Each pixel uses the relief of
  the 5′ block it sits in.

### 4.3 Landform: water
- **Water** is the satellite image's water colour (oceans, seas and lakes), plus floating ice: Antarctic
  ice shelves, and sea ice where the image shows water (4.4).
- Everything else is land, graded into plains, hills or mountains by 4.2. This includes land under
  glaciers and ice sheets, whose relief is that of the ice surface (ETOPO1's "ice surface" version).

### 4.4 Cover: ice
- **Ice on land:** glaciers, ice caps and the Greenland and Antarctic ice sheets (Natural Earth's
  glaciated areas, filled onto the pixel grid with an even-odd scanline fill). Also white (snow-class)
  pixels in the northern hemisphere and from 60°S poleward: in July, that white is permanent snow or
  ice the outlines missed.
- **Floating ice (water under ice):** Antarctic ice shelves (Natural Earth), which take precedence over
  the glacier outlines where both apply. Also sea ice present at the summer minimum, i.e. all year:
  - **Arctic:** NSIDC September 2025.
  - **Antarctic:** NSIDC February 2025.
  - **How it's applied:** the NSIDC extent polygons are in polar stereographic metres (Hughes 1980
    ellipsoid, true scale at 70°; central meridian −45° in the north and 0° in the south). They're
    filled onto a 6.25 km grid in that projection, and every pixel north of 60°N or south of 55°S is
    projected and looked up.
  - **Water pixels only:** the 25 km-resolution outlines spill onto coasts, so sea ice is only applied
    to pixels that the image shows as water.
- Ice pixels get no wetlands.

### 4.5 Cover: grass, forest, desert, tundra and sand
For every other land pixel, the cover starts as its colour class (4.1), with three adjustments:
- **Sand:** beaches, dunes and low coastal flats. A plains pixel becomes sand when it touches a water
  pixel, its 5′ block reaches sea level (lowest point ≤ 0 m), the block's highest point is ≤ 30 m, it
  isn't forest, and it is below 60° latitude (polar coasts are tundra). Requiring sea level keeps
  highland lakeshores out.
- **Alpine tundra:** grass-class land whose 5′ block's mean elevation is above the tree line becomes
  tundra. The tree line is modelled as about 4,000 m up to 20° latitude, falling about 70 m per degree
  poleward (about 2,600 m at 40°, 1,900 m at 50°, 500 m at 70°). Without this, bare rock above the
  tree line came out as "grass" (Everest's tile was 76% grass).
- **Southern-hemisphere snow** north of 60°S (it's winter in the July image) on land is tundra.

### 4.6 Cover: wetlands from GLWD
Wetlands come from the **Global Lakes and Wetlands Database v2** (GLWD v2; Lehner et al. 2025, CC BY 4.0),
read by `glwd.py`:
- GLWD gives, for each 15″ cell (about 500 m), its main wetland class and the share of the cell that is
  wetland. A cell counts as wetland when its main class is a wetland where water stands:
  - lacustrine (8–9);
  - riverine, regularly or seasonally flooded (10–13);
  - palustrine, regularly flooded (16–17);
  - peatlands (22–27), mangroves (28), saltmarsh (29), large river deltas (30) and other coastal
    wetlands (31).

  Its wetland share is then its wetland percentage.
- Not counted:
  - open water (1–7), which the satellite image already shows as water;
  - "seasonally saturated" soils (14–15, 18–19), which in Europe are mostly farmland and towns.
    Including them made Paris 30% wetlands; without them it is 14%.
  - ephemeral wetlands (20–21), salt pans (32) and rice paddies (33).
- 10 × 10 GLWD cells make one 2.5′ pixel. Each pixel's wetland percentage is the mean of its 100 cells,
  cached in `data/glwd_wetlands_2p5min.bin` (built in about 2 minutes).
- A land pixel's wetland percentage goes to `wetlands` and the rest to its other cover. A pixel that is 40%
  wetland and otherwise forest adds 40 units of wetlands and 60 of forest.
- GLWD covers 84°N–56°S, so there are no wetlands south of 56°S.

Checks of the wetland grid (mean wetland % in a 0.5° box): Everglades 89%, Hudson Bay Lowlands 83%,
Sundarbans 66%, Pantanal 55%, Amazon floodplain 50%, Sudd 39%, Okavango Delta 26%, West Siberian bogs 20%;
Sahara 0%, Kansas 2%, Paris 14%.

### 4.7 Order of rules per pixel
1. **Ice shelf:** water, ice cover.
2. **Water colour** (and not glacier or permanent white): water, with ice cover if it's in the sea-ice
   extent, otherwise no cover.
3. **Land:** mountains (relief ≥ 500 m), hills (≥ 100 m), otherwise plains.
4. **Cover** for land:
   - glacier outline or permanent white gives ice;
   - otherwise southern winter snow gives tundra;
   - otherwise sand (coastal plains below 60°), alpine tundra (above the tree line), or the colour
     class;
   - then the GLWD wetland share is moved to wetlands.

## 5. Rivers

- **Source:** Natural Earth 1:10m river centrelines, features of class `River` only. Lake centrelines
  are skipped because those tiles are lake. The 71 unnamed pieces are skipped because they can't be
  listed by name.
- **Grouping:**
  - Pieces are grouped by name, after collapsing doubled spaces (`"Syr  Darya"` → `"Syr Darya"`).
  - Within a name, pieces are merged when their end points are within 0.5°. This also bridges
    gaps where a river passes through a lake.
  - Unconnected rivers with the same name stay separate and get the country code that most of their
    tiles fall in, from `world_population.yaml`: `"Colorado (USA)"` / `"Colorado (ARG)"`,
    `"Mackenzie (CAN)"` / `"Mackenzie (AUS)"`.
- **Tiles:** each Natural Earth line is sampled every 0.01° and becomes one `segment`, an ordered
  chain of touching tiles. The river's `tiles` is the set of all its segments' tiles, without
  duplicates.
- **All Natural Earth rivers:** 1,110 rivers flag 13,824 tiles. That was too dense for a game map,
  so the map now keeps only navigable rivers (next point). Setting `NAVIGABLE_ONLY = False` in
  `gen_map.py` brings all of them back.
- **Navigable rivers only (current):**
  - The list comes from Wikipedia's
    [List of waterways](https://en.wikipedia.org/wiki/List_of_waterways), fetched 2026-10-04 (raw
    wikitext saved as `data/list_of_waterways.wikitext`).
  - Its lakes, canals, straits, bays, seas and intracoastal waterways are left out, because the map
    only has rivers. "Saint Lawrence Seaway" is taken as the St. Lawrence River and "Mississippi River
    System" as the Mississippi; the Ohio, Missouri, Tennessee and Monongahela are listed separately.
  - Each listed river was matched by hand to Natural Earth's names, checking its location, and
    merged into one river under the list's English name (table `NAVIGABLE` in `gen_map.py`). For
    example, the Danube is "Danube" + "Donau"; the Nile is "Nile" plus its upper course through
    Sudan, South Sudan and Uganda ("El Bahr el Abyad", "Bahr el Jebel", "Albert Nile", "Victoria
    Nile"); the Congo includes its upper course, the "Lualaba".
  - Natural Earth names that matched but were the wrong river were rejected: its "St. Croix" is in
    Wisconsin (the list means the Maine–New Brunswick border river), its "St. Marys" is in Nova
    Scotia, and one "Drau" is in Iceland.
  - Listed rivers Natural Earth doesn't have are left out: St. Croix (Canada–US), St. Marys
    (Michigan–Ontario), Detroit, Valdivia, Bueno, Karun, Paraguay, Bega and the Humber.
  - **Result: 51 rivers on 1,806 tiles.**
- **Naming:** Natural Earth uses local spellings and splits some rivers (`"Amazonas"`, `"Huang"` for
  the Yellow River, `"Dnipro"`, both `"Yangtze"` and `"Chang Jiang"`). The navigable rivers use the
  list's English names and record the Natural Earth names in `natural_earth_names`.
- **Partly drawn rivers:** Natural Earth's St. Lawrence covers only the upper river near Lake Ontario
  (5 tiles); below that it widens into water on the satellite image.

---

## 6. Results and checks

`validate_map.py` confirms every guarantee in `world_map_schema.md` §6, with **0 errors**: tile ids,
order, terrain sums, two-way neighbour links, river flags, and continuous river segments.

Reference tiles in the current map (0.5° tiles; percentages, largest first):

| place | tile | terrain (landform) | cover |
|-------|------|--------------------|-------|
| Everest | N275E0865 | mountains 100 | ice 44, tundra 30, grass 13, forest 13 |
| Swiss Alps | N460E0090 | mountains 99, water 1 | grass 41, forest 41, tundra 13, wetlands 4, ice 1 |
| Colorado Front Range | N395W1055 | hills 44, mountains 36, plains 20 | grass 76, forest 12, tundra 12 |
| Tibetan Plateau | N330E0880 | hills 81, mountains 14, plains 5 | desert 94, tundra 6 |
| Sahara | N230E0100 | plains 92, hills 8 | desert 100 |
| Amazon floodplain | S035W0605 | plains 94, water 6 | wetlands 58, forest 27, grass 15 |
| Everglades | N255W0810 | plains 100 | wetlands 97, forest 3 |
| Sundarbans | N215E0890 | plains 100 | grass 54, wetlands 39, forest 7 |
| Sudd | N075E0305 | plains 100 | wetlands 52, grass 36, forest 12 |
| Hudson Bay Lowlands | N540W0850 | plains 100 | wetlands 72, forest 21, grass 7 |
| West Siberia | N600E0750 | plains 100 | forest 81, wetlands 19 |
| Nile Delta / Cairo | N300E0310 | plains 75, hills 25 | grass 62, desert 25, wetlands 13 |
| Iceland | N640W0190 | hills 61, plains 39 | tundra 81, forest 13, wetlands 6 |
| Svalbard | N790E0150 | mountains 83, hills 11, water 3, plains 3 | tundra 58, ice 35, forest 5, wetlands 2 |
| Greenland ice sheet | N725W0400 | plains 100 | ice 100 |
| Antarctic plateau | S800E0000 | plains 100 | ice 100 |
| Transantarctic Mountains | S850E1700 | hills 75, mountains 14, plains 11 | ice 100 |
| Ross Ice Shelf | S800W1750 | water 100 | ice 100 |
| Weddell Sea (sea ice) | S760W0550 | water 100 | ice 100 |
| Arctic Ocean (sea ice) | N860E0000 | water 100 | ice 100 |

Across the map, some ice cover appears in 52,464 tiles, and some wetlands in 49,872 tiles (usually a few percent).

The table below is historical. It compares the first, texture-based version with the first
elevation-based version, when terrain was still one combined set of nine types.

Reference tiles, old (texture-based, March image) → new. These comparisons were made on the 1° grid,
so they use 1° tile ids; section 6.1 shows the 0.5° grid reproduces them.

| place | tile | old | new |
|-------|------|-----|-----|
| Himalaya | N27E086 | mountains 100 | mountains 84, ice 15, hills 1 |
| Zagros | N33E048 | plains 93, desert 7 | mountains 70, hills 28, desert 2 |
| Appalachians | N37W082 | plains 100 | hills 95, forests 2, mountains 2, plains 1 |
| Ethiopian Highlands | N10E038 | plains 100 | mountains 76, hills 24 |
| Tibetan Plateau | N33E088 | desert 96, plains 4 | hills 80, mountains 9, desert 8, plains 2, ice 1 |
| Cairo / Nile Delta | N30E031 | hills 77, mountains 23 | plains 68, desert 21, hills 11 |
| Bangladesh | N22E090 | hills 64, mountains 31, water 5 | plains 98, forests 2 |
| Florida | N27W081 | hills 60, mountains 20, water 20 | plains 58, water 27, forests 13, sand 2 |
| Netherlands | N52E004 | hills 57, water 30, plains 13 | water 48, plains 44, forests 5, sand 3 |
| Greenland | N72W040 | tundra 100 | ice 100 |
| Antarctica | S80E000 | tundra 100 | ice 100 |
| Arctic Ocean | N86E000 | water 100 | ice 100 |
| West Siberia | N62E075 | tundra 100 (snow) | forests 80, tundra 20 |

Across the 585 coastal tiles that the old map had at ≥ 30% hills, hills fell from an average of
**51% to 22%**. What remains is real relief, such as the Norwegian, Chilean and Japanese coasts.

On the 0.5° grid, 50,156 tiles contain some ice and 8,492 contain some sand.

### 6.1 The 0.5° grid
The current files use 0.5° tiles; the 1° version is kept in `save5/`.

- **Same inputs and rules.** Each 0.5° tile is simply a 12 × 12 block of the same 2.5′ pixels a 1°
  tile used as 24 × 24, so the four 0.5° tiles inside a 1° tile average back to its old values. That
  was checked for Himalaya, Zagros, Netherlands, Amazon, Greenland and Cairo tiles: they differ by at
  most 0.5 percentage points, which is rounding.
- **Rivers** are re-sampled onto the finer grid. With every Natural Earth river they covered 13,824
  tiles (about 17% of land tiles, against about a third at 1°); the navigable-only set covers 1,806.
- **Population** (`world_population.yaml`) was regridded from the ~1 km GHSL 2020 data, not split
  from the old 1° numbers. See `world_population_schema.md`.
- `validate_map.py` also checks that `world_population.yaml` uses the same tiles, order and
  neighbours as `world_map.yaml`.

---

## 7. Known limitations
- **Cover colours come from one month's image.** Dark lava fields can read as forest (Iceland's tile
  N640W0190 shows 13% forest), and southern-hemisphere winter snow makes Andes and New Zealand land
  tundra rather than its summer cover.
- **Cover is per tile, not per landform.** A tile says, for example, "40% hills, 60% plains" and
  "70% forest, 30% grass", but not which landform the forest is on.
- **Wetlands follow GLWD's wetland map,** including drained peatlands: the Dutch peat polders come out
  about half wetlands. GLWD stops at 56°S, so there are no wetlands further south. Small amounts of wetlands
  (a few percent) appear in about 50,000 tiles, mostly along rivers.
- **The tree line is a simple latitude formula,** so alpine tundra boundaries are approximate.
- **Relief is measured within ~9 km blocks.** A high but flat plateau with little local relief
  counts as plains or hills, not mountains.
- **Sand is a thin strip,** about one 4.6 km pixel along low coasts. It is typically 1–3% of a
  coastal tile.
- **Sea ice is one season's minimum** (Arctic September 2025, Antarctic February 2025). It changes
  from year to year.
- **Only listed navigable rivers are drawn** (51 rivers, 1,806 tiles). The Wikipedia list is marked
  incomplete, so some major navigable rivers aren't on it: the Irrawaddy and Orinoco are absent, and
  the Volga appears only through the "Volga–Baltic Waterway" canal route, so the river itself isn't
  included. Listed rivers missing from Natural Earth's 1:10m layer can't be drawn. Add rows to
  `NAVIGABLE` in `gen_map.py` to include more.
- **At 0.5°, each tile has 144 sample pixels,** so a landform percentage moves in steps of about 0.7
  points. Relief is still measured over ~9 km blocks, about a sixth of a tile's width.

---

## 8. Reproducing

1. Download the datasets in section 2 into `data/` (URLs also in `data/SOURCES.txt`).
2. Make the resized images with PowerShell (.NET System.Drawing):
   ```powershell
   Add-Type -AssemblyName System.Drawing
   $src = [System.Drawing.Image]::FromFile("$PWD\data\world.200407.3x21600x10800.jpg")
   foreach ($s in @(@(8640,4320,'data\bmng_200407_8640x4320.bmp','Bmp'), @(8192,4096,'bmng_200407_8192x4096.jpg','Jpeg'))) {
     $b = New-Object System.Drawing.Bitmap($s[0], $s[1], [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
     $g = [System.Drawing.Graphics]::FromImage($b)
     $g.InterpolationMode = 'HighQualityBilinear'; $g.PixelOffsetMode = 'HighQuality'
     $a = New-Object System.Drawing.Imaging.ImageAttributes; $a.SetWrapMode('TileFlipXY')   # no dark edge pixels
     $g.DrawImage($src, (New-Object System.Drawing.Rectangle(0, 0, $s[0], $s[1])), 0, 0, $src.Width, $src.Height, 'Pixel', $a)
     $g.Dispose()
     $b.Save("$PWD\$($s[2])", [System.Drawing.Imaging.ImageFormat]::$($s[3])); $b.Dispose() }
   ```
   The textures used in this build were saved at JPEG quality 90. The 16384×8192 and 4096×2048
   copies are made with the same code (add their sizes to the list, save as `Jpeg`).
3. Run `python gen_population.py` and then `python convert_population.py` (about 25 s + 60 s). The
   map uses the population file's country codes to label rivers that share a name.
4. Run `python gen_map.py`. It takes about 30–45 s. The first time, it also builds
   `data/etopo1_5min.bin` (about 35 s) and `data/glwd_wetlands_2p5min.bin` (about 2 minutes; this extracts
   `GLWD_v2_0_main_class.tif` and `GLWD_v2_0_area_pct.tif` from the GLWD zip).
5. Run `python validate_map.py` to check the result.
6. Run `python convert_map.py` to write `world_map.json`, the copy the server reads (about 90 s,
   mostly reading the YAML).
7. Restart `server.py` (it starts in under a second).

Earlier versions are backed up:
- `save5/`: the same data on the 1° grid (map, population, server, page and docs).
- `save4/`: the first version (March image, texture-based relief, rivers traced from the LizardPoint
  GIF), including its generator and docs.
