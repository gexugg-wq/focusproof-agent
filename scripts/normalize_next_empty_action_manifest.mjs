import {
  lstatSync,
  readFileSync,
  realpathSync,
  writeFileSync,
} from "node:fs";
import { isAbsolute, join, relative, resolve } from "node:path";

const PUBLIC_EMPTY_ACTION_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
const BUILD_MANIFEST_PATHS = [
  "app-build-manifest.json",
  "app-path-routes-manifest.json",
  "server/app-paths-manifest.json",
  "server/pages-manifest.json",
];

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

const isPlainObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

function readJson(path, errorMessage) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    fail(errorMessage);
  }
}

function normalizeEmptyActionManifest(path) {
  const document = readJson(path, "invalid server-reference manifest schema");
  const expectedKeys = ["edge", "encryptionKey", "node"];
  const actualKeys = isPlainObject(document) ? Object.keys(document).sort() : [];
  if (
    !isPlainObject(document) ||
    JSON.stringify(actualKeys) !== JSON.stringify(expectedKeys) ||
    !isPlainObject(document.node) ||
    !isPlainObject(document.edge) ||
    typeof document.encryptionKey !== "string"
  ) {
    fail("invalid server-reference manifest schema");
  }
  if (Object.keys(document.node).length !== 0 || Object.keys(document.edge).length !== 0) {
    fail("server-reference action maps must be empty");
  }
  writeFileSync(
    path,
    JSON.stringify({ node: {}, edge: {}, encryptionKey: PUBLIC_EMPTY_ACTION_KEY }),
    "utf8",
  );
}

function isStringRouteMap(value) {
  return (
    isPlainObject(value) &&
    Object.entries(value).every(
      ([route, target]) => route.startsWith("/") && typeof target === "string" && target.length > 0,
    )
  );
}

function validateBuildManifest(relativePath, document) {
  if (relativePath === "app-build-manifest.json") {
    const keys = isPlainObject(document) ? Object.keys(document) : [];
    if (
      keys.length !== 1 ||
      keys[0] !== "pages" ||
      !isPlainObject(document.pages) ||
      !Object.entries(document.pages).every(
        ([route, chunks]) =>
          route.startsWith("/") &&
          Array.isArray(chunks) &&
          chunks.every((chunk) => typeof chunk === "string" && chunk.length > 0),
      )
    ) {
      fail("invalid allowlisted Next manifest schema");
    }
    return;
  }
  if (!isStringRouteMap(document)) {
    fail("invalid allowlisted Next manifest schema");
  }
}

function sortRecursively(value) {
  if (Array.isArray(value)) {
    return value.map(sortRecursively);
  }
  if (!isPlainObject(value)) {
    return value;
  }
  return Object.fromEntries(
    Object.keys(value)
      .sort()
      .map((key) => [key, sortRecursively(value[key])]),
  );
}

function canonicalizeBuildManifests(rootArgument) {
  let root;
  try {
    if (!isAbsolute(rootArgument) || lstatSync(rootArgument).isSymbolicLink()) {
      fail("invalid Next build root path");
    }
    root = realpathSync(rootArgument);
    if (!lstatSync(root).isDirectory()) {
      fail("invalid Next build root path");
    }
  } catch {
    fail("invalid Next build root path");
  }

  const outputs = [];
  for (const relativePath of BUILD_MANIFEST_PATHS) {
    const path = resolve(join(root, relativePath));
    const pathFromRoot = relative(root, path);
    if (pathFromRoot.startsWith("..") || isAbsolute(pathFromRoot)) {
      fail("invalid allowlisted Next manifest path");
    }
    let stat;
    try {
      stat = lstatSync(path);
    } catch {
      fail("missing allowlisted Next manifest");
    }
    if (stat.isSymbolicLink() || !stat.isFile() || realpathSync(path) !== path) {
      fail("invalid allowlisted Next manifest path");
    }
    const document = readJson(path, "invalid allowlisted Next manifest schema");
    validateBuildManifest(relativePath, document);
    outputs.push([path, JSON.stringify(sortRecursively(document))]);
  }
  for (const [path, payload] of outputs) {
    writeFileSync(path, payload, "utf8");
  }
}

if (process.argv.length === 3) {
  normalizeEmptyActionManifest(process.argv[2]);
} else if (
  process.argv.length === 4 &&
  process.argv[2] === "--canonicalize-build-manifests"
) {
  canonicalizeBuildManifests(process.argv[3]);
} else {
  fail(
    "usage: normalize_next_empty_action_manifest.mjs <manifest> | " +
      "--canonicalize-build-manifests <absolute-.next-root>",
  );
}
