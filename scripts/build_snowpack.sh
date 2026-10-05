#!/usr/bin/env bash
# Reproducible native build of the pinned SNOWPACK + MeteoIO engine.
# Pinned source: github.com/snowpack-model/snowpack @ b324cbdd1b7f4da4db8ff8a5b408274e88ac1945
#   (mirror of gitlabext.wsl.ch; MeteoIO 337bfbc3, SNOWPACK 364eb947, merged 2026-06-01)
# Requires: git, cmake >= 3.16, a C++17 compiler, make.  ~5 min on 4 cores.
set -euo pipefail
COMMIT=${SNOWPACK_COMMIT:-b324cbdd1b7f4da4db8ff8a5b408274e88ac1945}
SRC=${SNOWPACK_SRC:-/opt/snowpack-src/snowpack-model}
PREFIX=${PREFIX:-/opt/snowpack}
JOBS=${JOBS:-4}
if [ ! -d "$SRC/.git" ]; then
  mkdir -p "$(dirname "$SRC")"
  git clone --filter=blob:none https://github.com/snowpack-model/snowpack.git "$SRC"
fi
git -C "$SRC" fetch --depth 1 origin "$COMMIT" 2>/dev/null || true
git -C "$SRC" checkout -q "$COMMIT"
for pkg in meteoio snowpack; do
  B="$SRC/Source/$pkg/build"; mkdir -p "$B"; cd "$B"
  # upstream tests OFF: tests/albedo/albedoTest.cc does not compile at this commit (upstream bug)
  cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_PREFIX_PATH="$PREFIX" \
        -DMETEOIO_ROOT="$PREFIX" -DBUILD_TESTING=OFF
  make -j"$JOBS"
  make install
done
BIN="$PREFIX/bin/snowpack"
# -v prints the version and the usage text, then exits 1 (upstream behaviour), so its status is not the check:
# the installed binary must run and report its version (a missing or unloadable binary prints none).
version=$("$BIN" -v 2>&1 || true)
if ! grep -q "Snowpack version" <<<"$version"; then
  echo "build failed: $BIN does not run or report its version:" >&2
  echo "$version" >&2
  exit 1
fi
sed -n '/Snowpack version/,/MeteoIO/p' <<<"$version"
echo "Installed. Export SNOWPACK_BIN=$BIN if not using the default prefix."
