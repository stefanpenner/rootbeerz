// Writes bun/inputs_1.json: each byte that the Bun build reads, with its checksum.
//
//   bun pin_inputs.ts <bun source> <rust channel manifest> <rust-src library dir> <out.json>
//
// The tool imports the dep tables of the pinned Bun source, because those tables are TypeScript.
// Only macOS arm64 is pinned. No other host can run this build yet.
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const [source, rustManifest, stdLibrary, out] = process.argv.slice(2).map(p => resolve(p));
const TRIPLE = "aarch64-apple-darwin";
const LLVM = "23.1.1";
const CMAKE = "3.30.5";

async function sha256Of(url: string): Promise<string> {
  const response = await fetch(url, { redirect: "follow" });
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return createHash("sha256")
    .update(new Uint8Array(await response.arrayBuffer()))
    .digest("hex");
}

/** Reads one `[pkg.<name>.target.<target>]` table of the channel manifest. */
function rustArchive(manifest: string, name: string, target: string) {
  const start = manifest.indexOf(`[pkg.${name}.target.${target}]`);
  if (start < 0) throw new Error(`the channel manifest has no ${name} for ${target}`);
  const table = manifest.slice(start, manifest.indexOf("\n\n", start));
  const url = /^xz_url = "(.+)"$/m.exec(table)![1];
  return { name: `rust-${name}`, url, sha256: /^xz_hash = "(.+)"$/m.exec(table)![1] };
}

/** Reads the registry packages of one Cargo.lock. Each has a checksum there. */
function cratesOf(path: string) {
  const crates = [];
  for (const block of readFileSync(path, "utf8").split("[[package]]").slice(1)) {
    const checksum = /^checksum = "(.+)"$/m.exec(block)?.[1];
    const registry = /^source = "registry\+/m.test(block);
    if (/^source = "git\+/m.test(block)) throw new Error(`${path} holds a git crate`);
    if (!registry) continue;
    if (checksum === undefined) throw new Error(`${path} holds a registry crate with no checksum`);
    crates.push({
      name: /^name = "(.+)"$/m.exec(block)![1],
      version: /^version = "(.+)"$/m.exec(block)![1],
      sha256: checksum,
    });
  }
  return crates;
}

function matchesHost(value: string | string[] | undefined, host: string) {
  if (value === undefined || value === "any") return true;
  const list = Array.isArray(value) ? value : [value];
  const denied = list.filter(item => item.startsWith("!")).map(item => item.slice(1));
  if (denied.length > 0) return !denied.includes(host);
  return list.includes(host);
}

/** Reads the registry packages of one bun.lock. Each has a sha512 integrity there. */
function npmOf(path: string) {
  const lock = JSON.parse(readFileSync(path, "utf8").replace(/,(\s*[}\]])/g, "$1"));
  const packages = [];
  for (const entry of Object.values<any[]>(lock.packages)) {
    if (entry.length < 4 || typeof entry[3] !== "string" || !entry[3].startsWith("sha512-")) continue;
    if (!matchesHost(entry[2]?.os, "darwin") || !matchesHost(entry[2]?.cpu, "arm64")) continue;
    const at = entry[0].lastIndexOf("@");
    packages.push({ name: entry[0].slice(0, at), version: entry[0].slice(at + 1), integrity: entry[3] });
  }
  return packages;
}

function unique<T>(items: T[], key: (item: T) => string): T[] {
  const seen = new Map(items.map(item => [key(item), item]));
  return [...seen.keys()].sort().map(name => seen.get(name)!);
}

// A bare config: the source functions read only these fields on this host.
const cfg = {
  darwin: true,
  linux: false,
  windows: false,
  os: "darwin",
  arch: "aarch64",
  arm64: true,
  x64: false,
  abi: undefined,
  lto: false,
  asan: false,
  debug: false,
  release: true,
  baseline: false,
  webkit: "prebuilt",
  tinycc: true,
  host: { os: "darwin", arch: "aarch64" },
  cacheDir: "/nonexistent",
} as Record<string, unknown>;

const { allDeps } = await import(join(source, "scripts/build/deps/index.ts"));
const { WEBKIT_VERSION } = await import(join(source, "scripts/build/deps/webkit.ts"));
const { pins } = await import(join(source, "scripts/build/ci-images/spec.ts"));
cfg.webkitVersion = WEBKIT_VERSION;
cfg.nodejsVersion = pins.nodejs.version;

const manifest = readFileSync(rustManifest, "utf8");
const archives: { name: string; url: string; sha256: string }[] = [
  {
    name: "llvm",
    url: `https://github.com/llvm/llvm-project/releases/download/llvmorg-${LLVM}/LLVM-${LLVM}-macOS-ARM64.tar.xz`,
    sha256: "64220f1c99132ef7e580447781b84f96fbba6862a43a8f6522b423052cd67502",
  },
  {
    name: "cmake",
    url: `https://github.com/Kitware/CMake/releases/download/v${CMAKE}/cmake-${CMAKE}-macos-universal.tar.gz`,
    sha256: "3d603e507c7579b13518ef752b4ffcf3ed479fba80ee171d7d85da8153e869d0",
  },
  {
    name: "ninja",
    url: `https://github.com/oven-sh/ninja/releases/download/${pins.bunNinja.tag}/bun-ninja-darwin-aarch64.zip`,
    sha256: pins.bunNinja.sha256["darwin-aarch64"],
  },
  rustArchive(manifest, "rustc", TRIPLE),
  rustArchive(manifest, "cargo", TRIPLE),
  rustArchive(manifest, "rust-std", TRIPLE),
  rustArchive(manifest, "rust-src", '"*"'),
];

const deps: { name: string; url: string; identity: string; dest: string; sha256: string }[] = [];
for (const dep of allDeps) {
  if (dep.enabled !== undefined && typeof dep.enabled === "function" && !dep.enabled(cfg)) continue;
  const src = dep.source(cfg);
  if (src.kind === "github-archive") {
    const url = `https://github.com/${src.repo}/archive/${src.commit}.tar.gz`;
    deps.push({ name: dep.name, url, identity: src.commit, dest: "", sha256: await sha256Of(url) });
  } else if (src.kind === "prebuilt") {
    deps.push({ name: dep.name, url: src.url, identity: src.identity, dest: src.destDir ?? "", sha256: await sha256Of(src.url) });
  } else {
    console.error(`skip ${dep.name}: source kind ${src.kind}`);
  }
}

// These are the three calls of emitPackageInstall in scripts/build/codegen.ts.
const installDirs = [".", "packages/bun-error", "src/node-fallbacks"];

const inputs = {
  bun_ref: "bun-v1.4.4",
  host: TRIPLE,
  archives,
  deps: unique(deps, dep => dep.url),
  crates: unique(
    [...cratesOf(join(source, "Cargo.lock")), ...cratesOf(join(stdLibrary, "Cargo.lock"))],
    crate => `${crate.name}-${crate.version}`,
  ),
  npm: unique(
    installDirs.flatMap(dir => npmOf(join(source, dir, "bun.lock"))),
    pkg => `${pkg.name}@${pkg.version}`,
  ),
  install_dirs: installDirs,
};
writeFileSync(out, JSON.stringify(inputs, null, 1) + "\n");
console.log(`archives ${archives.length} deps ${inputs.deps.length} crates ${inputs.crates.length} npm ${inputs.npm.length}`);
console.log(inputs.install_dirs.join(" "));
