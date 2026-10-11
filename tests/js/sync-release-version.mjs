import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const packagePath = path.join(projectRoot, "downloader_app", "__init__.py");
const installerPath = path.join(projectRoot, "installer", "Downetic.iss");

async function main() {
  const packageSource = await readFile(packagePath, "utf8");
  const installerSource = await readFile(installerPath, "utf8");
  const versionMatches = [...packageSource.matchAll(/^\s*__version__\s*=\s*["']([^"']+)["']/gm)];
  const installerVersionMatches = [...installerSource.matchAll(/^#define AppVersion "([^"]+)"/gm)];

  if (versionMatches.length !== 1) {
    throw new Error(`Expected exactly one application version, found ${versionMatches.length}.`);
  }
  if (installerVersionMatches.length !== 1) {
    throw new Error(
      `Expected exactly one installer AppVersion, found ${installerVersionMatches.length}.`,
    );
  }

  const version = versionMatches[0][1];
  if (!/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/.test(version)) {
    throw new Error(`Application version is not a valid release version: ${version}.`);
  }

  const currentVersion = installerVersionMatches[0][1];
  if (currentVersion === version) {
    console.log(`Installer version is already synchronized: ${version}.`);
    return;
  }

  const updatedInstaller = installerSource.replace(
    /^#define AppVersion "[^"]+"/m,
    `#define AppVersion "${version}"`,
  );
  await writeFile(installerPath, updatedInstaller, "utf8");
  console.log(`Updated installer version: ${currentVersion} -> ${version}.`);
}

main().catch((error) => {
  console.error(`Release version synchronization failed: ${error.message}`);
  process.exitCode = 1;
});
