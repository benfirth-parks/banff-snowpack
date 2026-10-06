#!/usr/bin/env bash
# Reproducible native build of the pinned SNOWPACK + MeteoIO engine (Linux and macOS, Apple silicon or Intel).
# Pinned source: github.com/snowpack-model/snowpack @ b324cbdd1b7f4da4db8ff8a5b408274e88ac1945
#   (mirror of gitlabext.wsl.ch; MeteoIO 337bfbc3, SNOWPACK 364eb947, merged 2026-06-01)
# Requires: git, cmake >= 3.16, a C++17 compiler, make, perl (macOS: `xcode-select --install`, `brew install cmake`).
# About 5 min on 4 cores; about 0.6 GB for the source and build trees.
# Default prefix: Linux /opt/snowpack (needs write access), macOS $HOME/.local/snowpack (no sudo). snowagent finds
# both without SNOWPACK_BIN; for any other PREFIX: export SNOWPACK_BIN="$PREFIX/bin/snowpack".
set -euo pipefail
COMMIT=${SNOWPACK_COMMIT:-b324cbdd1b7f4da4db8ff8a5b408274e88ac1945}
if [ "$(uname -s)" = Darwin ]; then
  SRC=${SNOWPACK_SRC:-$HOME/src/snowpack-model}
  PREFIX=${PREFIX:-$HOME/.local/snowpack}
else
  SRC=${SNOWPACK_SRC:-/opt/snowpack-src/snowpack-model}
  PREFIX=${PREFIX:-/opt/snowpack}
fi
JOBS=${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}
for tool in git cmake make perl; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "error: $tool not found (macOS: xcode-select --install; brew install cmake; no brew? see docs/lab/run_locally.md section 0)" >&2
    exit 1
  fi
done
if [ ! -d "$SRC/.git" ]; then
  mkdir -p "$(dirname "$SRC")"
  git clone --filter=blob:none https://github.com/snowpack-model/snowpack.git "$SRC"
fi
git -C "$SRC" fetch --depth 1 origin "$COMMIT" 2>/dev/null || true
git -C "$SRC" checkout -q -f "$COMMIT"   # -f also drops the layout edit below left by an earlier build
# Upstream's macOS branch installs the binary and libraries into an app-bundle directory beside the prefix
# (EXE_DEST / LIB_DEST "../MacOS"), where neither the SNOWPACK build (it looks for MeteoIO under $PREFIX/lib) nor
# snowagent finds them. Install into bin/ and lib/ as on Linux. The edit touches only the APPLE branch.
for f in "$SRC/Source/meteoio/CMakeLists.txt" "$SRC/Source/snowpack/CMakeLists.txt"; do
  perl -pi -e 's{SET\(EXE_DEST "\.\./MacOS"\)}{SET(EXE_DEST bin)}; s{SET\(LIB_DEST "\.\./MacOS"\)}{SET(LIB_DEST lib)}' "$f"
done
mkdir -p "$PREFIX"
for pkg in meteoio snowpack; do
  B="$SRC/Source/$pkg/build"; mkdir -p "$B"; cd "$B"
  # upstream tests OFF: tests/albedo/albedoTest.cc does not compile at this commit (upstream bug)
  cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_PREFIX_PATH="$PREFIX" \
        -DMETEOIO_ROOT="$PREFIX" -DBUILD_TESTING=OFF -DCMAKE_INSTALL_RPATH="$PREFIX/lib"
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
echo "Installed $BIN (export SNOWPACK_BIN=$BIN if you chose another PREFIX)."
