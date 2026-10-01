# GHCN-Daily station records near the study plots (NOAA, raw, unchanged apart from gzip)

Source: s3://noaa-ghcn-pds/csv/by_station/<ID>.csv (AWS Open Data mirror of NCEI GHCN-Daily); sha256 of the
uncompressed files in manifest.json. GHCN-D units: TMAX/TMIN tenths of deg C, PRCP tenths of mm,
SNOW (new snow) mm, SNWD (snow depth) mm, WESD tenths of mm. Times are station observation days (local).

Most useful for the study plots (years with data, from ghcnd-inventory):
- Bow Summit (PC) CA00305A002, 2012 m: TMAX/PRCP/SNWD 1999-2007; (AE) Bow Summit CA003050PPF 1998-2007
- Sunshine CS CA003056267, 2187 m: 1997-2007; Goat's Eye CA003052838: temperature 1998-2007
- Lookout CA00305A003, 2621 m: 1998-2007; False Nicholas CA003052603, 2926 m: 1998-2007
- Skoki CA003055976 1998-2005; Cuthead Lake CA003051R51 1998-2007; Temple CA003056LPA 2000-2007
- Yoho NP O'Hara Lake CA00117R00H, 2045 m: 1987-2021; Wildcat Cr. Mistaya Lodge CA001178931: 1990-2003
- Valley references: Banff CA003050520 1887-2025, Banff CS 1995-2024, Lake Louise 1915-2007, Yoho Park 1992-2026
